#!/usr/bin/env python3
"""APIA off-model ingestion engine (Stage 1 of the ingest -> clarify -> modify ->
test -> apply loop).

This script performs the *mechanical*, deterministic preprocessing that builds
the cached project knowledge base under ``.apia/knowledge/``. It walks a target
repository, detects the stack(s) from manifests and file extensions, discovers
tests, parses dependency manifests, and emits compact Markdown artifacts the
model later summarizes and consumes on demand. Doing this heavy lifting here --
off-model -- is the single biggest token/credit saver (see
docs/ARCHITECTURE.md sections 3 and 5).

HARD CONSTRAINT (confirmed with the user): this script performs ZERO network and
ZERO LLM calls. It is PURE STATIC ANALYSIS. It must never import ``requests``,
``urllib``-for-network, ``openai``, ``anthropic`` or any other LLM/network SDK,
and must never reach a remote endpoint. Model inference always runs on the
user's existing Copilot session; this script only distills facts for that model
to read. The dependency surface is intentionally minimal: Python standard
library plus optional PyYAML (used only to read ``.apia/config.yml``). Anything
beyond that is out of scope here.

Usage:
    python3 .apia/scripts/ingest.py <repo_path> [--full] [--knowledge-dir DIR]

Artifacts emitted into the knowledge directory:
    project-map.md     tree + per-module file inventory
    architecture.md    top-level components / directory responsibilities
    dependencies.md    declared dependencies parsed from manifests
    pipelines.md       CI/CD and build pipeline files detected
    workflows.md       entrypoints / scripts that hint at business flows
    test-inventory.md  discovered test files and test functions
    stack.md           detected languages / frameworks
    .index.json        incremental cache: per-artifact source paths + hashes
"""

# NOTE: the imports below are standard-library only (plus the optional PyYAML
# import guarded further down). There is deliberately NO networking/LLM import
# here -- see the module docstring's HARD CONSTRAINT.
import argparse
import ast
import fnmatch
import hashlib
import json
import os
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Stack detection rules
# ---------------------------------------------------------------------------
# Each stack is detected by (a) manifest filenames and (b) source file
# extensions. Manifest matches are strong signals; extensions are supporting
# evidence. Rules are intentionally declarative so Phase-2 can extend them.
STACK_RULES = {
    "python": {
        "manifests": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"],
        "manifest_globs": ["requirements*.txt"],
        "extensions": [".py"],
        "frameworks": {
            "pytest": ["pytest", "[tool.pytest"],
            "unittest": ["import unittest"],
        },
    },
    "cpp": {
        "manifests": ["CMakeLists.txt", "conanfile.txt", "conanfile.py", "Makefile"],
        "manifest_globs": [],
        "extensions": [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h"],
        "frameworks": {},
    },
    "csharp": {
        "manifests": [],
        "manifest_globs": ["*.csproj", "*.sln"],
        "extensions": [".cs"],
        "frameworks": {},
    },
    "groovy": {
        "manifests": ["build.gradle", "settings.gradle", "Jenkinsfile"],
        "manifest_globs": ["*.gradle", "*.groovy"],
        "extensions": [".groovy", ".gradle"],
        "frameworks": {},
    },
}

# Files/dirs that signal CI/CD or build pipelines.
PIPELINE_PATTERNS = [
    ".github/workflows",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    ".circleci",
    ".travis.yml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "CMakeLists.txt",
]

# Directories that are never useful to walk and should be skipped entirely.
DEFAULT_IGNORES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".idea",
    ".vs",
    "build",
    "dist",
    ".apia",  # never ingest the knowledge base itself
}

ARTIFACTS = [
    "project-map.md",
    "architecture.md",
    "dependencies.md",
    "pipelines.md",
    "workflows.md",
    "test-inventory.md",
    "stack.md",
]

INDEX_FILE = ".index.json"


# ---------------------------------------------------------------------------
# .gitignore handling (minimal, dependency-free)
# ---------------------------------------------------------------------------
def load_gitignore_patterns(repo_path):
    """Read simple glob patterns from a top-level .gitignore, if present.

    This is a pragmatic subset of gitignore semantics (comments, blank lines,
    plain globs, directory suffixes). It is sufficient for pruning the walk and
    keeps us dependency-free.
    """
    patterns = []
    gi = os.path.join(repo_path, ".gitignore")
    if os.path.isfile(gi):
        with open(gi, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("!"):
                    # Negation is not modelled; skip to stay conservative.
                    continue
                patterns.append(line.rstrip("/"))
    return patterns


def is_ignored(rel_path, name, gitignore_patterns):
    """Return True if a path should be skipped (default ignores + .gitignore)."""
    if name in DEFAULT_IGNORES:
        return True
    for pat in gitignore_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_path, pat):
            return True
        # Match a directory component anywhere in the path.
        if pat in rel_path.split(os.sep):
            return True
    return False


# ---------------------------------------------------------------------------
# Repo walk
# ---------------------------------------------------------------------------
def walk_repo(repo_path):
    """Walk the repository, returning a sorted list of repo-relative file paths.

    Respects DEFAULT_IGNORES and a minimal .gitignore. Pure filesystem walk; no
    network access.
    """
    gitignore_patterns = load_gitignore_patterns(repo_path)
    files = []
    for root, dirs, filenames in os.walk(repo_path):
        rel_root = os.path.relpath(root, repo_path)
        if rel_root == ".":
            rel_root = ""
        # Prune ignored directories in-place so os.walk does not descend.
        kept = []
        for d in dirs:
            rel = os.path.join(rel_root, d) if rel_root else d
            if not is_ignored(rel, d, gitignore_patterns):
                kept.append(d)
        dirs[:] = sorted(kept)
        for fn in sorted(filenames):
            rel = os.path.join(rel_root, fn) if rel_root else fn
            if is_ignored(rel, fn, gitignore_patterns):
                continue
            files.append(rel)
    return sorted(files)


def file_hash(path):
    """Return a short sha256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def detect_stacks(repo_path, files):
    """Detect stacks from manifests and extensions.

    Returns a dict: stack name -> {matched manifests, extension counts,
    frameworks}. Only stacks with evidence are included.
    """
    basenames = {os.path.basename(f): f for f in files}
    ext_counts = {}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

    detected = {}
    for stack, rules in STACK_RULES.items():
        manifests = []
        for m in rules["manifests"]:
            if m in basenames:
                manifests.append(basenames[m])
        for glob in rules["manifest_globs"]:
            for f in files:
                if fnmatch.fnmatch(os.path.basename(f), glob):
                    manifests.append(f)
        exts = {e: ext_counts[e] for e in rules["extensions"] if e in ext_counts}
        if not manifests and not exts:
            continue
        frameworks = detect_frameworks(repo_path, files, manifests, rules["frameworks"])
        detected[stack] = {
            "manifests": sorted(set(manifests)),
            "extensions": exts,
            "source_count": sum(exts.values()),
            "frameworks": sorted(frameworks),
        }
    return detected


def detect_frameworks(repo_path, files, manifests, framework_rules):
    """Scan manifests and a bounded set of source files for framework markers."""
    found = set()
    scan_targets = list(manifests)
    # Also scan test files, where framework imports usually live.
    for f in files:
        base = os.path.basename(f)
        if base.startswith("test_") or base.endswith("_test.py") or "tests" in f.split(os.sep):
            scan_targets.append(f)
    for rel in scan_targets:
        full = os.path.join(repo_path, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (OSError, UnicodeError):
            continue
        for name, markers in framework_rules.items():
            if any(marker in text for marker in markers):
                found.add(name)
    return found


def discover_tests(repo_path, files):
    """Discover Python test files and their test functions via AST (no exec).

    Returns a list of dicts: {path, functions:[names]}. Only static parsing is
    performed; test code is never imported or executed.
    """
    tests = []
    for rel in files:
        base = os.path.basename(rel)
        looks_like_test = base.startswith("test_") or base.endswith("_test.py")
        if not rel.endswith(".py"):
            continue
        # A file qualifies as a test target when its name follows the
        # conventional pattern, or when it is conftest.py (shared fixtures live
        # there). Non-test helpers inside a tests/ dir are skipped to avoid
        # false positives.
        if not (looks_like_test or base == "conftest.py"):
            continue
        full = os.path.join(repo_path, rel)
        funcs = []
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                    funcs.append(node.name)
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test"):
                            funcs.append("{}.{}".format(node.name, sub.name))
        except (OSError, SyntaxError, ValueError):
            funcs = []
        tests.append({"path": rel, "functions": sorted(funcs)})
    return sorted(tests, key=lambda t: t["path"])


def parse_dependencies(repo_path, files):
    """Parse declared dependencies from known manifests.

    Returns a list of dicts: {manifest, deps:[...]} with best-effort, static
    parsing. No package index is contacted.
    """
    results = []
    basenames = {os.path.basename(f): f for f in files}

    # requirements*.txt
    for f in files:
        base = os.path.basename(f)
        if fnmatch.fnmatch(base, "requirements*.txt"):
            deps = _parse_requirements(os.path.join(repo_path, f))
            results.append({"manifest": f, "deps": deps})

    # pyproject.toml
    if "pyproject.toml" in basenames:
        f = basenames["pyproject.toml"]
        deps = _parse_pyproject(os.path.join(repo_path, f))
        results.append({"manifest": f, "deps": deps})

    return sorted(results, key=lambda r: r["manifest"])


def _parse_requirements(path):
    deps = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                deps.append(line)
    except OSError:
        pass
    return sorted(deps)


def _parse_pyproject(path):
    """Extract dependency strings from a pyproject.toml without a TOML parser.

    Python 3.9 has no stdlib tomllib, and we keep the dependency surface to
    stdlib + optional PyYAML. We therefore do a small, well-scoped scan of the
    ``[project] dependencies`` and ``[project.optional-dependencies]`` arrays.
    """
    deps = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return deps

    # Keys that are arrays but are NOT dependency lists; never collect them even
    # when they appear as `key = [...]` tables/entries.
    _NON_DEP_KEYS = ("name", "version", "description", "requires-python", "readme",
                     "pythonpath", "testpaths", "classifiers", "keywords",
                     "authors", "maintainers")

    def _is_optional_dep_table(section):
        # The [project.optional-dependencies] table maps extra -> array, so every
        # key under it is a dependency extra regardless of its name.
        return section == "project.optional-dependencies"

    section = None          # current [table] header, if any
    collecting = False      # inside a multi-line array we care about
    current_key = None      # the key whose array we are collecting
    current_extra = None    # extra label when the array is an optional-dependency

    for line in lines:
        stripped = line.strip()

        # Track the active table header so we can treat every key under
        # [project.optional-dependencies] as an extra, including multi-line forms.
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            section = stripped.strip("[]").strip()
            collecting = False
            current_key = None
            current_extra = None
            continue

        if collecting:
            # Continuation lines of a multi-line array we opened below.
            for token in _extract_quoted(line):
                if current_extra is not None:
                    deps.append("{} (extra: {})".format(token, current_extra))
                else:
                    deps.append(token)
            if "]" in line:
                collecting = False
                current_key = None
                current_extra = None
            continue

        # A new `key = [` assignment. Decide whether it is a dependency array.
        if "=" in stripped and "[" in stripped:
            key = stripped.split("=", 1)[0].strip().strip('"')
            if not key or key.startswith("#") or key in _NON_DEP_KEYS:
                continue

            is_core_deps = key == "dependencies"
            is_optional_extra = _is_optional_dep_table(section)
            if not (is_core_deps or is_optional_extra):
                continue

            extra_label = key if is_optional_extra else None
            after_bracket = stripped.split("[", 1)[1]
            tokens = _extract_quoted(after_bracket)
            for token in tokens:
                if extra_label is not None:
                    deps.append("{} (extra: {})".format(token, extra_label))
                else:
                    deps.append(token)
            if "]" not in after_bracket:
                # Array continues on following lines (multi-line form).
                collecting = True
                current_key = key
                current_extra = extra_label

    # Deduplicate while keeping deterministic order.
    seen = []
    for d in deps:
        if d not in seen:
            seen.append(d)
    return sorted(seen)


def _extract_quoted(text):
    """Return the double-quoted tokens found in a line (TOML array elements)."""
    out = []
    buf = None
    for ch in text:
        if ch == '"':
            if buf is None:
                buf = ""
            else:
                out.append(buf)
                buf = None
        elif buf is not None:
            buf += ch
    return out


def detect_pipelines(repo_path, files):
    """Return CI/CD and build pipeline files/dirs detected in the repo."""
    found = []
    for pat in PIPELINE_PATTERNS:
        for f in files:
            norm = f.replace(os.sep, "/")
            if norm == pat or norm.startswith(pat + "/") or os.path.basename(f) == pat:
                found.append(f)
    return sorted(set(found))


def detect_workflows(repo_path, files):
    """Return likely entrypoints / scripts that hint at business workflows."""
    found = []
    for f in files:
        base = os.path.basename(f)
        if base in ("__main__.py", "main.py", "app.py", "cli.py", "manage.py", "wsgi.py", "asgi.py"):
            found.append(f)
        elif base == "pyproject.toml":
            # project.scripts entrypoints are workflow signals.
            try:
                with open(os.path.join(repo_path, f), "r", encoding="utf-8", errors="replace") as fh:
                    if "[project.scripts]" in fh.read():
                        found.append(f + " ([project.scripts])")
            except OSError:
                pass
    return sorted(set(found))


def map_modules(repo_path, files):
    """Group source files by top-level directory / package for the project map."""
    groups = {}
    for f in files:
        parts = f.split(os.sep)
        top = parts[0] if len(parts) > 1 else "(root)"
        groups.setdefault(top, []).append(f)
    return {k: sorted(v) for k, v in sorted(groups.items())}


# ---------------------------------------------------------------------------
# Artifact rendering (compact, factual markdown)
# ---------------------------------------------------------------------------
def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _header(title, repo_name):
    return "# {}\n\n> Generated by APIA off-model ingest (no LLM/network calls) for `{}`.\n\n".format(
        title, repo_name
    )


def render_stack(repo_name, stacks):
    out = _header("Stack", repo_name)
    if not stacks:
        return out + "No known stack detected.\n"
    primary = max(stacks.items(), key=lambda kv: kv[1]["source_count"])[0]
    out += "Primary stack: **{}**\n\n".format(primary)
    out += "| Stack | Source files | Manifests | Frameworks |\n"
    out += "|---|---|---|---|\n"
    for name, info in sorted(stacks.items()):
        manifests = ", ".join(os.path.basename(m) for m in info["manifests"]) or "-"
        frameworks = ", ".join(info["frameworks"]) or "-"
        out += "| {} | {} | {} | {} |\n".format(name, info["source_count"], manifests, frameworks)
    return out


def render_project_map(repo_name, modules):
    out = _header("Project map", repo_name)
    for top, members in modules.items():
        out += "## {}\n\n".format(top)
        for m in members:
            out += "- `{}`\n".format(m)
        out += "\n"
    return out


def render_architecture(repo_name, modules, stacks):
    out = _header("Architecture", repo_name)
    out += "Top-level components (directory -> file count):\n\n"
    out += "| Component | Files |\n|---|---|\n"
    for top, members in modules.items():
        out += "| {} | {} |\n".format(top, len(members))
    out += "\nDetected stacks: {}\n".format(", ".join(sorted(stacks)) or "none")
    return out


def render_dependencies(repo_name, deps):
    out = _header("Dependencies", repo_name)
    if not deps:
        return out + "No dependency manifests found (or no declared dependencies).\n"
    for entry in deps:
        out += "## `{}`\n\n".format(entry["manifest"])
        if entry["deps"]:
            for d in entry["deps"]:
                out += "- {}\n".format(d)
        else:
            out += "- (no declared dependencies)\n"
        out += "\n"
    return out


def render_pipelines(repo_name, pipelines):
    out = _header("Pipelines", repo_name)
    if not pipelines:
        return out + "No CI/CD or build pipeline files detected.\n"
    for p in pipelines:
        out += "- `{}`\n".format(p)
    return out


def render_workflows(repo_name, workflows):
    out = _header("Workflows", repo_name)
    if not workflows:
        return out + "No obvious entrypoints / workflow scripts detected.\n"
    for w in workflows:
        out += "- `{}`\n".format(w)
    return out


def render_test_inventory(repo_name, tests):
    out = _header("Test inventory", repo_name)
    total = sum(len(t["functions"]) for t in tests)
    out += "Test files: **{}** | Test functions: **{}**\n\n".format(len(tests), total)
    if not tests:
        out += "No tests found. (Legacy / no-tests case: characterization tests "
        out += "should be generated before any modification.)\n"
        return out
    for t in tests:
        out += "## `{}`\n\n".format(t["path"])
        if t["functions"]:
            for fn in t["functions"]:
                out += "- {}\n".format(fn)
        else:
            out += "- (no test functions parsed)\n"
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# Incremental cache / index
# ---------------------------------------------------------------------------
def build_source_index(repo_path, files):
    """Return {rel_path: short_hash} for every walked file (the change oracle)."""
    index = {}
    for rel in files:
        full = os.path.join(repo_path, rel)
        try:
            index[rel] = file_hash(full)
        except OSError:
            continue
    return index


def load_index(knowledge_dir):
    path = os.path.join(knowledge_dir, INDEX_FILE)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    return None


def changed_paths(old_index, new_index):
    """Return the set of paths added/removed/changed between two source indexes."""
    if not old_index:
        return set(new_index.keys())
    old_sources = old_index.get("sources", {})
    changed = set()
    for path, h in new_index.items():
        if old_sources.get(path) != h:
            changed.add(path)
    for path in old_sources:
        if path not in new_index:
            changed.add(path)
    return changed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def ingest(repo_path, knowledge_dir, full=False):
    """Run ingestion and write artifacts. Returns a result summary dict.

    This is the single entrypoint used by both the CLI and the tests.
    """
    repo_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_path.rstrip(os.sep))
    os.makedirs(knowledge_dir, exist_ok=True)

    files = walk_repo(repo_path)
    new_sources = build_source_index(repo_path, files)

    old_index = None if full else load_index(knowledge_dir)
    changed = changed_paths(old_index, new_sources)

    # Determine whether anything needs reprocessing. We regenerate artifacts
    # only when sources changed, when --full was requested, or when any artifact
    # is missing on disk (first run / deleted output).
    artifacts_present = all(
        os.path.isfile(os.path.join(knowledge_dir, a)) for a in ARTIFACTS
    )
    index_present = os.path.isfile(os.path.join(knowledge_dir, INDEX_FILE))
    reprocess = full or bool(changed) or not artifacts_present or not index_present

    if not reprocess:
        return {
            "repo": repo_name,
            "reprocessed": False,
            "changed_paths": [],
            "artifacts": ARTIFACTS,
            "knowledge_dir": knowledge_dir,
            "stacks": (old_index or {}).get("stacks", []),
            "test_count": (old_index or {}).get("test_count", 0),
        }

    # --- static analysis passes (all off-model) ---
    stacks = detect_stacks(repo_path, files)
    modules = map_modules(repo_path, files)
    deps = parse_dependencies(repo_path, files)
    pipelines = detect_pipelines(repo_path, files)
    workflows = detect_workflows(repo_path, files)
    tests = discover_tests(repo_path, files)

    rendered = {
        "stack.md": render_stack(repo_name, stacks),
        "project-map.md": render_project_map(repo_name, modules),
        "architecture.md": render_architecture(repo_name, modules, stacks),
        "dependencies.md": render_dependencies(repo_name, deps),
        "pipelines.md": render_pipelines(repo_name, pipelines),
        "workflows.md": render_workflows(repo_name, workflows),
        "test-inventory.md": render_test_inventory(repo_name, tests),
    }

    for name, content in rendered.items():
        with open(os.path.join(knowledge_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)

    index = {
        "generated_at": _timestamp(),
        "repo": repo_name,
        "repo_path": repo_path,
        "stacks": sorted(stacks.keys()),
        "test_count": sum(len(t["functions"]) for t in tests),
        "test_file_count": len(tests),
        "sources": new_sources,
        "artifacts": {name: ARTIFACTS for name in ["all"]},
    }
    # Record, per artifact, which source paths it was derived from. For this
    # mechanical ingester every artifact is a view over the whole file set, so
    # the derivation set is the full source index; the hashes live in sources.
    index["artifacts"] = {name: sorted(new_sources.keys()) for name in ARTIFACTS}

    with open(os.path.join(knowledge_dir, INDEX_FILE), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, sort_keys=True)

    return {
        "repo": repo_name,
        "reprocessed": True,
        "changed_paths": sorted(changed),
        "artifacts": ARTIFACTS,
        "knowledge_dir": knowledge_dir,
        "stacks": sorted(stacks.keys()),
        "test_count": index["test_count"],
    }


def default_knowledge_dir():
    """Default to the package's .apia/knowledge/ directory (next to this script)."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    apia_dir = os.path.dirname(scripts_dir)
    return os.path.join(apia_dir, "knowledge")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="APIA off-model ingestion engine (pure static analysis; no LLM/network calls)."
    )
    parser.add_argument("repo_path", help="Path to the repository to ingest.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full re-ingest, ignoring the incremental cache.",
    )
    parser.add_argument(
        "--knowledge-dir",
        default=None,
        help="Where to write knowledge artifacts (default: .apia/knowledge/).",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.repo_path):
        parser.error("repo_path is not a directory: {}".format(args.repo_path))

    knowledge_dir = args.knowledge_dir or default_knowledge_dir()
    result = ingest(args.repo_path, knowledge_dir, full=args.full)

    if result["reprocessed"]:
        print("Ingested '{}': {} stack(s) {}, {} test function(s).".format(
            result["repo"],
            len(result["stacks"]),
            result["stacks"],
            result["test_count"],
        ))
        print("Wrote {} artifacts + {} to {}".format(
            len(result["artifacts"]), INDEX_FILE, knowledge_dir
        ))
        if result["changed_paths"]:
            print("Changed paths: {}".format(len(result["changed_paths"])))
    else:
        print("No changes since last ingest of '{}'. Nothing reprocessed (cache hit).".format(
            result["repo"]
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
