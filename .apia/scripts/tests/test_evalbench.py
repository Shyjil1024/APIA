"""Tests for the APIA effectiveness-measurement harness (.apia/scripts/evalbench.py).

Coverage (per design.md §6). The harness's only NEW logic is the PURE scorer
``score(engine, expected, actual)`` and the aggregator; those are tested in
isolation with SYNTHETIC structured inputs needing no external tool. The schema,
tool_env, determinism and the static no-network/no-LLM guard are also asserted.
Engine-dependent end-to-end assertions are guarded with
``pytest.mark.skipif(... is None)`` exactly as the rest of the suite does, so the
tests stay deterministic and never fail merely because a scanner is absent.
"""

import json
import os
import shutil
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import evalbench  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic scenario-result helpers (no engine/tool needed).
# ---------------------------------------------------------------------------

def _scenario(id, engine, expected, actual, status="scored", skip_reason=None):
    """Build a scored scenario-result dict the way run_scenario would."""
    if status == "scored":
        result = evalbench.score(engine, expected, actual)
        checks = result["checks"]
        passed = result["passed"]
        public = evalbench._public_actual(engine, actual) if actual is not None else None
    else:
        checks, passed, public = [], None, None
    return {
        "id": id,
        "engine": engine,
        "metric": expected and next(iter(expected), None),
        "expected": expected,
        "status": status,
        "skip_reason": skip_reason,
        "actual": public,
        "checks": checks,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Pure scorer (the only new logic).
# ---------------------------------------------------------------------------

def test_scores_pass_scenario():
    """A synthetic actual that matches the declared expected scores PASS."""
    expected = {"gate_status": "FAILED", "rules_present": ["B602"]}
    actual = {"gate_status": "FAILED", "incomplete_reason": [], "rules": ["B602", "B105"]}
    result = evalbench.score("scan", expected, actual)
    assert result["passed"] is True
    assert all(c["ok"] for c in result["checks"])


def test_scores_fail_scenario():
    """Declared FAILED vs actual PASSED => passed False (the harness can fail)."""
    expected = {"gate_status": "FAILED"}
    actual = {"gate_status": "PASSED", "incomplete_reason": [], "rules": []}
    result = evalbench.score("scan", expected, actual)
    assert result["passed"] is False
    assert result["checks"] == [{"key": "gate_status", "ok": False}]


def test_scorer_incomplete_includes_substring():
    """incomplete_includes matches a tool name as a substring of a reason entry."""
    expected = {"gate_status": "INCOMPLETE", "incomplete_includes": ["cppcheck"]}
    actual = {"gate_status": "INCOMPLETE",
              "incomplete_reason": ["cppcheck (unavailable)"], "rules": []}
    assert evalbench.score("scan", expected, actual)["passed"] is True
    bad = {"gate_status": "INCOMPLETE", "incomplete_reason": ["ruff (failed)"], "rules": []}
    assert evalbench.score("scan", expected, bad)["passed"] is False


def test_scorer_evaldata_reject():
    """evaldata reject assertions (count + artifact) are checked exactly."""
    expected = {"golden_all_match": False, "mismatch_count": 1,
                "mismatch_artifact": "out/report.json"}
    actual = {"golden_all_match": False, "mismatch_count": 1,
              "mismatch_artifact": "out/report.json"}
    assert evalbench.score("evaldata", expected, actual)["passed"] is True
    actual2 = dict(actual, mismatch_count=2, mismatch_artifact=None)
    assert evalbench.score("evaldata", expected, actual2)["passed"] is False


def test_scorer_ingest_keys():
    """ingest apia_paths_present membership + delta are checked."""
    expected = {
        "apia_paths_absent": True,
        "apia_paths_present": [".apia/scripts/foo.py", ".apia/scripts/tests/test_foo.py"],
        "test_count_delta": 1,
    }
    actual = {
        "apia_paths_absent": True,
        "apia_paths_present_set": {".apia/scripts/foo.py", ".apia/scripts/tests/test_foo.py"},
        "test_count_delta": 1,
    }
    assert evalbench.score("ingest", expected, actual)["passed"] is True
    missing = dict(actual, apia_paths_present_set={".apia/scripts/foo.py"})
    assert evalbench.score("ingest", expected, missing)["passed"] is False


# ---------------------------------------------------------------------------
# False positive / false negative counts.
# ---------------------------------------------------------------------------

def test_false_pos_neg_counts():
    """A clean oracle that FAILED increments FP; a defect oracle that passed clean => FN."""
    clean = _scenario(
        "gate_pass_clean", "scan",
        {"gate_status": "PASSED", "rules_present": []},
        {"gate_status": "FAILED", "incomplete_reason": [], "rules": ["B101"]})
    defect = _scenario(
        "gate_fail_pyapp", "scan",
        {"gate_status": "FAILED", "rules_present": ["B602"]},
        {"gate_status": "PASSED", "incomplete_reason": [], "rules": []})
    agg = evalbench.aggregate([clean, defect])
    assert agg["false_positive"] == 1
    assert agg["false_negative"] == 1


# ---------------------------------------------------------------------------
# Schema, serializability, aggregate derivation.
# ---------------------------------------------------------------------------

def test_results_schema_and_serializable():
    scenarios = [
        _scenario("gate_fail_pyapp", "scan",
                  {"gate_status": "FAILED", "rules_present": ["B602"]},
                  {"gate_status": "FAILED", "incomplete_reason": [], "rules": ["B602", "B105"]}),
        _scenario("gate_pass_clean", "scan",
                  {"gate_status": "PASSED", "rules_present": []},
                  {"gate_status": "PASSED", "incomplete_reason": [], "rules": []}),
        _scenario("gate_incomplete_missing_scanner", "scan",
                  {"gate_status": "INCOMPLETE", "incomplete_includes": ["cppcheck"]},
                  {"gate_status": "INCOMPLETE", "incomplete_reason": ["cppcheck (unavailable)"], "rules": []}),
    ]
    results = evalbench.build_results(scenarios)
    assert results["schema_version"] == 1
    assert {"generated_at", "tool_env", "scope_note", "scenarios", "aggregate"} <= set(results)
    # JSON round-trips.
    reloaded = json.loads(json.dumps(results, sort_keys=True))
    assert reloaded == results
    # Aggregate equals the derivation of the per-scenario contributions.
    agg = results["aggregate"]
    assert agg["gate_correctness"]["correct"] == 3
    assert agg["gate_correctness"]["scored"] == 3
    assert agg["headline"]["status"] == "full"
    assert agg["headline"]["gate_correctness"] == pytest.approx(1.0)
    assert agg["scenarios_passed"] == 3


# ---------------------------------------------------------------------------
# Skipping on absent/violated tool requirements.
# ---------------------------------------------------------------------------

def test_skips_on_absent_required_tool(monkeypatch):
    """Positive-absent AND violated-negative requirements both yield a skip reason."""
    present = {"present_tool"}

    def fake_present(tool):
        return tool in present

    monkeypatch.setattr(evalbench, "tool_present", fake_present)
    # Positive requirement absent -> skip.
    assert evalbench.check_requirements(["absent_tool"]) is not None
    # Negative requirement violated (tool present when it must be absent) -> skip.
    assert evalbench.check_requirements(["!present_tool"]) is not None
    # Satisfied positive + satisfied negative -> no skip.
    assert evalbench.check_requirements(["present_tool", "!absent_tool"]) is None


# ---------------------------------------------------------------------------
# Degraded headline when a gate scenario is skipped.
# ---------------------------------------------------------------------------

def test_headline_degraded_when_gate_scenario_skipped():
    scenarios = [
        _scenario("gate_fail_pyapp", "scan",
                  {"gate_status": "FAILED", "rules_present": ["B602"]},
                  None, status="skipped", skip_reason="required tool 'bandit' not present"),
        _scenario("gate_pass_clean", "scan",
                  {"gate_status": "PASSED", "rules_present": []},
                  {"gate_status": "PASSED", "incomplete_reason": [], "rules": []}),
        _scenario("gate_incomplete_missing_scanner", "scan",
                  {"gate_status": "INCOMPLETE", "incomplete_includes": ["cppcheck"]},
                  {"gate_status": "INCOMPLETE", "incomplete_reason": ["cppcheck (unavailable)"], "rules": []}),
    ]
    agg = evalbench.aggregate(scenarios)
    assert agg["headline"]["status"] == "degraded"
    assert agg["headline"]["gate_correctness"] is None  # not a bare number
    assert agg["gate_correctness"]["scored"] == 2
    assert agg["gate_correctness"]["declared"] == 3
    detail_ids = {d["id"] for d in agg["scenarios_skipped_detail"]}
    assert "gate_fail_pyapp" in detail_ids
    assert all(d["skip_reason"] for d in agg["scenarios_skipped_detail"])


# ---------------------------------------------------------------------------
# tool_env is built from REAL probes (not a constant).
# ---------------------------------------------------------------------------

def test_tool_env_reflects_actual_presence(monkeypatch):
    """Monkeypatching a probe flips the scanners_present/absent entry."""
    monkeypatch.setattr(evalbench, "tool_present", lambda tool: tool == "ruff")
    env = evalbench.build_tool_env()
    assert "ruff" in env["scanners_present"]
    assert "bandit" in env["scanners_absent"]
    assert env["python"].startswith("{}.{}".format(*sys.version_info[:2]))

    monkeypatch.setattr(evalbench, "tool_present", lambda tool: tool != "ruff")
    env2 = evalbench.build_tool_env()
    assert "ruff" in env2["scanners_absent"]
    assert "bandit" in env2["scanners_present"]


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------

def _blank_nonstable(results):
    """Drop the only non-stable fields (timestamp + abs paths) for comparison."""
    clone = json.loads(json.dumps(results, sort_keys=True))
    clone["generated_at"] = ""
    return clone


def test_determinism():
    scenarios = [
        _scenario("gate_fail_pyapp", "scan",
                  {"gate_status": "FAILED", "rules_present": ["B602"]},
                  {"gate_status": "FAILED", "incomplete_reason": [], "rules": ["B602"]}),
        _scenario("verify_golden_accept", "evaldata",
                  {"golden_all_match": True},
                  {"golden_all_match": True, "mismatch_count": 0, "mismatch_artifact": None}),
    ]
    a = _blank_nonstable(evalbench.build_results(scenarios))
    b = _blank_nonstable(evalbench.build_results(scenarios))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# Static no-network / no-LLM guard (same token list as test_ingest/test_scan).
# ---------------------------------------------------------------------------

def test_evalbench_makes_no_network_or_llm_calls():
    src_path = os.path.join(SCRIPTS_DIR, "evalbench.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    forbidden = [
        "import requests",
        "import openai",
        "import anthropic",
        "import httpx",
        "urllib.request",
        "http.client",
        "socket.socket",
        "api.openai.com",
        "chat/completions",
    ]
    for token in forbidden:
        assert token not in src, "evalbench.py must not reference '{}'".format(token)


# ---------------------------------------------------------------------------
# Integration smoke (engine-dependent; guarded so it skips cleanly).
# ---------------------------------------------------------------------------

def test_discovers_the_seven_scenarios():
    manifests = evalbench.discover_manifests()
    assert len(manifests) == 7
    for p in manifests:
        m = evalbench.load_manifest(p)
        assert m["engine"] in evalbench.ENGINES


@pytest.mark.skipif(
    not evalbench.tool_present("bandit"), reason="bandit not importable in this interpreter")
def test_scan_scenario_runs_end_to_end():
    """When bandit is reachable, the gate_fail scenario scores FAILED with B602."""
    m = evalbench.load_manifest(
        os.path.join(evalbench.REPO_ROOT, ".apia", "eval", "scenarios",
                     "gate_fail_pyapp", "scenario.yml"))
    actual = evalbench.invoke_scan(m)
    assert actual["gate_status"] == "FAILED"
    assert "B602" in actual["rules"]
