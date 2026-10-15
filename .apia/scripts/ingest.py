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
    project-facts.md   persisted detection memory (build system, test framework,
                       confidence, evidence, and a specific clarifying question
                       when confidence is below threshold)
    .index.json        incremental cache: per-artifact source paths + hashes

Optional preflight:
    python3 .apia/scripts/ingest.py --doctor      # offline toolchain check
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
import re
import sys
from datetime import datetime, timezone

# share.py (Phase-6a) ships the LOCAL plugin loader. ingest.py consults it so a
# team-provided LOCAL adapter plugin (.apia/plugins/adapters/) contributes its
# stack to detection even when the built-in STACK_RULES would not surface it
# (e.g. a new/unusual stack). The import is OPTIONAL and ADDITIVE: when share.py
# is absent or no adapter plugins are present, detection behaves exactly as
# before. share.py is a LOCAL stdlib-only module (no network/LLM). The scripts
# dir is this file's own directory, already importable.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import share  # noqa: E402  (reused for plugin adapter discovery)
except ImportError:
    share = None


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
        "manifest_globs": ["*.vcxproj", "*.sln"],
        "extensions": [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h"],
        # Marker substrings scanned (never executed) in manifests + bounded
        # C/C++ sources, CMake files, and MSBuild project files. These implement
        # the DETECT-FIRST principle: real project evidence, no guessing.
        "frameworks": {
            "googletest": ["gtest/gtest.h", "TEST(", "TEST_F(", "find_package(GTest", "gtest_main", "googletest"],
            "catch2": ["catch2/catch.hpp", "catch_amalgamated", "TEST_CASE(", "find_package(Catch2"],
            "ctest": ["enable_testing()", "add_test("],
            "vstest": ["Microsoft.VisualStudio.TestTools", "[TestMethod]", "[TestClass]"],
            "boost_test": ["boost/test/", "BOOST_AUTO_TEST_CASE", "BOOST_TEST"],
        },
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
    ".ruff_cache",
    "node_modules",
    ".idea",
    ".vs",
    "build",
    "dist",
    # Excluded BY DEFAULT so an ordinary ingest never indexes the knowledge
    # base / APIA package itself. This default is OVERRIDABLE for self-ingest:
    # pass --include-apia on the CLI (or include_apia=True to ingest/walk_repo)
    # to remove .apia from the effective ignore set for that run only, so APIA
    # can index its OWN scripts/tests/adapters. The default stays excluded
    # because a normal project ingest should not surface APIA's own machinery.
    ".apia",
    # Vendored / third-party dependency trees. These hold code the project does
    # not own (e.g. a C++ repo that checks Eigen / nlohmann / GoogleTest into
    # cpp/third-parties/), so walking them inflates the file/stack counts and
    # buries the project's OWN sources. Skipped by default like build/ and
    # node_modules/; conservative to common conventional directory names.
    "third-parties",
    "third-party",
    "third_party",
    "thirdparty",
    "vendor",
    "vendored",
    "external",
    "externals",
    "extern",
    "deps",
    ".conan",
}

ARTIFACTS = [
    "project-map.md",
    "architecture.md",
    "dependencies.md",
    "pipelines.md",
    "workflows.md",
    "test-inventory.md",
    "stack.md",
    "project-facts.md",
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


def is_ignored(rel_path, name, gitignore_patterns, ignores=DEFAULT_IGNORES):
    """Return True if a path should be skipped (default ignores + .gitignore).

    ``ignores`` is the effective default-ignore set to consult; it defaults to
    the module-level ``DEFAULT_IGNORES`` so existing callers are unchanged. The
    walk passes a per-run set here (e.g. with ``.apia`` removed for self-ingest)
    rather than mutating the shared global.
    """
    if name in ignores:
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
def walk_repo(repo_path, include_apia=False):
    """Walk the repository, returning a sorted list of repo-relative file paths.

    Respects DEFAULT_IGNORES and a minimal .gitignore. Pure filesystem walk; no
    network access.

    When ``include_apia`` is True the ``.apia`` directory is removed from the
    effective ignore set for THIS run only (opt-in self-ingest), so APIA can
    index its own scripts/tests/adapters. The default (False) keeps ``.apia``
    excluded. The module-level DEFAULT_IGNORES is never mutated -- a fresh
    effective set is built per call so the choice cannot leak across calls/tests.
    """
    effective_ignores = (DEFAULT_IGNORES - {".apia"}) if include_apia else DEFAULT_IGNORES
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
            if not is_ignored(rel, d, gitignore_patterns, effective_ignores):
                kept.append(d)
        dirs[:] = sorted(kept)
        for fn in sorted(filenames):
            rel = os.path.join(rel_root, fn) if rel_root else fn
            if is_ignored(rel, fn, gitignore_patterns, effective_ignores):
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
        frameworks = detect_frameworks(repo_path, files, manifests, rules["frameworks"], stack=stack)
        entry = {
            "manifests": sorted(set(manifests)),
            "extensions": exts,
            "source_count": sum(exts.values()),
            "frameworks": sorted(frameworks),
        }
        if stack == "cpp":
            # DETECT-FIRST: infer build system + score confidence from the real
            # evidence so the (off-model) ingester can RECORD a specific
            # clarifying question only when confidence is genuinely low.
            entry["build_system"] = infer_build_system(files)
            entry["decision"] = score_cpp_decision(
                repo_path, files, sorted(set(manifests)), sorted(frameworks), entry["build_system"]
            )
        detected[stack] = entry

    # ADDITIVE plugin wiring (Phase-6a): a team-provided LOCAL adapter plugin
    # (.apia/plugins/adapters/<stack>.yml) may declare a stack the built-in
    # STACK_RULES do not model. When such a plugin's declared extensions /
    # manifests have real evidence in this repo, surface it as a detected stack
    # so a team adapter is actually discovered. Best-effort and non-crashing:
    # no plugins -> `detected` is exactly the built-in detection.
    detected = _augment_detected_with_plugins(repo_path, files, detected,
                                              basenames, ext_counts)
    return detected


def _augment_detected_with_plugins(repo_path, files, detected, basenames, ext_counts):
    """Fold LOCAL adapter-plugin stacks (with repo evidence) into `detected`.

    A plugin adapter YAML carries the same ``extensions`` / ``manifests`` keys as
    a built-in adapter. When a plugin stack is NOT already detected and its
    declared extensions/manifests have evidence in the repo, add an entry mirror-
    ing the built-in shape so downstream (stack.md / adapter resolution) finds it.
    Never raises; returns `detected` unchanged on any failure or when no plugin
    contributes an evidenced stack.
    """
    if share is None:
        return detected
    try:
        catalog = share.load_plugins(repo_path)
    except Exception:
        return detected
    for entry in catalog.get("adapters", []):
        name = entry.get("name")
        data = entry.get("data") or {}
        if not name or name in detected:
            continue
        exts_decl = data.get("extensions") or []
        manifests_decl = (data.get("manifests") or [])
        matched_exts = {e: ext_counts[e] for e in exts_decl if e in ext_counts}
        matched_manifests = []
        for m in manifests_decl:
            if m in basenames:
                matched_manifests.append(basenames[m])
            else:
                for f in files:
                    if fnmatch.fnmatch(os.path.basename(f), m):
                        matched_manifests.append(f)
        if not matched_exts and not matched_manifests:
            continue
        detected[name] = {
            "manifests": sorted(set(matched_manifests)),
            "extensions": matched_exts,
            "source_count": sum(matched_exts.values()),
            "frameworks": sorted(data.get("frameworks") or []),
            "plugin": True,
        }
    return detected


# Bounds for the C++ source/header marker scan. Detection must stay cheap and
# deterministic, so we cap how many files and how many bytes per file we read.
CPP_SCAN_FILE_CAP = 200
CPP_SCAN_BYTES_CAP = 200000
# C/C++ source + header + build-project extensions scanned for framework markers.
_CPP_SCAN_EXTS = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".vcxproj", ".sln", ".cs")


def _cpp_scan_targets(files, manifests):
    """Return a deterministic, bounded list of C++-relevant files to scan.

    Includes CMake/manifest files (so find_package / enable_testing / add_test
    markers are seen) plus bounded C/C++ sources and headers and MSBuild project
    files. Pure path selection; nothing is opened here.
    """
    targets = []
    seen = set()

    def _add(rel):
        if rel not in seen:
            seen.add(rel)
            targets.append(rel)

    for m in manifests:
        _add(m)
    for f in sorted(files):
        base = os.path.basename(f)
        ext = os.path.splitext(f)[1].lower()
        if base == "CMakeLists.txt" or base.endswith(".cmake"):
            _add(f)
        elif ext in _CPP_SCAN_EXTS:
            _add(f)
    return targets[:CPP_SCAN_FILE_CAP]


def detect_frameworks(repo_path, files, manifests, framework_rules, stack=None):
    """Scan manifests and a bounded set of source files for framework markers.

    For Python the scan targets are manifests plus conventional test files. For
    C++ (and any stack whose markers live in sources/headers/build files) we also
    scan a bounded set of C/C++ sources, CMake files, and MSBuild project files so
    GoogleTest/Catch2/CTest/Boost.Test/VSTest evidence is seen. Substring scan
    only; no file is ever executed.
    """
    found = set()
    if stack == "cpp":
        scan_targets = _cpp_scan_targets(files, manifests)
        byte_cap = CPP_SCAN_BYTES_CAP
    else:
        scan_targets = list(manifests)
        # Also scan test files, where framework imports usually live.
        for f in files:
            base = os.path.basename(f)
            if base.startswith("test_") or base.endswith("_test.py") or "tests" in f.split(os.sep):
                scan_targets.append(f)
        byte_cap = None
    for rel in scan_targets:
        full = os.path.join(repo_path, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(byte_cap) if byte_cap is not None else fh.read()
        except (OSError, UnicodeError):
            continue
        for name, markers in framework_rules.items():
            if any(marker in text for marker in markers):
                found.add(name)
    return found


def infer_build_system(files):
    """Infer the C++ build system from real project evidence (pure static).

    Returns 'cmake' when a CMakeLists.txt is present, 'msbuild' when a .sln or
    .vcxproj is present, else None. When BOTH are present, CMake wins (it is the
    stronger, cross-platform signal) but both are noted by the caller via the
    evidence list. Nothing is executed; this is filename inspection only.
    """
    has_cmake = any(os.path.basename(f) == "CMakeLists.txt" for f in files)
    has_msbuild = any(
        f.lower().endswith(".sln") or f.lower().endswith(".vcxproj") for f in files
    )
    if has_cmake and has_msbuild:
        return "cmake"  # prefer CMake; caller records that both were seen
    if has_cmake:
        return "cmake"
    if has_msbuild:
        return "msbuild"
    return None


# Strong vs supporting markers for confidence scoring. Strong markers are
# framework-specific and hard to see by accident; supporting markers (like a
# bare add_test()) are weaker on their own.
_CPP_STRONG_MARKERS = {
    "googletest": ["gtest/gtest.h", "find_package(GTest", "gtest_main"],
    "catch2": ["catch2/catch.hpp", "catch_amalgamated", "find_package(Catch2"],
    "boost_test": ["boost/test/", "BOOST_AUTO_TEST_CASE"],
    "vstest": ["Microsoft.VisualStudio.TestTools", "[TestMethod]", "[TestClass]"],
    "ctest": ["enable_testing()", "add_test("],
}


def _collect_cpp_evidence(repo_path, files, manifests):
    """Return a list of (file, marker) tuples for every C++ marker actually seen.

    Deterministic and bounded (reuses the same scan targets / byte cap as
    detection). Used both for confidence scoring and for the human-readable
    evidence listing in project-facts.md.
    """
    rules = STACK_RULES["cpp"]["frameworks"]
    evidence = []
    for rel in _cpp_scan_targets(files, manifests):
        full = os.path.join(repo_path, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(CPP_SCAN_BYTES_CAP)
        except (OSError, UnicodeError):
            continue
        for name, markers in rules.items():
            for marker in markers:
                if marker in text:
                    evidence.append((rel, marker, name))
    return evidence


def _load_confidence_threshold(repo_path):
    """Read confidence_threshold from .apia/config.yml, defaulting to 0.75.

    Uses the same optional-PyYAML pattern as verify.load_adapter. Looks first at
    the ingested repo's own .apia/config.yml, then falls back to the APIA
    package config next to this script. Any failure -> the documented 0.75.
    """
    try:
        import yaml  # optional; only used to read the declarative config
    except ImportError:
        yaml = None

    candidates = [
        os.path.join(repo_path, ".apia", "config.yml"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yml"),
    ]
    if yaml is not None:
        for cfg in candidates:
            if os.path.isfile(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8") as fh:
                        data = yaml.safe_load(fh) or {}
                    val = data.get("confidence_threshold")
                    if isinstance(val, (int, float)):
                        return float(val)
                except (OSError, ValueError):
                    continue
    return 0.75


def score_cpp_decision(repo_path, files, manifests, frameworks, build_system):
    """Score the C++ build-system + test-framework decision in [0, 1].

    Implements DETECT-FIRST / ASK-ONLY-ON-GENUINE-DOUBT / ASK-SPECIFICALLY:
      * Multiple distinct strong markers for a real test framework (e.g. a gtest
        include + find_package(GTest) + TEST(...)) => high confidence (>=0.75):
        proceed and STATE the reasoning.
      * Only a weak/ambiguous signal (e.g. add_test() present but no
        gtest/catch/boost includes => looks like plain CTest with standalone
        executables) => below threshold: record ONE specific clarifying
        question. The ingester is off-model and never asks; it RECORDS the
        question string for the model/user to see.

    Returns a dict: {build_system, test_frameworks, confidence, evidence (list of
    "file: marker" strings), reasoning, threshold, needs_clarification,
    clarifying_question (str or None)}.
    """
    threshold = _load_confidence_threshold(repo_path)
    evidence_pairs = _collect_cpp_evidence(repo_path, files, manifests)
    evidence = ["{}: {}".format(rel, marker) for rel, marker, _ in evidence_pairs]

    # Count distinct strong markers seen per framework.
    strong_hits = {}
    for rel, marker, name in evidence_pairs:
        if marker in _CPP_STRONG_MARKERS.get(name, []):
            strong_hits.setdefault(name, set()).add(marker)

    # A real (non-ctest) framework is one of googletest/catch2/boost_test/vstest.
    real_frameworks = [f for f in frameworks if f != "ctest"]
    strong_real = {k: v for k, v in strong_hits.items() if k != "ctest"}

    confidence = 0.0
    reasoning = ""
    clarifying_question = None

    if strong_real:
        # Highest-evidence real framework drives confidence.
        best = max(strong_real.items(), key=lambda kv: len(kv[1]))
        name, markers = best
        n = len(markers)
        if n >= 2:
            confidence = 0.95
        else:
            confidence = 0.8
        bump = " and build system '{}'".format(build_system) if build_system else ""
        reasoning = (
            "Found {} distinct strong {} marker(s) ({}){} -> proceeding with "
            "test_framework '{}'.".format(n, name, ", ".join(sorted(markers)), bump, name)
        )
    elif real_frameworks:
        # A real framework name matched only via weak markers (e.g. TEST( alone).
        name = real_frameworks[0]
        confidence = 0.7
        reasoning = (
            "Matched test_framework '{}' only via weak marker(s) (no "
            "include/find_package evidence) -> below threshold.".format(name)
        )
        clarifying_question = _compose_cpp_question(build_system, frameworks, "weak")
    elif "ctest" in frameworks:
        # Classic ambiguous case: add_test()/enable_testing() but no framework
        # include. Looks like plain CTest driving standalone executables.
        confidence = 0.6
        reasoning = (
            "Found CTest wiring (enable_testing()/add_test()) but no "
            "GoogleTest/Catch2/Boost.Test includes -> looks like plain CTest "
            "with standalone executables; below threshold."
        )
        clarifying_question = _compose_cpp_question(build_system, frameworks, "ctest")
    else:
        confidence = 0.3
        reasoning = "No conclusive test-framework evidence found."
        clarifying_question = _compose_cpp_question(build_system, frameworks, "none")

    needs_clarification = confidence < threshold
    if not needs_clarification:
        clarifying_question = None

    return {
        "build_system": build_system,
        "test_frameworks": sorted(frameworks),
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "reasoning": reasoning,
        "threshold": threshold,
        "needs_clarification": needs_clarification,
        "clarifying_question": clarifying_question,
    }


def _compose_cpp_question(build_system, frameworks, kind):
    """Compose ONE specific, informed clarifying question (ASK-SPECIFICALLY).

    Shows what was found, what was inferred, the uncertainty, and a best guess,
    then asks the user to confirm, name the framework, or paste the exact test
    command. Returned as a string; the off-model ingester only records it.
    """
    bs = build_system or "unknown"
    if kind == "ctest":
        return (
            "I found add_test()/enable_testing() in CMake but no "
            "GoogleTest/Catch2/Boost.Test includes - looks like plain CTest "
            "running standalone executables (build system: {}), ~60% sure. "
            "Is that right? If not, name the framework "
            "(GoogleTest/Catch2/Boost.Test/VSTest) or paste the exact command "
            "you use to run the tests.".format(bs)
        )
    if kind == "weak":
        guessed = ", ".join(f for f in frameworks if f != "ctest") or "none"
        return (
            "I saw a possible test marker suggesting '{}' but no "
            "include/find_package evidence to confirm it (build system: {}), "
            "~70% sure. Is that the framework? If not, name the framework "
            "(GoogleTest/Catch2/Boost.Test/VSTest) or paste the exact command "
            "you use to run the tests.".format(guessed, bs)
        )
    return (
        "I detected a C++ project (build system: {}) but found no conclusive "
        "test-framework evidence (no GoogleTest/Catch2/Boost.Test/VSTest/CTest "
        "markers). Which test framework does this project use, or what exact "
        "command runs its tests?".format(bs)
    )


# ---------------------------------------------------------------------------
# Doctor / preflight toolchain check (cross-stack, offline, stdlib-only)
# ---------------------------------------------------------------------------
# Reusable so FEAT-003 can import and expose it from verify.py too. It locates
# tools on PATH with shutil.which (no execution for presence) and reports a
# version via a local `tool --version` subprocess (an allowed LOCAL command,
# consistent with verify.py only ever spawning local build/test commands). It is
# strictly offline: no network, no LLM.
# Each tool entry is (exe, version_args, description, category). The CATEGORY
# drives the doctor EXIT-CODE policy (not the warning text, which is always
# emitted for every missing tool because it is useful):
#   "cpp"      -> required for a C++ CMake/CTest run on ANY host.
#   "core"     -> required on every host regardless of stack (git).
#   "windows"  -> MSVC-toolchain tools that exist ONLY inside a VS Developer
#                 shell on Windows; their absence is NEVER a hard failure on a
#                 non-Windows host (or when the resolved run does not use them).
#   "optional" -> nice-to-have / alternative-invocation tools whose absence must
#                 never gate a run (e.g. a standalone `pytest` shim, since the
#                 Python path invokes `python -m pytest`).
#   "scanner"     -> Phase-2 Reviewer scanner tools for the Python stack
#                 (bandit/ruff/radon/pip-audit/flake8). ADVISORY: their absence
#                 is REPORTED (warning) but NEVER a hard failure -- the reviewer
#                 degrades gracefully to a tool_unavailable marker.
#   "scanner_cpp" -> Phase-2 Reviewer scanner tools for the C++ stack
#                 (cppcheck/clang-tidy). Also advisory / non-gating.
DOCTOR_TOOLS = [
    ("cmake", ["--version"], "CMake build system (C++ configure/build).", "cpp"),
    ("ctest", ["--version"], "CTest test runner (C++).", "cpp"),
    ("g++", ["--version"], "GNU C++ compiler.", "cpp"),
    ("gcc", ["--version"], "GNU C compiler.", "optional"),
    ("make", ["--version"], "GNU make.", "cpp"),
    ("msbuild", ["-version"], "MSBuild (Windows/VS; present only inside a VS Developer shell).", "windows"),
    # cl.exe has no `--version` flag: run with no args it prints its banner on
    # stderr and exits non-zero, so there is no clean version line to parse. It
    # is deliberately a PRESENCE-ONLY check (version_args == []): we report
    # whether it is reachable, not a version string.
    ("cl", [], "MSVC compiler cl.exe (Windows/VS Developer shell only; presence-only, no version probe).", "windows"),
    ("git", ["--version"], "Git.", "core"),
    ("pytest", ["--version"], "pytest (Python test runner).", "optional"),
    # Phase-2 Reviewer scanners (scan.py). ADVISORY, never gating: a missing
    # scanner is reported as a warning only. The Python scanners are invoked via
    # `python -m <mod>` by the adapter so they run under the venv and install no
    # reliable console script on PATH; a shutil.which lookup therefore
    # UNDER-reports them as MISSING even when installed. Each declares a MODULE
    # probe (the optional 5th element) so doctor() reports presence by MODULE
    # availability in this interpreter (importlib.util.find_spec) -- the same
    # interpreter the `python -m <mod>` scanner commands run under -- instead of
    # a PATH lookup. (pip-audit's import module is ``pip_audit``.)
    ("bandit", ["-m", "bandit", "--version"], "bandit (Python security scanner; Reviewer).", "scanner", "bandit"),
    ("ruff", ["-m", "ruff", "--version"], "ruff (Python lint/quality scanner; Reviewer).", "scanner", "ruff"),
    ("radon", ["-m", "radon", "--version"], "radon (Python complexity/perf scanner; Reviewer).", "scanner", "radon"),
    ("pip-audit", ["-m", "pip_audit", "--version"], "pip-audit (Python dependency CVE scanner; Reviewer).", "scanner", "pip_audit"),
    ("flake8", ["-m", "flake8", "--version"], "flake8 (Python lint/quality scanner; Reviewer).", "scanner", "flake8"),
    ("cppcheck", ["--version"], "cppcheck (C++ security/quality/perf scanner; Reviewer).", "scanner_cpp"),
    ("clang-tidy", ["--version"], "clang-tidy (C++ quality scanner; Reviewer).", "scanner_cpp"),
]
# Tools that on Windows live only inside the "VS 2022 Developer Command Prompt".
_VS_DEV_SHELL_TOOLS = {"msbuild", "cl"}

# Categories whose ABSENCE should gate the doctor exit code when no specific
# stack has been resolved. Windows/optional tools are intentionally excluded so
# a complete C++ toolchain on a non-Windows host exits 0 even though msbuild/cl/
# standalone-pytest are (correctly) missing. When a repo path IS supplied,
# required_tool_categories() narrows this to the detected stack.
_DEFAULT_REQUIRED_CATEGORIES = {"cpp", "core"}

# Map a detected stack name -> the tool categories required to run it. "core"
# (git) is always required. Unknown/other stacks fall back to "core" only.
_STACK_REQUIRED_CATEGORIES = {
    "cpp": {"cpp", "core"},
}


def _tool_entry_fields(entry):
    """Normalize a DOCTOR_TOOLS entry to (exe, version_args, desc, category).

    Accepts legacy 3-tuples (exe, version_args, desc) for backward compatibility
    with callers/tests that predate the category field; such entries default to
    the ``optional`` category so they never gate the exit code unexpectedly.
    """
    if len(entry) >= 4:
        return entry[0], entry[1], entry[2], entry[3]
    exe, version_args, desc = entry
    return exe, version_args, desc, "optional"


def _tool_entry_module(entry):
    """Return the module-availability probe declared by a DOCTOR_TOOLS entry.

    Scanner tools that are invoked as ``python -m <module>`` (bandit / ruff /
    radon / pip-audit / flake8) install no reliable console script on PATH, so a
    ``shutil.which`` lookup under-reports them as MISSING even though they ARE
    installed and runnable in the current interpreter's environment. Such an
    entry may declare a MODULE name as an optional 5th element; when present,
    doctor() probes presence via module availability in THIS interpreter
    (importlib.util.find_spec, a pure in-process lookup -- no network, no code
    execution) instead of a PATH lookup. Entries without a 5th element (every
    legacy 3-/4-tuple) return None and keep the shutil.which behavior unchanged.
    """
    if len(entry) >= 5 and entry[4]:
        return entry[4]
    return None


def _module_available(module):
    """Return True when ``module`` is importable in the current interpreter.

    Uses importlib.util.find_spec, which resolves the module WITHOUT importing
    (and thus without executing) it. Strictly local and offline: this is the
    same interpreter (sys.executable) the adapter's ``python -m <module>``
    scanner commands run under, so availability here means the scanner is
    genuinely runnable.
    """
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def required_tool_categories(repo_path=None):
    """Return the set of tool categories whose absence should gate the exit code.

    When ``repo_path`` resolves to a known stack (via detect_stacks), the
    required set is that stack's categories; otherwise the cross-stack default
    ({cpp, core}) is used. Windows-only and optional tools are never in the set,
    so a healthy non-Windows C++ toolchain exits 0. This mirrors how
    verify.preflight_missing_tool only checks the tools the resolved run uses.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return set(_DEFAULT_REQUIRED_CATEGORIES)
    try:
        files = walk_repo(repo_path)
        stacks = detect_stacks(repo_path, files)
    except Exception:  # detection is best-effort; never let it crash doctor
        return set(_DEFAULT_REQUIRED_CATEGORIES)
    required = {"core"}
    for stack in stacks:
        required |= _STACK_REQUIRED_CATEGORIES.get(stack, set())
    return required or set(_DEFAULT_REQUIRED_CATEGORIES)


def _tool_version(exe, version_args):
    """Return the first non-empty line of `exe <version_args>` output, or None.

    Runs a LOCAL subprocess only (no network). Any failure returns None. We never
    execute project files here -- only the tool's own --version banner.
    """
    if not version_args:
        return None
    import subprocess  # stdlib; local process only, never a network call
    try:
        proc = subprocess.run(
            [exe] + list(version_args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def doctor(tools=None, repo_path=None, required_categories=None):
    """Offline toolchain preflight. Returns a dict report; also human-printable.

    For each tool: locate it on PATH with shutil.which (NO execution needed to
    determine presence), and when present report a version via a local
    `tool --version`. Missing tools and the Windows VS-Developer-shell
    requirement for MSBuild/cl are flagged as clear WARNINGS. Stdlib-only and
    strictly offline.

    EXIT-CODE POLICY (``missing_required``): a missing tool is only a hard
    failure when its category is required for the resolved run. ``warnings``
    still lists EVERY missing tool (the text is useful), but ``missing_required``
    lists only the subset whose category gates the exit code. The required
    categories are resolved from ``repo_path`` (its detected stack) when given,
    else from ``required_categories``, else the cross-stack default
    ({cpp, core}). Windows-only (msbuild/cl) and optional (standalone pytest,
    gcc) tools are therefore never counted as failures on a non-Windows host.
    """
    import shutil  # stdlib; shutil.which is a PATH lookup, not a network call

    if tools is None:
        tools = DOCTOR_TOOLS
    if required_categories is None:
        required_categories = required_tool_categories(repo_path)
    required_categories = set(required_categories)
    results = []
    warnings = []
    missing_required = []
    for entry in tools:
        exe, version_args, desc, category = _tool_entry_fields(entry)
        module = _tool_entry_module(entry)
        if module is not None:
            # Module-invoked scanner (python -m <module>): presence is MODULE
            # availability in THIS interpreter (which is exactly how the adapter
            # runs it), not a console-script PATH lookup. The version probe runs
            # under sys.executable (`python -m <module> --version`).
            present = _module_available(module)
            path = sys.executable if present else None
            version_probed = bool(version_args)
            version = (
                _tool_version(sys.executable, version_args)
                if (present and version_probed) else None
            )
        else:
            path = shutil.which(exe)
            present = path is not None
            # An empty version_args means the tool is checked presence-only (e.g.
            # cl.exe has no clean --version banner). We never run it; we only report
            # whether it is reachable.
            version_probed = bool(version_args)
            version = _tool_version(exe, version_args) if (present and version_probed) else None
        is_required = category in required_categories
        tool_entry = {
            "tool": exe,
            "present": present,
            "path": path,
            "version": version,
            "version_probed": version_probed,
            "description": desc,
            "category": category,
            "required": is_required,
        }
        if not present:
            if exe in _VS_DEV_SHELL_TOOLS:
                warn = (
                    "WARNING: '{}' not found on PATH. On Windows it is available "
                    "only inside the 'VS 2022 Developer Command Prompt' (the "
                    "Developer shell sets up MSBuild/cl). Launch APIA from that "
                    "shell for MSBuild/VSTest projects.".format(exe)
                )
            else:
                warn = "WARNING: '{}' not found on PATH ({}).".format(exe, desc)
            warnings.append(warn)
            tool_entry["warning"] = warn
            if is_required:
                missing_required.append(exe)
        results.append(tool_entry)
    return {
        "tools": results,
        "warnings": warnings,
        "missing_required": missing_required,
        "required_categories": sorted(required_categories),
    }


def format_doctor_report(report):
    """Render a doctor() report as a compact human-readable string."""
    lines = ["APIA doctor / preflight (offline toolchain check)", ""]
    for e in report["tools"]:
        if e["present"]:
            if not e.get("version_probed", True):
                ver = "(presence-only; no version probe)"
            else:
                ver = e["version"] or "(version unavailable)"
            lines.append("  [ok]   {:<8} {}  -> {}".format(e["tool"], e["path"], ver))
        else:
            tag = "required" if e.get("required") else "optional"
            lines.append("  [MISS] {:<8} not found on PATH ({})".format(e["tool"], tag))
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in report["warnings"]:
            lines.append("  - {}".format(w))
    else:
        lines.append("")
        lines.append("All checked tools present.")
    # The exit code gates only on REQUIRED tools; make that explicit so a
    # non-Windows host with a healthy C++ toolchain reads as OK despite the
    # (expected) msbuild/cl/standalone-pytest warnings above.
    missing_required = report.get("missing_required")
    if missing_required is not None:
        lines.append("")
        if missing_required:
            lines.append(
                "Preflight: FAIL -- missing required tool(s): {}.".format(
                    ", ".join(missing_required)))
        else:
            lines.append(
                "Preflight: OK -- all required tools present "
                "(any warnings above are optional/other-platform tools).")
    return "\n".join(lines) + "\n"


# C++ test-case macros scanned (never executed) to count individual cases in a
# C++ test file. The macro names mirror STACK_RULES['cpp']['frameworks'] markers
# (GoogleTest TEST/TEST_F/TEST_P, Catch2 TEST_CASE, Boost.Test
# BOOST_AUTO_TEST_CASE / BOOST_FIXTURE_TEST_CASE). A static regex scan locates
# each macro invocation and captures its argument(s) as the case label.
_CPP_TEST_CASE_RE = re.compile(
    r"\b(TEST_F|TEST_P|TEST|TEST_CASE|BOOST_AUTO_TEST_CASE|BOOST_FIXTURE_TEST_CASE)"
    r"\s*\(\s*([^),\s]+)",
)

# C++ source extensions that may hold tests (mirrors cpp.yml test_discovery_glob
# which targets .cpp; .cc/.cxx are the common GoogleTest/Catch2 variants).
_CPP_TEST_EXTS = (".cpp", ".cc", ".cxx")


def _is_cpp_test_file(rel):
    """Return True if a C++ source path follows a conventional test-file pattern.

    Recognizes (aligned with cpp.yml test_discovery_glob):
      * PascalCase ``*Test.cpp`` / ``*Tests.cpp``
      * snake_case ``*_test.cpp`` / ``test_*.cpp`` (and .cc/.cxx variants)
      * any C++ source living under a ``test/`` or ``tests/`` directory
    Pure name/path matching; the file is never compiled or executed.
    """
    low = rel.lower()
    if not low.endswith(_CPP_TEST_EXTS):
        return False
    base = os.path.basename(rel)
    stem = base.rsplit(".", 1)[0]
    # PascalCase convention: FooTest.cpp / FooTests.cpp.
    if stem.endswith("Test") or stem.endswith("Tests"):
        return True
    low_base = base.lower()
    low_stem = low_base.rsplit(".", 1)[0]
    if low_stem.endswith("_test") or low_stem.startswith("test_"):
        return True
    # Any C++ source under a test/ or tests/ directory (normalize separators).
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("test", "tests") for p in parts[:-1]):
        return True
    return False


def _discover_cpp_test_cases(repo_path, rel):
    """Return the list of C++ test-case labels found in a test file (static scan).

    Counts GoogleTest/Catch2/Boost.Test case macros by regex. No execution; the
    file is read once (bounded by the shared C++ scan cap). When a recognized
    test file has no parseable macros (e.g. an unusual helper), a single
    placeholder label is returned so the file still counts as holding a test and
    the no-tests/legacy path does not false-trigger.
    """
    full = os.path.join(repo_path, rel)
    cases = []
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(CPP_SCAN_BYTES_CAP)
    except (OSError, UnicodeError):
        text = ""
    for macro, label in _CPP_TEST_CASE_RE.findall(text):
        cases.append("{}({})".format(macro, label))
    if not cases:
        # A conventionally named test file with no parseable macro still counts
        # as one test target (keeps test_count/test_file_count non-zero).
        cases.append("(test case)")
    return cases


def discover_tests(repo_path, files):
    """Discover test files and their test cases (multi-language, no exec).

    Returns a list of dicts: {path, functions:[names]}. Only static parsing is
    performed; test code is never imported or executed.

    Python test files are parsed via AST (unchanged). C++ test files (GoogleTest
    / Catch2 / Boost.Test, recognized by conventional naming aligned with
    cpp.yml's test_discovery_glob) are scanned statically for their test-case
    macros so ``test_count`` / ``test_file_count`` reflect them and the
    no-tests/legacy path is not falsely triggered on a repo that has tests.
    """
    tests = []
    for rel in files:
        base = os.path.basename(rel)
        if rel.endswith(".py"):
            # --- Python AST path (unchanged) ---
            looks_like_test = base.startswith("test_") or base.endswith("_test.py")
            # A file qualifies as a test target when its name follows the
            # conventional pattern, or when it is conftest.py (shared fixtures
            # live there). Non-test helpers inside a tests/ dir are skipped to
            # avoid false positives.
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
        elif _is_cpp_test_file(rel):
            # --- C++ static-scan path (GoogleTest / Catch2 / Boost.Test) ---
            cases = _discover_cpp_test_cases(repo_path, rel)
            tests.append({"path": rel, "functions": sorted(cases)})
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
    # Surface the C++ detected build system + framework + confidence beneath the
    # table (as prose, so the table shape that existing tests assert is intact).
    cpp = stacks.get("cpp")
    if cpp and cpp.get("decision"):
        d = cpp["decision"]
        bs = cpp.get("build_system") or "unknown"
        fw = ", ".join(d["test_frameworks"]) or "none"
        out += "\n**C++ detection:** build system `{}`, test framework(s) `{}`, ".format(bs, fw)
        out += "confidence {:.2f} (threshold {:.2f}).".format(d["confidence"], d["threshold"])
        if d["needs_clarification"]:
            out += " Confidence is below threshold - see `project-facts.md` for the specific question to ask."
        out += "\n"
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


def render_project_facts(repo_name, stacks):
    """Render the persisted project-facts / detection-memory artifact.

    This is the single source of truth for detected build system(s), test
    framework(s), the confidence score, and the concrete evidence (file +
    marker). When confidence < threshold it also carries the ONE specific
    clarifying question the model/user should ask (DETECT-FIRST / ASK-ONLY-ON-
    GENUINE-DOUBT / ASK-SPECIFICALLY). Persisting it here means APIA never has to
    re-detect or re-ask for this project. Always emits a non-empty body, even
    when there are no stack-specific facts (e.g. a pure-Python repo).
    """
    out = _header("Project facts", repo_name)
    out += ("Persisted detection memory. These facts are the single source of "
            "truth so APIA does not re-detect or re-ask on later runs.\n\n")

    cpp = stacks.get("cpp") if stacks else None
    if cpp and cpp.get("decision"):
        d = cpp["decision"]
        out += "## C++\n\n"
        out += "- Build system: `{}`\n".format(cpp.get("build_system") or "unknown")
        out += "- Test framework(s): {}\n".format(
            ", ".join("`{}`".format(f) for f in d["test_frameworks"]) or "none detected"
        )
        out += "- Confidence: **{:.2f}** (threshold {:.2f})\n".format(d["confidence"], d["threshold"])
        out += "- Decision: {}\n".format(d["reasoning"])
        out += "\n### Evidence\n\n"
        if d["evidence"]:
            for ev in d["evidence"]:
                out += "- `{}`\n".format(ev)
        else:
            out += "- (no marker evidence found)\n"
        if d["needs_clarification"] and d["clarifying_question"]:
            out += "\n### Clarifying question (confidence below threshold)\n\n"
            out += "> {}\n".format(d["clarifying_question"])
        else:
            out += "\n_Confidence meets the threshold; proceeding without asking._\n"
        out += (
            "\n_To confirm/override these facts durably, record them under a "
            "`cpp` overrides block in `.apia/config.yml`._\n"
        )
        return out

    out += "No stack-specific detection facts recorded.\n\n"
    out += "- build-system: n/a\n- test-framework: n/a\n"
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


def build_project_facts(stacks):
    """Build the JSON-serializable persisted project-facts block for .index.json.

    Captures, per detected stack where relevant, the build system, test
    framework(s), confidence, threshold, evidence, reasoning, and (when below
    threshold) the specific clarifying question. This is the durable detection
    memory that lets a later ingest skip re-detection / re-asking.
    """
    facts = {}
    cpp = stacks.get("cpp") if stacks else None
    if cpp and cpp.get("decision"):
        d = cpp["decision"]
        facts["cpp"] = {
            "build_system": cpp.get("build_system"),
            "test_frameworks": d["test_frameworks"],
            "confidence": d["confidence"],
            "threshold": d["threshold"],
            "evidence": d["evidence"],
            "reasoning": d["reasoning"],
            "needs_clarification": d["needs_clarification"],
            "clarifying_question": d["clarifying_question"],
        }
    return facts


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
def ingest(repo_path, knowledge_dir, full=False, include_apia=False):
    """Run ingestion and write artifacts. Returns a result summary dict.

    This is the single entrypoint used by both the CLI and the tests.

    ``include_apia`` (opt-in, default False) threads through to walk_repo: when
    True the ``.apia`` directory is included in the walk so APIA can self-ingest
    its own scripts/tests/adapters. Default keeps ``.apia`` excluded.
    """
    repo_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_path.rstrip(os.sep))
    os.makedirs(knowledge_dir, exist_ok=True)

    files = walk_repo(repo_path, include_apia=include_apia)
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
        "project-facts.md": render_project_facts(repo_name, stacks),
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
        # Persisted project-facts / detection memory so later ingests reuse the
        # detected+confirmed facts instead of re-detecting or re-asking.
        "project_facts": build_project_facts(stacks),
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
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=".",
        help="Path to the repository to ingest (default: current directory).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full re-ingest, ignoring the incremental cache.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run the offline toolchain preflight (shutil.which + local --version) and exit.",
    )
    parser.add_argument(
        "--include-apia",
        dest="include_apia",
        action="store_true",
        help="Include the .apia/ package itself in the walk so APIA can ingest "
             "its own scripts/tests/adapters (default: excluded).",
    )
    parser.add_argument(
        "--knowledge-dir",
        default=None,
        help="Where to write knowledge artifacts (default: .apia/knowledge/).",
    )
    args = parser.parse_args(argv)

    if args.doctor:
        # Resolve required tools from the target repo's detected stack when the
        # path is a real directory (so e.g. a C++ repo requires cmake/ctest but
        # not msbuild on Linux). Falls back to the cross-stack default otherwise.
        doctor_repo = args.repo_path if os.path.isdir(args.repo_path) else None
        report = doctor(repo_path=doctor_repo)
        print(format_doctor_report(report), end="")
        # Non-zero exit ONLY when a REQUIRED tool is missing, so a preflight can
        # gate a run without false-failing on absent optional/other-platform
        # tools (msbuild/cl on Linux, a standalone pytest shim, etc.).
        return 1 if report["missing_required"] else 0

    if not os.path.isdir(args.repo_path):
        parser.error("repo_path is not a directory: {}".format(args.repo_path))

    knowledge_dir = args.knowledge_dir or default_knowledge_dir()
    result = ingest(args.repo_path, knowledge_dir, full=args.full,
                    include_apia=args.include_apia)

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
