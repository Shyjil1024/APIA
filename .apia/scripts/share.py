#!/usr/bin/env python3
"""APIA off-model LOCAL sharing engine (Phase-6a).

This script is the deterministic backbone of APIA's LOCAL, git-based knowledge &
capability sharing. It is the sharing counterpart to the other off-model engines
(ingest / scan / scaffold / verify / evalloop / orchestrate): it does ONLY
mechanical, reproducible work and never reasons, never calls a model, and never
touches the network. Sharing happens VIA THE GIT REPO -- the committed files
under ``.apia/`` -- exactly like ``.apia/knowledge/`` is already shared. There is
NO server, NO network service, NO auth/multi-tenancy, and nothing ever leaves the
machine.

It implements FOUR capabilities, all REUSING the existing engine functions
(never duplicating them):

  (1) KNOWLEDGE PACKS -- export_pack()/import_pack(). A versioned (schema_version)
      export of the committed knowledge artifacts (ingest.ARTIFACTS incl.
      project-facts.md + ingest.INDEX_FILE) into one portable JSON document, and
      a SAFE import that MERGES into a target knowledge dir WITHOUT silently
      clobbering a locally-corrected artifact (a divergent local copy is kept and
      reported as a conflict; an explicit opt-in force=True overrides). This is
      the same anti-hallucination respect for user-corrected knowledge the rest
      of APIA applies.

  (2) ORG/TEAM PROFILES -- load_profile()/apply_profile(). A committable profile
      file layered onto the existing config.yml structure with a documented
      precedence where LOCAL config ALWAYS wins (the profile only fills in keys
      the local config does not set). No new top-level config concepts are
      invented; the profile uses the SAME keys as .apia/config.yml.

  (3) PLUGIN LOADER -- discover_plugins()/load_plugins(). Discovers team-provided
      LOCAL plugin files (adapters / scanners / shapes) under a committable
      ``.apia/plugins/`` directory and exposes a loader the existing engines
      consult. A MALFORMED plugin is captured in ``errors`` and skipped -- the
      loader NEVER raises. No download / fetch / marketplace: strictly local
      files already committed to the repo.

  (4) CAPABILITY MANIFEST -- build_manifest(). A machine-readable description of
      this install (adapters present + complete-vs-stub, available scanners,
      supported shapes, loaded plugins incl. any errors, versions) REUSING
      ingest.doctor() + scan/scaffold detection.

HARD CONSTRAINT (identical to ingest.py / scan.py / scaffold.py / verify.py; see
docs/ARCHITECTURE.md sections 1-3): this module performs ZERO network and ZERO
LLM/model calls. It NEVER imports any HTTP client, networking SDK, or model SDK
(no requests / urllib networking / openai / anthropic / httpx / low-level HTTP /
raw sockets), and it NEVER reaches a remote endpoint. Model inference always runs
on the user's existing Copilot session. The dependency surface is intentionally
minimal: Python standard library plus optional PyYAML (used only to read the
declarative config / profile / adapter YAML, exactly as the other engines do).

    # assert: no network/LLM calls anywhere in this module -- it only reads and
    # writes LOCAL files already committed to (or destined for) the git repo.

Usage:
    python3 .apia/scripts/share.py --export-pack --repo . --out pack.json
    python3 .apia/scripts/share.py --import-pack --pack pack.json [--force]
    python3 .apia/scripts/share.py --apply-profile --profile PROFILE [--repo .]
    python3 .apia/scripts/share.py --manifest --repo . [--json]
    python3 .apia/scripts/share.py --plugins --repo . [--json]
    python3 .apia/scripts/share.py --doctor [--repo .]

EXIT CODE (import): exit 0 whenever a KNOWN pack version imports, INCLUDING when
a divergent local artifact is kept as a preserved local correction -- that is a
SUCCESS, not a failure (the safe no-clobber merge did its job). Preserved
corrections are surfaced in the report / --json output (``conflicts`` +
``skipped_local``), not via the exit code. The import exits NON-ZERO only when
the pack's ``schema_version`` is unknown/newer and nothing was applied. (The
other actions exit 0 on success; --doctor exits non-zero when a required tool is
missing, mirroring ingest.doctor.)
"""

# NOTE: standard-library only (plus the optional PyYAML import guarded below for
# reading the declarative config / profile / adapter YAML). There is deliberately
# NO networking/LLM import here -- see the module docstring's HARD CONSTRAINT.
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# ingest / scan / scaffold are the SHARED off-model building blocks this engine
# COMPOSES (never reimplements): ingest supplies the knowledge-artifact list +
# index-file name + default knowledge dir + the shared doctor() preflight; scan
# supplies adapter loading + stack->adapter resolution + the scanner presence
# report; scaffold supplies the supported-shapes resolution. The scripts dir is
# this file's directory; make sure it is importable regardless of the caller's
# cwd (mirrors scan.py / scaffold.py / verify.py). These are LOCAL stdlib-only
# modules that perform no network/LLM calls (enforced by their own guard tests +
# the one for this module).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    import ingest  # noqa: E402  (ARTIFACTS/INDEX_FILE/default_knowledge_dir/doctor)
except ImportError:
    ingest = None
try:
    import scan  # noqa: E402  (load_adapter/_adapter_path_for_stack/scanner_tools_report)
except ImportError:
    scan = None
try:
    import scaffold  # noqa: E402  (resolve_supported_shapes)
except ImportError:
    scaffold = None


# The knowledge-pack document schema version. Bump when the pack layout changes
# in a backward-incompatible way. import_pack() KNOWS this version (imports it)
# and REPORTS -- never silently applies -- any newer/unknown version.
PACK_SCHEMA_VERSION = "1"

# The APIA sharing engine's own version, surfaced in the capability manifest.
APIA_VERSION = "6a"
MANIFEST_SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------
def _utc_now():
    """Return an ISO-8601 UTC timestamp (deterministic format, no locale)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text):
    """Return a short sha256 hex digest of a text artifact's bytes.

    Mirrors ingest.file_hash's digest width (16 hex chars) so a pack hash and an
    ingest index hash are directly comparable.
    """
    if text is None:
        text = ""
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


def _apia_dir():
    """Return the APIA package .apia/ dir (the parent of this scripts dir)."""
    return os.path.dirname(_SCRIPTS_DIR)


def _package_config_path():
    return os.path.join(_apia_dir(), "config.yml")


def _load_yaml(path):
    """Load a YAML mapping from ``path`` (optional PyYAML), or {} on any failure.

    Pure local file read; never raises on a missing file / unreadable file / bad
    YAML / absent PyYAML -- returns {} so callers degrade gracefully.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError, ValueError):
        # A malformed plugin file (bad YAML) must NEVER crash the loader; the
        # validator turns the empty {} into a captured error. yaml.YAMLError
        # covers all PyYAML parse failures (ParserError/ScannerError/etc.).
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# (1) KNOWLEDGE PACKS -- versioned export/import with a safe no-clobber merge.
# ---------------------------------------------------------------------------
def _knowledge_artifact_names():
    """Return the knowledge artifact filenames to pack (REUSE ingest.ARTIFACTS)."""
    if ingest is not None:
        return list(ingest.ARTIFACTS)
    # Defensive fallback mirroring ingest.ARTIFACTS so the engine still runs if
    # ingest somehow failed to import; the real list always comes from ingest.
    return [
        "project-map.md", "architecture.md", "dependencies.md", "pipelines.md",
        "workflows.md", "test-inventory.md", "stack.md", "project-facts.md",
    ]


def _index_file_name():
    """Return the knowledge index filename (REUSE ingest.INDEX_FILE)."""
    if ingest is not None:
        return ingest.INDEX_FILE
    return ".index.json"


def default_knowledge_dir():
    """Return the default knowledge dir (REUSE ingest.default_knowledge_dir())."""
    if ingest is not None:
        return ingest.default_knowledge_dir()
    return os.path.join(_apia_dir(), "knowledge")


def export_pack(knowledge_dir=None, out_path=None, source_repo=None):
    """Export the committed knowledge artifacts into one versioned pack document.

    Reads each ingest.ARTIFACTS file (the 8 committed knowledge artifacts incl.
    project-facts.md) plus ingest.INDEX_FILE ('.index.json') from
    ``knowledge_dir`` (default: ingest.default_knowledge_dir()) and emits a single
    portable pack dict with a top-level ``schema_version``, the source repo name,
    a generated_at timestamp, and per-artifact {content, hash}. Only artifacts
    that exist on disk are included (a partially-ingested knowledge dir packs what
    it has). When ``out_path`` is given the pack is also written there as JSON.

    Returns the pack dict (JSON-serializable). Pure local read/write; no network.
    """
    knowledge_dir = knowledge_dir or default_knowledge_dir()
    if source_repo is None:
        # The source repo name is the git repo root two levels above .apia/
        # knowledge (knowledge_dir -> .apia -> repo). Best-effort basename.
        source_repo = os.path.basename(
            os.path.dirname(os.path.dirname(os.path.abspath(knowledge_dir))))

    artifacts = {}
    for name in _knowledge_artifact_names():
        full = os.path.join(knowledge_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        artifacts[name] = {"content": content, "hash": _content_hash(content)}

    # The committed index is carried verbatim (as parsed JSON) so an importer can
    # compare a local artifact against what the pack's producer recorded.
    index = {}
    index_full = os.path.join(knowledge_dir, _index_file_name())
    if os.path.isfile(index_full):
        try:
            with open(index_full, "r", encoding="utf-8") as fh:
                index = json.load(fh)
        except (OSError, ValueError):
            index = {}

    pack = {
        "schema_version": PACK_SCHEMA_VERSION,
        "source_repo": source_repo,
        "generated_at": _utc_now(),
        "artifacts": artifacts,
        "index": index,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(pack, fh, indent=2, sort_keys=True)
    return pack


def load_pack(pack_path):
    """Load a pack JSON document from disk. Returns the dict (may raise on IO)."""
    with open(pack_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def import_pack(pack, knowledge_dir=None, options=None):
    """Import a knowledge pack into ``knowledge_dir`` with a SAFE no-clobber merge.

    ``pack`` is a pack dict (from export_pack / load_pack) or a path to a pack
    JSON file. ``options`` may set ``force=True`` (NOT the default) to override a
    locally-corrected artifact.

    VERSION HANDLING: the pack's ``schema_version`` is validated against the
    KNOWN PACK_SCHEMA_VERSION. A known version imports. An unknown/newer version
    is REPORTED (``unknown_version: True``) and NOT applied -- never silently
    forced -- so a newer pack layout cannot corrupt an older install.

    SAFE MERGE: for each artifact, if the target file already exists AND its
    on-disk content diverges from what the pack recorded (its own content hash),
    the LOCAL copy is a local correction: it is KEPT, recorded in ``conflicts``
    (and ``skipped_local``), and surfaced -- never silently clobbered. A missing
    or byte-identical target is written/overwritten freely. ``force=True`` writes
    the pack copy over a divergent local file (recorded in ``forced``).

    INDEX: the pack's recorded index is written ONLY when the import actually
    applied pack content (at least one artifact written/forced, or a pure
    re-import where every artifact was already byte-identical). An all-conflict
    import -- every artifact a preserved local correction, nothing applied --
    leaves any existing local ``.index.json`` UNTOUCHED, so the on-disk index
    never describes pack content that was never written.

    EXIT/RESULT: a known-version import is a SUCCESS even when a local correction
    is kept; the preserved correction is surfaced in ``conflicts`` /
    ``skipped_local`` here, not via a failure signal (the CLI exits 0). Only an
    unknown version (nothing applied) is a non-success.

    Returns a structured result::

        {schema_version, unknown_version: bool, imported: [...], skipped_local:
         [...], conflicts: [{artifact, local_hash, pack_hash}], forced: [...],
         index_written: bool}

    Pure local read/write; no network.
    """
    options = options or {}
    force = bool(options.get("force"))
    if isinstance(pack, str):
        pack = load_pack(pack)
    knowledge_dir = knowledge_dir or default_knowledge_dir()

    schema_version = str(pack.get("schema_version", ""))
    result = {
        "schema_version": schema_version,
        "unknown_version": False,
        "imported": [],
        "skipped_local": [],
        "conflicts": [],
        "forced": [],
        "index_written": False,
    }

    if schema_version != PACK_SCHEMA_VERSION:
        # Forward/backward aware: an unknown/newer version is REPORTED, not
        # applied. We do not guess how to translate a layout we do not know.
        result["unknown_version"] = True
        return result

    os.makedirs(knowledge_dir, exist_ok=True)
    artifacts = pack.get("artifacts") or {}
    # Did we actually APPLY pack content to disk (a real write or a force)? If
    # every artifact was a preserved local correction, nothing was written and
    # the on-disk index must be left untouched (see the index-gating below). A
    # byte-identical artifact needs no write but IS consistent with disk, so a
    # pure re-import still (re)writes the matching index.
    wrote_from_pack = False
    identical_present = False
    for name in sorted(artifacts):
        entry = artifacts[name] or {}
        pack_content = entry.get("content", "")
        pack_hash = entry.get("hash") or _content_hash(pack_content)
        target = os.path.join(knowledge_dir, name)

        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as fh:
                    local_content = fh.read()
            except OSError:
                local_content = None
            local_hash = _content_hash(local_content) if local_content is not None else None
            if local_hash == pack_hash:
                # Byte-identical: nothing to write, but it IS present as expected
                # so the pack's index stays consistent with what is on disk.
                result["imported"].append(name)
                identical_present = True
                continue
            # Divergent local copy == a local correction.
            if not force:
                result["conflicts"].append({
                    "artifact": name,
                    "local_hash": local_hash,
                    "pack_hash": pack_hash,
                })
                result["skipped_local"].append(name)
                continue
            # Explicit opt-in override.
            result["forced"].append(name)

        with open(target, "w", encoding="utf-8") as fh:
            fh.write(pack_content)
        wrote_from_pack = True
        if name not in result["imported"]:
            result["imported"].append(name)

    # The index (ingest's {source_path: hash} change oracle) is advisory
    # metadata; write it so a subsequent incremental ingest sees the imported
    # artifacts' recorded source hashes. GATING: only when the import actually
    # brought the knowledge dir into agreement with the pack -- at least one
    # artifact freshly written/forced, OR every artifact already byte-identical
    # (a pure re-import) -- do we touch the on-disk index. An ALL-CONFLICT import
    # (every artifact a preserved local correction, nothing applied) writes
    # NOTHING, so it must NOT overwrite a divergent local .index.json with a pack
    # index describing content that was never applied.
    index = pack.get("index")
    applied_any = wrote_from_pack or (identical_present and not result["skipped_local"])
    if isinstance(index, dict) and index and applied_any:
        index_full = os.path.join(knowledge_dir, _index_file_name())
        try:
            with open(index_full, "w", encoding="utf-8") as fh:
                json.dump(index, fh, indent=2, sort_keys=True)
            result["index_written"] = True
        except OSError:
            result["index_written"] = False

    result["imported"] = sorted(result["imported"])
    result["skipped_local"] = sorted(result["skipped_local"])
    result["forced"] = sorted(result["forced"])
    return result


# ---------------------------------------------------------------------------
# (2) ORG/TEAM PROFILES -- layered onto config.yml, LOCAL overrides win.
# ---------------------------------------------------------------------------
# A committable org/team profile lives at .apia/profile.yml (an example ships as
# .apia/profiles/example-team.yml). It uses the SAME keys as .apia/config.yml
# (review.thresholds, eval_data, generate, orchestrate, token_budget,
# confidence_threshold, stacks); NO new top-level config concepts are invented.
# PRECEDENCE: the profile is deep-merged UNDER the local config -- a key present
# locally WINS; a key absent locally is FILLED IN from the profile.
DEFAULT_PROFILE_PATH = "profile.yml"
EXAMPLE_PROFILE_REL = os.path.join("profiles", "example-team.yml")


def load_profile(path=None, repo_path=None):
    """Load an org/team profile mapping from ``path`` (optional PyYAML), or {}.

    When ``path`` is None, resolve REPO-FIRST: the target repo's own
    ``.apia/profile.yml`` wins, then the APIA package's ``.apia/profile.yml``.
    Returns {} when no profile exists (so apply_profile is a no-op). Pure local
    read; never raises.
    """
    if path:
        return _load_yaml(path)
    candidates = []
    if repo_path:
        candidates.append(os.path.join(repo_path, ".apia", DEFAULT_PROFILE_PATH))
    candidates.append(os.path.join(_apia_dir(), DEFAULT_PROFILE_PATH))
    for cand in candidates:
        if os.path.isfile(cand):
            return _load_yaml(cand)
    return {}


def _deep_merge_local_wins(profile, local, provenance=None, prefix=""):
    """Deep-merge ``profile`` UNDER ``local`` so LOCAL keys always win.

    Returns a new merged dict. Where both sides hold a mapping the merge recurses;
    where a key exists only in the profile it is filled in; where it exists in
    local (even as a different type) the LOCAL value wins outright. ``provenance``
    (a dict) records the dotted key path -> "local" | "profile" for leaf keys, so
    the manifest/report can show which keys came from where.
    """
    if provenance is None:
        provenance = {}
    merged = {}
    keys = list(local.keys()) + [k for k in profile.keys() if k not in local]
    for key in keys:
        dotted = "{}.{}".format(prefix, key) if prefix else str(key)
        in_local = key in local
        in_profile = key in profile
        lval = local.get(key)
        pval = profile.get(key)
        if in_local and in_profile and isinstance(lval, dict) and isinstance(pval, dict):
            merged[key] = _deep_merge_local_wins(pval, lval, provenance, dotted)
        elif in_local:
            merged[key] = lval
            provenance[dotted] = "local"
        else:
            merged[key] = pval
            provenance[dotted] = "profile"
    return merged


def apply_profile(profile, local_config):
    """Return the EFFECTIVE merged config and a provenance map.

    Deep-merges ``profile`` UNDER ``local_config`` so LOCAL overrides win (a key
    present locally wins; absent -> the profile fills it in). Consistent with the
    existing config.yml keys; no new top-level concepts. Returns
    ``(effective_config, provenance)`` where provenance maps a dotted key path to
    "local" or "profile" for the manifest/report. Never mutates its inputs.
    """
    profile = profile or {}
    local_config = local_config or {}
    provenance = {}
    effective = _deep_merge_local_wins(profile, local_config, provenance)
    return effective, provenance


def effective_config(repo_path=None, profile_path=None):
    """Return (effective_config, provenance) for a repo: profile under local config.

    Loads the local config (REPO-FIRST, package fallback -- the SAME resolution
    scaffold._load_config uses) and the org/team profile, then applies the
    profile UNDER the local config. Convenience wrapper for the CLI/manifest.
    """
    local_config = {}
    if scaffold is not None:
        local_config = scaffold._load_config(repo_path) or {}
    else:
        local_config = _load_yaml(
            os.path.join(repo_path, ".apia", "config.yml") if repo_path else
            _package_config_path())
    profile = load_profile(profile_path, repo_path=repo_path)
    return apply_profile(profile, local_config)


# ---------------------------------------------------------------------------
# (3) PLUGIN LOADER -- LOCAL adapters/scanners/shapes under .apia/plugins/.
# ---------------------------------------------------------------------------
# Team-provided LOCAL plugins live under a committable ``.apia/plugins/`` dir
# (REPO-FIRST: the target repo's own .apia/plugins/ is consulted, then the APIA
# package's .apia/plugins/). The layout is:
#   .apia/plugins/adapters/<stack>.yml   a new/overriding stack adapter YAML
#                                        (same schema as .apia/adapters/*.yml)
#   .apia/plugins/scanners/<name>.yml    a scanner plugin that EXTENDS an
#                                        adapter's scanners: contract, keyed by
#                                        the target `stack` + `category` + a
#                                        scanner_def {tool, command, format}
#   .apia/plugins/shapes/<name>.yml      a shape plugin naming a new `shape` id
#                                        (consistent with scaffold's contract)
# A MALFORMED plugin (bad YAML / missing required keys) is captured in `errors`
# and skipped; the loader NEVER raises and NEVER downloads anything.
PLUGINS_DIR = "plugins"
_PLUGIN_SUBDIRS = ("adapters", "scanners", "shapes")


def _plugin_roots(repo_path=None):
    """Return the plugin root dirs to scan, REPO-FIRST then package.

    A repo may ship its own .apia/plugins/; the APIA package also ships one (for
    the committed example plugin). Both are scanned so an example in the package
    is always discoverable while a consuming repo can add its own.
    """
    roots = []
    seen = set()

    def _add(path):
        # Dedupe by real path so a repo whose .apia/ IS the package dir (e.g. the
        # APIA repo itself) does not scan the same plugins twice.
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            roots.append(path)

    if repo_path:
        _add(os.path.join(repo_path, ".apia", PLUGINS_DIR))
    _add(os.path.join(_apia_dir(), PLUGINS_DIR))
    return roots


def _iter_plugin_files(root, subdir):
    """Yield absolute .yml/.yaml plugin files under root/subdir (sorted), if any."""
    base = os.path.join(root, subdir)
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        if name.startswith("."):
            continue
        if name.endswith(".yml") or name.endswith(".yaml"):
            yield os.path.join(base, name)


def discover_plugins(repo_path=None):
    """Discover LOCAL plugin files under .apia/plugins/ WITHOUT loading them.

    Returns a catalog of discovered file paths grouped by kind::

        {adapters: [path...], scanners: [path...], shapes: [path...]}

    Pure directory listing; nothing is parsed or executed here. The optional
    plugin dir simply yields empty lists when absent (so every engine behaves
    exactly as before when no plugins are present).
    """
    catalog = {"adapters": [], "scanners": [], "shapes": []}
    for root in _plugin_roots(repo_path):
        for subdir in _PLUGIN_SUBDIRS:
            for path in _iter_plugin_files(root, subdir):
                catalog[subdir].append(path)
    return catalog


def _validate_adapter_plugin(data):
    """Return an error string if an adapter plugin is malformed, else None.

    A minimally valid adapter plugin names its stack (``name``). Keeping the bar
    low matches the adapter schema (stubs are valid adapters) while still
    rejecting an empty / non-mapping / nameless file.
    """
    if not isinstance(data, dict) or not data:
        return "adapter plugin is empty or not a mapping"
    if not data.get("name"):
        return "adapter plugin missing required 'name' key"
    return None


def _validate_scanner_plugin(data):
    """Return an error string if a scanner plugin is malformed, else None.

    A scanner plugin declares the target ``stack``, a ``category``
    (security|quality|perf) and a ``scanner`` def {tool, command, format} that
    extends that adapter's scanners: contract.
    """
    if not isinstance(data, dict) or not data:
        return "scanner plugin is empty or not a mapping"
    if not data.get("stack"):
        return "scanner plugin missing required 'stack' key"
    if not data.get("category"):
        return "scanner plugin missing required 'category' key"
    scanner = data.get("scanner")
    if not isinstance(scanner, dict):
        return "scanner plugin missing required 'scanner' mapping"
    for req in ("tool", "command", "format"):
        if not scanner.get(req):
            return "scanner plugin 'scanner' missing required '{}'".format(req)
    return None


def _validate_shape_plugin(data):
    """Return an error string if a shape plugin is malformed, else None.

    A shape plugin names a new ``shape`` id consistent with scaffold's contract.
    """
    if not isinstance(data, dict) or not data:
        return "shape plugin is empty or not a mapping"
    if not data.get("shape"):
        return "shape plugin missing required 'shape' key"
    return None


def load_plugins(repo_path=None):
    """Load + validate the discovered LOCAL plugins into a structured catalog.

    Returns::

        {
          adapters: [{name, path, data}...],
          scanners: [{stack, category, scanner, name, path}...],
          shapes:   [{shape, stack, name, path, data}...],
          errors:   [{path, kind, error}...],
        }

    A MALFORMED plugin (bad YAML / missing required keys / unreadable) is captured
    in ``errors`` and skipped; this function NEVER raises. No network/download:
    only LOCAL committed files are read.
    """
    catalog = {"adapters": [], "scanners": [], "shapes": [], "errors": []}
    discovered = discover_plugins(repo_path)

    for path in discovered["adapters"]:
        data = _load_yaml(path)
        err = _validate_adapter_plugin(data)
        if err:
            catalog["errors"].append({"path": path, "kind": "adapter", "error": err})
            continue
        catalog["adapters"].append({
            "name": data.get("name"),
            "path": path,
            "data": data,
        })

    for path in discovered["scanners"]:
        data = _load_yaml(path)
        err = _validate_scanner_plugin(data)
        if err:
            catalog["errors"].append({"path": path, "kind": "scanner", "error": err})
            continue
        catalog["scanners"].append({
            "stack": data.get("stack"),
            "category": data.get("category"),
            "scanner": data.get("scanner"),
            "name": data.get("scanner", {}).get("tool"),
            "path": path,
        })

    for path in discovered["shapes"]:
        data = _load_yaml(path)
        err = _validate_shape_plugin(data)
        if err:
            catalog["errors"].append({"path": path, "kind": "shape", "error": err})
            continue
        catalog["shapes"].append({
            "shape": data.get("shape"),
            "stack": data.get("stack"),
            "name": data.get("shape"),
            "path": path,
            "data": data,
        })

    return catalog


# --- the shared helpers the engines import (REUSE, do not fork) --------------
def plugin_adapter_path(stack_name, repo_path=None):
    """Return a LOCAL plugin adapter YAML path for ``stack_name``, or None.

    The single shared helper the engines (scan / verify / ingest) consult to find
    a team-provided adapter. Additive: returns None when no plugin provides the
    stack, so the caller's existing resolution is untouched.
    """
    if not stack_name:
        return None
    for entry in load_plugins(repo_path)["adapters"]:
        if entry.get("name") == stack_name:
            return entry.get("path")
    return None


def plugin_adapter_names(repo_path=None):
    """Return the set of stack names provided by LOCAL adapter plugins."""
    return {e.get("name") for e in load_plugins(repo_path)["adapters"] if e.get("name")}


def plugin_scanner_defs(stack_name, repo_path=None, category="all"):
    """Return LOCAL plugin scanner defs for ``stack_name`` as (category, def) pairs.

    The shared helper scan.py consults to pick up a plugin scanner. Additive:
    returns [] when no plugin extends the stack, so the adapter's own scanners
    enumeration is unchanged when no plugins are present.
    """
    pairs = []
    for entry in load_plugins(repo_path)["scanners"]:
        if entry.get("stack") != stack_name:
            continue
        cat = entry.get("category")
        if category != "all" and cat != category:
            continue
        scanner = entry.get("scanner")
        if isinstance(scanner, dict):
            pairs.append((cat, scanner))
    return pairs


def plugin_shape_ids(repo_path=None):
    """Return the list of shape ids provided by LOCAL shape plugins.

    The shared helper scaffold.resolve_supported_shapes consults. Additive:
    returns [] when no shape plugins are present.
    """
    ids = []
    for entry in load_plugins(repo_path)["shapes"]:
        shape = entry.get("shape")
        if shape and shape not in ids:
            ids.append(shape)
    return ids


# ---------------------------------------------------------------------------
# (4) CAPABILITY MANIFEST -- REUSE ingest.doctor + scan/scaffold detection.
# ---------------------------------------------------------------------------
# A declared adapter is COMPLETE when it carries the real sections (a non-null
# test_command AND a scanners: section); a stub otherwise (csharp/groovy).
def _adapter_completeness(adapter_data):
    """Return 'complete' | 'stub' for a loaded adapter mapping."""
    if not isinstance(adapter_data, dict) or not adapter_data:
        return "stub"
    has_test = bool(adapter_data.get("test_command"))
    has_scanners = bool(adapter_data.get("scanners"))
    return "complete" if (has_test and has_scanners) else "stub"


def _config_stacks(repo_path=None):
    """Return the stacks list from config (REUSE scan._config_stacks when present)."""
    if scan is not None:
        return scan._config_stacks(repo_path)
    cfg = _load_yaml(
        os.path.join(repo_path, ".apia", "config.yml") if repo_path else
        _package_config_path())
    stacks = cfg.get("stacks")
    return stacks if isinstance(stacks, list) else []


def build_manifest(repo_path=None, profile_path=None):
    """Build the machine-readable capability manifest for this install.

    REUSES ingest.doctor() (the shared offline preflight), scan.load_adapter +
    scan._adapter_path_for_stack + scan.scanner_tools_report (adapter resolution +
    scanner presence), and scaffold.resolve_supported_shapes (the supported-shape
    list, now plugin-aware). Returns a JSON-serializable dict::

        {
          apia_version, schema_version, repo,
          adapters: [{name, present, status(complete|stub), path, scanners:[...]}],
          scanners: {<tool>: present_bool, ...},   # union across adapters
          supported_shapes: [...],                 # incl. plugin shapes
          plugins: {adapters, scanners, shapes, errors},
          doctor: {...ingest.doctor() summary...},
          profile_applied: bool, provenance: {...},
        }

    No network/LLM.
    """
    manifest = {
        "apia_version": APIA_VERSION,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repo": os.path.basename(os.path.abspath(repo_path)) if repo_path else None,
        "adapters": [],
        "scanners": {},
        "supported_shapes": [],
        "plugins": {},
        "doctor": {},
        "profile_applied": False,
        "provenance": {},
    }

    # Adapters: iterate the config.yml stacks (python/cpp/csharp/groovy) and any
    # LOCAL adapter plugin, resolving each to its YAML and classifying complete
    # vs stub. REUSE scan's resolution so repo-first config wins.
    plugins = load_plugins(repo_path)
    manifest["plugins"] = plugins

    stack_entries = []  # (name, path)
    seen_stacks = set()
    for entry in _config_stacks(repo_path):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = entry["name"]
        seen_stacks.add(name)
        path = None
        if scan is not None:
            path = scan._adapter_path_for_stack(name, repo_path)
        # A LOCAL plugin adapter overrides / supplies the path.
        plugin_path = plugin_adapter_path(name, repo_path)
        if plugin_path:
            path = plugin_path
        stack_entries.append((name, path))
    # Plugin-only stacks (not declared in config.yml).
    for entry in plugins["adapters"]:
        name = entry.get("name")
        if name and name not in seen_stacks:
            seen_stacks.add(name)
            stack_entries.append((name, entry.get("path")))

    scanner_union = {}
    for name, path in stack_entries:
        present = bool(path and os.path.isfile(path))
        adapter_data = scan.load_adapter(path) if (scan is not None and present) else {}
        status = _adapter_completeness(adapter_data) if present else "stub"
        scanners_list = []
        if present and adapter_data.get("scanners") and scan is not None:
            report = scan.scanner_tools_report(adapter_data)
            for t in report["scanners"]:
                scanners_list.append({
                    "tool": t["tool"], "present": t["present"],
                    "format": t["format"], "category": t["category"],
                })
                scanner_union[t["tool"]] = scanner_union.get(t["tool"], False) or t["present"]
        # Fold in any LOCAL plugin scanners for this stack.
        for cat, sdef in plugin_scanner_defs(name, repo_path):
            tool = sdef.get("tool", "?")
            present_tool = scan._tool_present(sdef) if scan is not None else False
            scanners_list.append({
                "tool": tool, "present": present_tool,
                "format": sdef.get("format"), "category": cat, "plugin": True,
            })
            scanner_union[tool] = scanner_union.get(tool, False) or present_tool
        manifest["adapters"].append({
            "name": name, "present": present, "status": status,
            "path": path, "scanners": scanners_list,
        })
    manifest["scanners"] = scanner_union

    # Supported shapes (plugin-aware: scaffold.resolve_supported_shapes is wired
    # to include plugin shape ids in FEAT-002).
    if scaffold is not None:
        manifest["supported_shapes"] = list(scaffold.resolve_supported_shapes(repo_path))
    else:
        manifest["supported_shapes"] = list(plugin_shape_ids(repo_path))

    # Doctor preflight (REUSE ingest.doctor()).
    if ingest is not None:
        report = ingest.doctor(repo_path=(
            repo_path if (repo_path and os.path.isdir(repo_path)) else None))
        manifest["doctor"] = {
            "missing_required": report.get("missing_required", []),
            "warnings": report.get("warnings", []),
            "required_categories": report.get("required_categories", []),
        }

    # Effective config provenance (which keys the org profile would fill in).
    _effective, provenance = effective_config(repo_path, profile_path)
    manifest["profile_applied"] = bool(provenance) and any(
        v == "profile" for v in provenance.values())
    manifest["provenance"] = provenance
    return manifest


def format_manifest(manifest):
    """Render a capability manifest as a compact human string (doctor-style)."""
    lines = ["APIA capability manifest (local, off-model)", ""]
    lines.append("  apia version:   {}".format(manifest.get("apia_version")))
    lines.append("  schema version: {}".format(manifest.get("schema_version")))
    if manifest.get("repo"):
        lines.append("  repo:           {}".format(manifest["repo"]))
    lines.append("")
    lines.append("Adapters:")
    for a in manifest.get("adapters", []):
        mark = "ok " if a["present"] else "MISS"
        plugin_tag = ""
        lines.append("  [{}] {:<8} {:<8} ({} scanner(s)){}".format(
            mark, a["name"], a["status"], len(a.get("scanners", [])), plugin_tag))
    lines.append("")
    scanners = manifest.get("scanners", {})
    if scanners:
        lines.append("Scanners (union across adapters):")
        for tool in sorted(scanners):
            mark = "ok " if scanners[tool] else "MISS"
            lines.append("  [{}] {}".format(mark, tool))
        lines.append("")
    lines.append("Supported shapes: {}".format(
        ", ".join(manifest.get("supported_shapes", [])) or "none"))
    plugins = manifest.get("plugins", {})
    lines.append("")
    lines.append("Loaded plugins: {} adapter(s), {} scanner(s), {} shape(s), "
                 "{} error(s)".format(
                     len(plugins.get("adapters", [])),
                     len(plugins.get("scanners", [])),
                     len(plugins.get("shapes", [])),
                     len(plugins.get("errors", []))))
    for err in plugins.get("errors", []):
        lines.append("  [plugin error] {}: {}".format(
            os.path.basename(err.get("path", "?")), err.get("error")))
    doctor = manifest.get("doctor", {})
    if doctor:
        missing = doctor.get("missing_required", [])
        lines.append("")
        if missing:
            lines.append("Preflight: FAIL -- missing required tool(s): {}.".format(
                ", ".join(missing)))
        else:
            lines.append("Preflight: OK -- all required tools present.")
    if manifest.get("profile_applied"):
        filled = sorted(k for k, v in manifest.get("provenance", {}).items()
                        if v == "profile")
        lines.append("")
        lines.append("Org/team profile applied; profile-filled keys: {}".format(
            ", ".join(filled) or "none"))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA off-model LOCAL sharing engine (knowledge packs, org/team "
            "profiles, local plugins, capability manifest; no LLM/network calls)."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export-pack", dest="export_pack", action="store_true",
                        help="Export the knowledge artifacts into a versioned pack.")
    action.add_argument("--import-pack", dest="import_pack", action="store_true",
                        help="Import a knowledge pack with a safe no-clobber merge.")
    action.add_argument("--apply-profile", dest="apply_profile", action="store_true",
                        help="Apply an org/team profile and print the effective config.")
    action.add_argument("--manifest", action="store_true",
                        help="Build the machine-readable capability manifest.")
    action.add_argument("--plugins", action="store_true",
                        help="List discovered LOCAL plugins (adapters/scanners/shapes).")
    action.add_argument("--doctor", action="store_true",
                        help="Run the shared offline toolchain preflight and exit.")

    parser.add_argument("--repo", default=None,
                        help="Path to the repository (default: current dir where relevant).")
    parser.add_argument("--knowledge-dir", dest="knowledge_dir", default=None,
                        help="Knowledge dir for export/import (default: package knowledge/).")
    parser.add_argument("--out", default=None,
                        help="Output path for --export-pack.")
    parser.add_argument("--pack", default=None,
                        help="Pack file path for --import-pack.")
    parser.add_argument("--profile", default=None,
                        help="Profile file path for --apply-profile/--manifest.")
    parser.add_argument("--force", action="store_true",
                        help="Opt-in override for --import-pack (clobber a divergent local copy).")
    parser.add_argument("--json", action="store_true",
                        help="Emit the machine-readable JSON structure.")
    args = parser.parse_args(argv)

    repo = args.repo if (args.repo and os.path.isdir(args.repo)) else None

    if args.doctor:
        if ingest is None:
            parser.error("doctor preflight unavailable: could not import ingest.py")
        report = ingest.doctor(repo_path=repo)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(ingest.format_doctor_report(report), end="")
        return 1 if report.get("missing_required") else 0

    if args.export_pack:
        pack = export_pack(knowledge_dir=args.knowledge_dir, out_path=args.out,
                           source_repo=(os.path.basename(os.path.abspath(repo)) if repo else None))
        if args.json:
            print(json.dumps(pack, indent=2, sort_keys=True))
        else:
            print("Exported {} artifact(s) (schema_version {}){}".format(
                len(pack["artifacts"]), pack["schema_version"],
                " -> {}".format(args.out) if args.out else ""))
        return 0

    if args.import_pack:
        if not args.pack:
            parser.error("--import-pack requires --pack PATH")
        result = import_pack(args.pack, knowledge_dir=args.knowledge_dir,
                             options={"force": args.force})
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            if result["unknown_version"]:
                print("Pack schema_version {!r} is unknown; NOT applied.".format(
                    result["schema_version"]))
            else:
                print("Imported {}; kept {} local correction(s){}.".format(
                    len(result["imported"]), len(result["skipped_local"]),
                    " (forced {})".format(len(result["forced"])) if result["forced"] else ""))
        # A KNOWN-version import exits 0 EVEN WHEN a local correction was kept:
        # a preserved conflict means the safe no-clobber merge worked, so it is a
        # success and the kept corrections are surfaced in the report/--json
        # (conflicts + skipped_local), not via the exit code. Only an
        # unknown/newer version (nothing applied) exits non-zero.
        if result["unknown_version"]:
            return 1
        return 0

    if args.apply_profile:
        eff, provenance = effective_config(repo, args.profile)
        if args.json:
            print(json.dumps({"effective_config": eff, "provenance": provenance},
                             indent=2, sort_keys=True))
        else:
            filled = sorted(k for k, v in provenance.items() if v == "profile")
            print("Effective config computed; profile-filled keys: {}".format(
                ", ".join(filled) or "none (local config overrides everything)"))
        return 0

    if args.plugins:
        catalog = load_plugins(repo)
        if args.json:
            print(json.dumps(catalog, indent=2, sort_keys=True))
        else:
            print("Discovered plugins: {} adapter(s), {} scanner(s), {} shape(s), "
                  "{} error(s)".format(
                      len(catalog["adapters"]), len(catalog["scanners"]),
                      len(catalog["shapes"]), len(catalog["errors"])))
            for err in catalog["errors"]:
                print("  [error] {}: {}".format(err["path"], err["error"]))
        return 0

    if args.manifest:
        manifest = build_manifest(repo, args.profile)
        if args.json:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        else:
            print(format_manifest(manifest), end="")
        return 0

    parser.error("no action specified")  # pragma: no cover (group is required)


if __name__ == "__main__":
    sys.exit(main())
