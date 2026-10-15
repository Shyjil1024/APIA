#!/usr/bin/env python3
"""APIA effectiveness-measurement harness (``apia-eval``) — the one-command runner.

This is the benchmark runner over the ``.apia/eval/`` corpus (design.md §4). It
discovers the declared scenarios, dispatches each to the EXISTING deterministic
engines IN-PROCESS (``scan`` / ``evaldata`` / ``ingest``; ``verify`` is wired but
unused by the initial 7 scenarios), applies a PURE scorer, aggregates with
scored-only denominators and the degraded-headline rule, and emits a stable JSON
schema (``schema_version`` 1) plus a ``scan.format_human``-style summary.

The harness REUSES the engines' public functions and reimplements none of their
gate/verify/detect/ingest logic — it only reads the dicts they return. The only
genuinely new logic is scoring: comparing an engine's structured output against a
scenario's pre-declared ``expected`` block. It is fully offline/deterministic:
NO network, NO LLM. It measures the deterministic tooling on fixed inputs only
and makes NO claim about AI/LLM-generated-code quality.
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import tempfile

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import scan        # noqa: E402  reused public API (gate + findings report)
import verify      # noqa: E402  reused public API (reserved verify branch)
import evaldata    # noqa: E402  reused public API (mine_golden/compare_artifact)
import ingest      # noqa: E402  reused public API (ingest + .index.json sources)

try:
    import yaml
except ImportError as _exc:  # pragma: no cover - PyYAML is a hard dep of the engines
    yaml = None
    _YAML_IMPORT_ERROR = _exc

SCHEMA_VERSION = 1

# Repo root is two dirs up from .apia/scripts/ (.apia/scripts -> .apia -> root).
REPO_ROOT = os.path.dirname(os.path.dirname(_SCRIPTS_DIR))

SCENARIOS_GLOB = os.path.join(REPO_ROOT, ".apia", "eval", "scenarios", "*", "scenario.yml")
DEFAULT_OUT = os.path.join(".apia", "eval", "baseline", "latest.json")

SCOPE_NOTE = (
    "Measures APIA's deterministic tooling (gate/defect-detection/verify/ingest) "
    "on fixed inputs only. Makes NO claim about AI/LLM-generated-code quality. "
    "Fully offline; no network, no LLM."
)

ENGINES = ("scan", "verify", "evaldata", "ingest")

# Closed assertion vocabulary per engine (unknown keys => fail-loud at load).
ASSERTION_KEYS = {
    "scan": {"gate_status", "incomplete_includes", "rules_present", "rules_absent"},
    "evaldata": {"golden_all_match", "mismatch_count", "mismatch_artifact"},
    "ingest": {"apia_paths_absent", "apia_paths_present", "test_count_delta"},
    "verify": set(),
}

# The gate scenarios whose joint scoring determines the FULL vs DEGRADED headline.
GATE_SCENARIO_IDS = (
    "gate_fail_pyapp",
    "gate_pass_clean",
    "gate_incomplete_missing_scanner",
)

# Tools probed at runtime for the tool_env block. Python-module tools are probed
# the way the gate itself probes them (scan._tool_present on a `python -m` def);
# native tools via shutil.which — so the harness's skip decision never disagrees
# with the gate's coverage decision (design §11 R6).
_PY_MODULE_TOOLS = {
    "bandit": "bandit",
    "pip-audit": "pip_audit",
    "ruff": "ruff",
    "flake8": "flake8",
    "radon": "radon",
}
_NATIVE_TOOLS = ("cppcheck", "clang-tidy")


class ManifestError(Exception):
    """A fatal manifest/usage error (exit 2); carries a human message."""


# ---------------------------------------------------------------------------
# Tool presence (mirrors the gate's own availability check — design §11 R6).
# ---------------------------------------------------------------------------

def tool_present(tool):
    """Return True when ``tool`` is reachable the SAME way the gate decides it.

    ``python -m`` tools (bandit/pip-audit/ruff/flake8/radon) reuse
    ``scan._tool_present`` (importable in the interpreter the scanner runs under);
    native tools (cppcheck/clang-tidy) use ``shutil.which``. This keeps a skip
    decision aligned with the coverage decision the gate makes internally.
    """
    module = _PY_MODULE_TOOLS.get(tool)
    if module is not None:
        return scan._tool_present({"command": "python -m {} --version".format(module)})
    return shutil.which(tool) is not None


def build_tool_env():
    """Build the tool_env block from REAL runtime probes (never hardcoded)."""
    present = []
    absent = []
    for tool in list(_PY_MODULE_TOOLS.keys()) + list(_NATIVE_TOOLS):
        (present if tool_present(tool) else absent).append(tool)
    return {
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "scanners_present": present,
        "scanners_absent": absent,
    }


# ---------------------------------------------------------------------------
# Manifest discovery + validation (fail-loud; design §4.3, §8).
# ---------------------------------------------------------------------------

def discover_manifests():
    """Return the sorted list of scenario.yml paths (stable ordering)."""
    return sorted(glob.glob(SCENARIOS_GLOB))


def load_manifest(path):
    """Load + validate one scenario.yml. Fail-loud (ManifestError) naming field."""
    if yaml is None:  # pragma: no cover - PyYAML is a hard dep of the engines
        raise ManifestError("PyYAML is required to load scenarios: {}".format(_YAML_IMPORT_ERROR))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ManifestError("cannot read scenario file {}: {}".format(path, exc))
    except yaml.YAMLError as exc:
        raise ManifestError("invalid YAML in {}: {}".format(path, exc))
    if not isinstance(data, dict):
        raise ManifestError("{}: top-level must be a mapping".format(path))

    parent = os.path.basename(os.path.dirname(path))
    for field in ("id", "engine", "metric", "repo", "expected"):
        if field not in data:
            raise ManifestError("{}: missing required field '{}'".format(path, field))
    if data["id"] != parent:
        raise ManifestError(
            "{}: field 'id' ({!r}) must equal the parent dir name ({!r})".format(
                path, data["id"], parent))
    engine = data["engine"]
    if engine not in ENGINES:
        raise ManifestError(
            "{}: field 'engine' ({!r}) must be one of {}".format(path, engine, sorted(ENGINES)))
    expected = data["expected"]
    if not isinstance(expected, dict):
        raise ManifestError("{}: field 'expected' must be a mapping".format(path))
    allowed = ASSERTION_KEYS[engine]
    for key in expected:
        if key not in allowed:
            raise ManifestError(
                "{}: expected key {!r} not in the {} assertion vocabulary {}".format(
                    path, key, engine, sorted(allowed)))

    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise ManifestError("{}: field 'options' must be a mapping".format(path))
    tool_requirements = data.get("tool_requirements") or []
    if not isinstance(tool_requirements, list):
        raise ManifestError("{}: field 'tool_requirements' must be a list".format(path))

    if engine == "evaldata":
        producer = options.get("producer")
        if not producer or not isinstance(producer, str):
            raise ManifestError(
                "{}: engine 'evaldata' requires a non-empty options.producer string".format(path))
        perturb = options.get("perturb")
        if perturb is not None:
            if not isinstance(perturb, dict):
                raise ManifestError("{}: options.perturb must be a mapping".format(path))
            kind = perturb.get("kind")
            if kind not in ("json-digit",):
                raise ManifestError(
                    "{}: options.perturb.kind ({!r}) must be one of ['json-digit']".format(
                        path, kind))
            if not perturb.get("artifact"):
                raise ManifestError("{}: options.perturb.artifact is required".format(path))

    data["_path"] = path
    data["_dir"] = os.path.dirname(path)
    data["options"] = options
    data["tool_requirements"] = tool_requirements
    return data


def resolve_repo(manifest):
    """Resolve the manifest ``repo`` to an existing tree (abs path), fail-loud.

    The documented contract (design §4.3) is scenario-dir-relative: the fixture
    repos (``fixture`` / ``fixture/cleanpy``) and the samples-reuse repos
    (``../../../../samples/<tree>``) all resolve directly that way. As a
    defensive fallback (e.g. a manifest that encodes too few ``..`` segments),
    when the scenario-dir-relative path does not exist the path's non-``..`` tail
    is re-anchored at the repo root. A path that resolves nowhere is a fail-loud
    manifest error.
    """
    rel = manifest["repo"]
    local = os.path.abspath(os.path.join(manifest["_dir"], rel))
    if os.path.exists(local):
        return local
    tail = [p for p in rel.replace(os.sep, "/").split("/") if p not in ("", "..", ".")]
    anchored = os.path.abspath(os.path.join(REPO_ROOT, *tail))
    if os.path.exists(anchored):
        return anchored
    raise ManifestError(
        "{}: repo path {!r} resolves to neither {} nor {}".format(
            manifest["_path"], rel, local, anchored))


def resolve_adapter(manifest):
    """Build the adapter path from the stack NAME and load it to a dict."""
    adapter_path = os.path.join(
        REPO_ROOT, ".apia", "adapters", manifest["adapter"] + ".yml")
    if not os.path.isfile(adapter_path):
        raise ManifestError(
            "{}: adapter path does not exist: {}".format(manifest["_path"], adapter_path))
    return scan.load_adapter(adapter_path), adapter_path


# ---------------------------------------------------------------------------
# Tool-availability gating (design §4.5).
# ---------------------------------------------------------------------------

def check_requirements(tool_requirements):
    """Return a skip_reason string when the scenario is not scorable, else None.

    * A positive requirement that is ABSENT -> skip.
    * A negative '!tool' requirement whose tool is PRESENT -> skip (the declared
      ground truth no longer holds; keeps S3 honest rather than a false fail).
    """
    for req in tool_requirements:
        if req.startswith("!"):
            tool = req[1:]
            if tool_present(tool):
                return "negative requirement violated: '{}' is present".format(tool)
        else:
            if not tool_present(req):
                return "required tool '{}' not present".format(req)
    return None


# ---------------------------------------------------------------------------
# Engine invocation (in-process; reuse only — design §4.3).
# ---------------------------------------------------------------------------

def invoke_scan(manifest):
    """Run scan.scan and derive the scorable actual from the report dict."""
    repo = resolve_repo(manifest)
    adapter, _ = resolve_adapter(manifest)
    report = scan.scan(repo, adapter, manifest["options"])
    gate = report["gate"]
    return {
        "gate_status": gate.get("status"),
        "incomplete_reason": list(gate.get("incomplete_reason") or []),
        "rules": sorted({f["rule"] for f in report["findings"] if f.get("rule")}),
    }


def _copytree(src, dst):
    """Copy a fixture tree to a fresh dir (ignoring VCS/cache noise)."""
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"))


def _perturb_json_digit(path, artifact_rel):
    """Apply the mode-significant json-digit perturbation to a produced artifact.

    Increments the first record's ``prod`` integer by 1 (value-relative, not a
    hard-coded replacement — design §11 R4). Fails loud if the artifact or the
    targeted field is missing/non-integer so a drifted producer surfaces as an
    error rather than a silent still-match.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ManifestError(
            "perturb target {} is not a non-empty list of records".format(artifact_rel))
    if "prod" not in data[0] or not isinstance(data[0]["prod"], int):
        raise ManifestError(
            "perturb target {}: first record has no integer 'prod' field".format(artifact_rel))
    data[0]["prod"] = data[0]["prod"] + 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))


def invoke_evaldata(manifest):
    """Copy fixture to temp, run the declared producer shell-free, then mine goldens."""
    repo = resolve_repo(manifest)
    adapter, _ = resolve_adapter(manifest)
    producer = manifest["options"]["producer"]
    perturb = manifest["options"].get("perturb")

    tmp_root = tempfile.mkdtemp(prefix="apia-eval-")
    try:
        temp_repo = os.path.join(tmp_root, os.path.basename(repo.rstrip(os.sep)) or "repo")
        _copytree(repo, temp_repo)
        argv = scan._split_command(producer)
        rc, out, err = scan._run(argv, temp_repo)
        if rc != 0:
            raise RuntimeError(
                "producer {!r} exited {} in {}: {}".format(producer, rc, temp_repo, scan._tail(err)))
        if perturb and perturb.get("kind") == "json-digit":
            artifact_rel = perturb["artifact"]
            _perturb_json_digit(os.path.join(temp_repo, artifact_rel), artifact_rel)
        compares = evaldata.mine_golden(temp_repo, adapter)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    mismatches = [c for c in compares if not c.get("match")]
    return {
        "golden_all_match": bool(compares) and all(c.get("match") for c in compares),
        "mismatch_count": len(mismatches),
        "mismatch_artifact": mismatches[0]["artifact"] if len(mismatches) == 1 else None,
    }


def _load_sources(knowledge_dir):
    """Load the on-disk .index.json 'sources' map (design §11 R1)."""
    index_path = os.path.join(knowledge_dir, ingest.INDEX_FILE)
    with open(index_path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("sources", {})


def _is_apia_path(rel):
    """True when a repo-relative source path lives under .apia/."""
    norm = rel.replace(os.sep, "/")
    return norm == ".apia" or norm.startswith(".apia/")


def invoke_ingest(manifest):
    """Run ingest twice (default vs include_apia) into temp dirs; derive actual.

    Path-membership assertions read the on-disk .index.json 'sources' map (the
    ingest() return value does not carry 'sources'); test_count comes from the
    return dict (design §11 R1).
    """
    repo = resolve_repo(manifest)
    tmp_root = tempfile.mkdtemp(prefix="apia-eval-ingest-")
    try:
        default_kdir = os.path.join(tmp_root, "default")
        incl_kdir = os.path.join(tmp_root, "include")
        default_res = ingest.ingest(repo, default_kdir, include_apia=False)
        incl_res = ingest.ingest(repo, incl_kdir, include_apia=True)
        default_sources = _load_sources(default_kdir)
        incl_sources = _load_sources(incl_kdir)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    incl_apia_keys = {k.replace(os.sep, "/") for k in incl_sources if _is_apia_path(k)}
    return {
        "apia_paths_absent": not any(_is_apia_path(k) for k in default_sources),
        "apia_paths_present_set": incl_apia_keys,
        "test_count_delta": incl_res["test_count"] - default_res["test_count"],
    }


def invoke_verify(manifest):
    """Reserved verify branch — wired but unused by the initial 7 scenarios."""
    repo = resolve_repo(manifest)
    adapter_path = os.path.join(
        REPO_ROOT, ".apia", "adapters", manifest["adapter"] + ".yml")
    verify.load_adapter(adapter_path, repo)  # noqa: F841 - signature wired, unused
    return verify.verify(repo, adapter_path)


INVOKERS = {
    "scan": invoke_scan,
    "evaldata": invoke_evaldata,
    "ingest": invoke_ingest,
    "verify": invoke_verify,
}


# ---------------------------------------------------------------------------
# The PURE scorer (the ONLY new logic — design §4.4).
# ---------------------------------------------------------------------------

def score(engine, expected, actual):
    """Pure scorer: compare an engine's structured ``actual`` to ``expected``.

    Returns ``{passed, checks:[{key,ok}], metric_contributions}``. ``passed`` iff
    every declared assertion key holds. There is NO path from ``actual`` back into
    ``expected`` (the honesty invariant): both are read-only inputs.
    """
    checks = []
    contrib = {}

    for key, want in expected.items():
        if engine == "scan":
            if key == "gate_status":
                ok = actual.get("gate_status") == want
            elif key == "incomplete_includes":
                reasons = actual.get("incomplete_reason") or []
                ok = all(any(name in entry for entry in reasons) for name in want)
            elif key == "rules_present":
                rules = set(actual.get("rules") or [])
                ok = all(r in rules for r in want)
            elif key == "rules_absent":
                rules = set(actual.get("rules") or [])
                ok = all(r not in rules for r in want)
            else:  # pragma: no cover - guarded by manifest validation
                ok = False
        elif engine == "evaldata":
            if key == "golden_all_match":
                ok = bool(actual.get("golden_all_match")) == bool(want)
            elif key == "mismatch_count":
                ok = actual.get("mismatch_count") == want
            elif key == "mismatch_artifact":
                ok = actual.get("mismatch_artifact") == want
            else:  # pragma: no cover
                ok = False
        elif engine == "ingest":
            if key == "apia_paths_absent":
                ok = bool(actual.get("apia_paths_absent")) == bool(want)
            elif key == "apia_paths_present":
                present = {p.replace(os.sep, "/") for p in (actual.get("apia_paths_present_set") or set())}
                ok = all(p.replace(os.sep, "/") in present for p in want)
            elif key == "test_count_delta":
                ok = actual.get("test_count_delta") == want
            else:  # pragma: no cover
                ok = False
        else:  # pragma: no cover - verify declares no assertion keys yet
            ok = False
        checks.append({"key": key, "ok": bool(ok)})

    passed = all(c["ok"] for c in checks)
    contrib["passed"] = passed
    return {"passed": passed, "checks": checks, "metric_contributions": contrib}


# ---------------------------------------------------------------------------
# Per-scenario execution.
# ---------------------------------------------------------------------------

def run_scenario(manifest):
    """Discover-dispatch-score one scenario; return its result dict (never raises)."""
    base = {
        "id": manifest["id"],
        "engine": manifest["engine"],
        "metric": manifest.get("metric"),
        "expected": manifest["expected"],
    }

    skip_reason = check_requirements(manifest["tool_requirements"])
    if skip_reason is not None:
        base.update({
            "status": "skipped",
            "skip_reason": skip_reason,
            "actual": None,
            "checks": [],
            "passed": None,
        })
        return base

    try:
        actual = INVOKERS[manifest["engine"]](manifest)
    except ManifestError:
        raise
    except Exception as exc:  # engine raised -> status 'error', corpus continues
        base.update({
            "status": "error",
            "skip_reason": None,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "actual": None,
            "checks": [],
            "passed": False,
        })
        return base

    result = score(manifest["engine"], manifest["expected"], actual)
    base.update({
        "status": "scored",
        "skip_reason": None,
        "actual": _public_actual(manifest["engine"], actual),
        "checks": result["checks"],
        "passed": result["passed"],
    })
    return base


def _public_actual(engine, actual):
    """Return a JSON-serializable view of the derived actual (drop internal sets)."""
    if engine == "ingest":
        return {
            "apia_paths_absent": actual.get("apia_paths_absent"),
            "apia_paths_present": sorted(actual.get("apia_paths_present_set") or set()),
            "test_count_delta": actual.get("test_count_delta"),
        }
    return dict(actual)


# ---------------------------------------------------------------------------
# Aggregation (scored-only denominators + degraded-headline rule — design §3).
# ---------------------------------------------------------------------------

def aggregate(scenarios):
    """Aggregate per-scenario results into the design §4.4 aggregate block.

    Every denominator counts SCORED scenarios only; skipped scenarios are
    excluded and surfaced in scenarios_skipped_detail. The headline is numeric
    only when all three gate scenarios were scored, else 'degraded'.
    """
    by_id = {s["id"]: s for s in scenarios}

    # Gate-correctness: scored scan scenarios declaring gate_status.
    gate_scored = [s for s in scenarios
                   if s["engine"] == "scan" and s["status"] == "scored"
                   and "gate_status" in s["expected"]]
    gate_declared = [s for s in scenarios
                     if s["engine"] == "scan" and "gate_status" in s["expected"]]
    gate_correct = sum(1 for s in gate_scored if s["passed"])
    gate_scored_n = len(gate_scored)
    all_gate_scored = all(
        gid in by_id and by_id[gid]["status"] == "scored" for gid in GATE_SCENARIO_IDS)

    # Defect-detection: expected rule ids that appear, over scored rules_present scenarios.
    detected = 0
    expected_rules = 0
    dd_scored = 0
    dd_declared = 0
    for s in scenarios:
        if "rules_present" in s["expected"]:
            dd_declared += 1
            if s["status"] == "scored":
                dd_scored += 1
                want = s["expected"]["rules_present"]
                got = set((s["actual"] or {}).get("rules") or [])
                expected_rules += len(want)
                detected += sum(1 for r in want if r in got)

    # False positive / false negative counts (clean oracle S2 / defect oracle S1).
    false_positive = 0
    false_negative = 0
    for s in scenarios:
        if s["status"] != "scored":
            continue
        exp = s["expected"]
        act = s["actual"] or {}
        is_clean = exp.get("gate_status") == "PASSED" and exp.get("rules_present") == []
        is_defect = exp.get("gate_status") == "FAILED"
        if is_clean and not s["passed"]:
            false_positive += 1
        if is_defect and not s["passed"]:
            false_negative += 1

    # Verification-correctness: BOTH accept (S5) and reject (S6) must hold.
    accept = by_id.get("verify_golden_accept")
    reject = by_id.get("verify_golden_reject")
    vc_scored = sum(1 for s in (accept, reject) if s and s["status"] == "scored")
    vc_declared = sum(1 for s in (accept, reject) if s)
    accept_ok = bool(accept and accept["status"] == "scored" and accept["passed"])
    reject_ok = bool(reject and reject["status"] == "scored" and reject["passed"])
    vc_pass = accept_ok and reject_ok

    # Ingest-accuracy: ok/total of the three ingest assertions.
    ingest_scenarios = [s for s in scenarios if s["engine"] == "ingest"]
    ingest_scored = [s for s in ingest_scenarios if s["status"] == "scored"]
    ingest_ok = sum(c["ok"] for s in ingest_scored for c in s["checks"])
    ingest_total = sum(len(s["checks"]) for s in ingest_scored)

    scored = [s for s in scenarios if s["status"] == "scored"]
    skipped = [s for s in scenarios if s["status"] == "skipped"]
    passed = [s for s in scored if s["passed"]]
    failed = [s for s in scored if not s["passed"]]

    headline_status = "full" if all_gate_scored else "degraded"
    headline_gate = (gate_correct / gate_scored_n) if (all_gate_scored and gate_scored_n) else None
    corpus_pass_rate = (len(passed) / len(scored)) if scored else None

    return {
        "gate_correctness": {
            "correct": gate_correct,
            "scored": gate_scored_n,
            "declared": len(gate_declared),
            "value": (gate_correct / gate_scored_n) if gate_scored_n else None,
            "good": 1.0,
        },
        "defect_detection": {
            "detected": detected,
            "expected": expected_rules,
            "scored": dd_scored,
            "declared": dd_declared,
            "value": (detected / expected_rules) if expected_rules else None,
            "good": 1.0,
        },
        "false_positive": false_positive,
        "false_negative": false_negative,
        "verification_correctness": {
            "pass": vc_pass,
            "accept": accept_ok,
            "reject": reject_ok,
            "scored": vc_scored,
            "declared": vc_declared,
        },
        "ingest_accuracy": {
            "ok": ingest_ok,
            "total": ingest_total,
            "value": (ingest_ok / ingest_total) if ingest_total else None,
            "scored": len(ingest_scored),
            "declared": len(ingest_scenarios),
        },
        "scenarios_total": len(scenarios),
        "scenarios_scored": len(scored),
        "scenarios_skipped": len(skipped),
        "scenarios_skipped_detail": [
            {"id": s["id"], "skip_reason": s["skip_reason"]} for s in skipped],
        "scenarios_passed": len(passed),
        "scenarios_failed": len(failed),
        "headline": {
            "gate_correctness": headline_gate,
            "status": headline_status,
            "corpus_pass_rate": corpus_pass_rate,
        },
    }


# ---------------------------------------------------------------------------
# Results assembly + human summary.
# ---------------------------------------------------------------------------

def build_results(scenarios):
    """Assemble the full JSON results object (design §4.4 schema)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),  # NON-STABLE
        "tool_env": build_tool_env(),
        "scope_note": SCOPE_NOTE,
        "scenarios": scenarios,
        "aggregate": aggregate(scenarios),
    }


def _arrow(scenario):
    """A compact expected->actual string for the summary line."""
    exp = scenario["expected"]
    if scenario["engine"] == "scan" and "gate_status" in exp:
        want = exp["gate_status"]
        got = (scenario["actual"] or {}).get("gate_status", "?") if scenario["actual"] else "?"
        return "{}->{}".format(want, got)
    if scenario["engine"] == "evaldata":
        want = "match" if exp.get("golden_all_match") else "mismatch"
        act = scenario["actual"] or {}
        got = "match" if act.get("golden_all_match") else "mismatch" if act else "?"
        return "{}->{}".format(want, got)
    return scenario["metric"] or scenario["engine"]


def _verdict(scenario):
    if scenario["status"] == "skipped":
        return "SKIP"
    if scenario["status"] == "error":
        return "ERROR"
    return "PASS" if scenario["passed"] else "FAIL"


def _fmt_ratio(block):
    val = block.get("value")
    return "n/a" if val is None else "{:.2f}".format(val)


def format_summary(results):
    """Render a scan.format_human-style text summary (design §4.4)."""
    lines = []
    lines.append("APIA eval :: {} scenario(s)".format(len(results["scenarios"])))
    for s in results["scenarios"]:
        lines.append("  {:<32} {:<9} {:<18} {}".format(
            s["id"], s["engine"], _arrow(s), _verdict(s)))
    agg = results["aggregate"]
    lines.append("  --- aggregate ---")
    gc = agg["gate_correctness"]
    lines.append("  gate_correctness:        {} ({}/{}) good>={:.2f}".format(
        _fmt_ratio(gc), gc["scored"], gc["declared"], gc["good"]))
    dd = agg["defect_detection"]
    lines.append("  defect_detection:        {} ({}/{}) good>={:.2f}".format(
        _fmt_ratio(dd), dd["scored"], dd["declared"], dd["good"]))
    lines.append("  false_positive:          {}".format(agg["false_positive"]))
    lines.append("  false_negative:          {}".format(agg["false_negative"]))
    vc = agg["verification_correctness"]
    lines.append("  verification_correctness: {} ({}/{}) [accept={} reject={}]".format(
        "PASS" if vc["pass"] else "FAIL", vc["scored"], vc["declared"],
        vc["accept"], vc["reject"]))
    ia = agg["ingest_accuracy"]
    lines.append("  ingest_accuracy:         {} ({}/{}) good>=1.00".format(
        _fmt_ratio(ia), ia["scored"], ia["declared"]))
    headline = agg["headline"]
    if headline["status"] == "degraded":
        skipped = ", ".join(d["id"] for d in agg["scenarios_skipped_detail"]) or "none"
        lines.append("  HEADLINE gate_correctness: degraded (skipped: {})".format(skipped))
    else:
        lines.append("  HEADLINE gate_correctness: {:.2f} (full)".format(
            headline["gate_correctness"]))
    env = results["tool_env"]
    lines.append("  tools present: {}".format(", ".join(env["scanners_present"]) or "none"))
    lines.append("  tools absent:  {}".format(", ".join(env["scanners_absent"]) or "none"))
    lines.append("  scope: {}".format(SCOPE_NOTE))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (design §4.6).
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "APIA effectiveness-measurement harness (apia-eval): runs the "
            "benchmark corpus against the deterministic engines, scores against "
            "pre-declared ground truth, and emits JSON + a human summary. No "
            "network, no LLM."))
    parser.add_argument(
        "--out", default=None,
        help="Path to write the JSON results (default: {}).".format(DEFAULT_OUT))
    parser.add_argument(
        "--json-only", dest="json_only", action="store_true",
        help="Suppress the human summary (JSON is still written).")
    parser.add_argument(
        "--summary-only", dest="summary_only", action="store_true",
        help="Suppress the JSON file (summary to stdout only).")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.summary_only and args.out is not None:
        parser.error("--out and --summary-only are mutually exclusive")

    try:
        manifests = [load_manifest(p) for p in discover_manifests()]
        if not manifests:
            raise ManifestError("no scenarios discovered under {}".format(SCENARIOS_GLOB))
        scenarios = [run_scenario(m) for m in manifests]
    except ManifestError as exc:
        sys.stderr.write("evalbench: {}\n".format(exc))
        return 2

    results = build_results(scenarios)

    if not args.summary_only:
        out_path = args.out or DEFAULT_OUT
        try:
            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except OSError as exc:
            sys.stderr.write("evalbench: cannot write --out {}: {}\n".format(out_path, exc))
            return 2

    if args.json_only:
        # Suppress the human summary; emit the machine-readable JSON to stdout
        # (mirrors scan.py --json-only). The JSON file is ALSO written to --out
        # above unless --summary-only suppressed it.
        sys.stdout.write(json.dumps(results, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_summary(results) + "\n")

    scored = [s for s in scenarios if s["status"] == "scored"]
    errored = [s for s in scenarios if s["status"] == "error"]
    failed = [s for s in scored if not s["passed"]]
    if errored or failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
