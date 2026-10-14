"""Tests for the APIA Layer-2 self-correction loop machinery (evalloop.py).

Coverage mirrors the rest of the suite (deterministic unit tests always run; the
real both-stacks iterate-to-green cycle runs for real, C++ guarded by
shutil.which(cmake/ctest)):

  (1) iteration-cap / stop: a cycle that stays RED yields status 'stop' with a
      specific ask AT the cap and the counter never exceeds max_iterations;
  (2) green-path: a passing cycle returns 'green' and stops immediately;
  (3) structured failure report: contains the failing phase, parsed counts,
      stdout/stderr tails, and any golden/invariant/differential failures, and is
      JSON-serializable; fail-loud on a clean summary;
  (4) acceptance-criteria gate: new-behavior criteria are returned
      needs_human_confirmation and evaluate() refuses to treat them as oracle
      until confirmed (config eval_data.acceptance_criteria_gate);
  (5) token-budget advisory: present on each cycle result and respects config;
  (6) a static guard that evalloop.py makes no network/LLM/model call -- the only
      subprocess is the adapter's local command (spawned via verify/evaldata);
  (7) the FULL deterministic iterate-to-green cycle on BOTH stacks: start red
      (a deliberately failing change), run a cycle -> structured failure, apply a
      DETERMINISTIC scripted fix (standing in for the LIVE model fix), re-run ->
      green, and assert the cap would have stopped it had it stayed red.

evalloop.py is a pure off-model module: it decides green/stop/ask and makes NO
model call. The only subprocess is the adapter's own LOCAL build/test command,
spawned indirectly through verify/evaldata (the shared verify._run path).
"""

import json
import os
import shutil
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import evalloop  # noqa: E402
import evaldata  # noqa: E402
import verify  # noqa: E402
import scan  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
CPPAPP = os.path.join(REPO_ROOT, "samples", "cppapp")
PY_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")
CPP_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "cpp.yml")

_HAVE_CMAKE = shutil.which("cmake") is not None and shutil.which("ctest") is not None
_requires_toolchain = pytest.mark.skipif(
    not _HAVE_CMAKE, reason="cmake/ctest not on PATH; C++ iterate-to-green skipped"
)


def _write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _purge_bytecode(repo):
    """Delete all __pycache__ dirs under ``repo``.

    A deterministic scripted edit can rewrite a .py file with the SAME byte size
    within the same filesystem mtime second (e.g. 'a + b' -> 'a - b'). CPython's
    pyc validation keys on (mtime, size), so a stale __pycache__ entry would make
    the subprocess pytest import the OLD bytecode and mask the edit. Purging the
    bytecode cache before each verify run forces a recompile so the loop always
    grades the ACTUAL current source (the real behavior), not a stale cache."""
    for dirpath, dirnames, _files in os.walk(repo):
        if os.path.basename(dirpath) == "__pycache__":
            shutil.rmtree(dirpath, ignore_errors=True)
    # os.walk collected __pycache__ dirs; a second pass is unnecessary because
    # rmtree removed them, but nested caches are covered by the full walk above.


# A tiny stub adapter with a real test_command run via sys.executable so the
# cap/stop and green-path tests drive verify() deterministically without any
# real stack. The test command exits 0/1 based on a sentinel file on disk.
def _scripted_adapter():
    return {
        "name": "python",
        "test_command": "python -m pytest",
        "no_tests_detection": {
            "collect_command": "python -m pytest --collect-only -q",
            "zero_tests_signal": "collected 0 items",
        },
    }


# ---------------------------------------------------------------------------
# config resolution helpers
# ---------------------------------------------------------------------------


def test_resolve_max_iterations_reads_config():
    # The committed .apia/config.yml pins max_iterations: 3 (the HARD cap).
    assert evalloop.resolve_max_iterations(REPO_ROOT) == 3


def test_resolve_max_iterations_override_wins():
    assert evalloop.resolve_max_iterations(REPO_ROOT, override=1) == 1
    # The cap is always a positive int: a bad/zero value can never disable it.
    assert evalloop.resolve_max_iterations(REPO_ROOT, override=0) == 1
    assert evalloop.resolve_max_iterations(REPO_ROOT, override=-5) == 1


def test_resolve_iterate_budget_reads_config():
    # token_budget.iterate: 4000 in the committed config.
    assert evalloop.resolve_iterate_budget(REPO_ROOT) == 4000


def test_resolve_acceptance_gate_defaults_on():
    assert evalloop.resolve_acceptance_criteria_gate(REPO_ROOT) is True


# ---------------------------------------------------------------------------
# (3) structured failure report: contents + JSON-serializable + fail-loud
# ---------------------------------------------------------------------------


def test_failure_report_contains_phase_counts_tails_and_layer1():
    summary = {
        "repo": "demo",
        "adapter": "python",
        "ok": False,
        "regression_delta": {"regressed": True, "removed_nodes": ["t::x"],
                             "passed_delta": -1},
        "phases": [
            {"phase": "baseline", "command": "python -m pytest", "returncode": 0,
             "passed": True, "counts": {"passed": 3, "failed": 0, "error": 0,
                                        "skipped": 0, "no_tests": False},
             "stdout_tail": "3 passed", "stderr_tail": ""},
            {"phase": "regression", "command": "python -m pytest", "returncode": 1,
             "passed": False, "counts": {"passed": 2, "failed": 1, "error": 0,
                                         "skipped": 0, "no_tests": False},
             "stdout_tail": "1 failed, 2 passed", "stderr_tail": "AssertionError"},
        ],
    }
    eval_data = {
        "golden": [
            {"match": True, "artifact": "out/a.json", "diff_summary": ""},
            {"match": False, "artifact": "out/b.json", "golden": "g/b.json",
             "mode": "json", "diff_summary": "json structure differs"},
        ],
        "invariants": [
            {"check_id": "no_nans", "held": True, "detail": ""},
            {"check_id": "row_count_matches_input", "held": False,
             "detail": "row count 2 != input row count 3"},
        ],
        "differential": {
            "unchanged": ["out/a.json"],
            "changed": [{"artifact": "out/b.json", "mode": "json",
                         "diff_summary": "changed"}],
            "mode": "json",
        },
    }
    report = evalloop.build_failure_report(summary, eval_data)

    # Failing phase present with counts + tails.
    assert [p["phase"] for p in report["failing_phases"]] == ["regression"]
    rp = report["failing_phases"][0]
    assert rp["counts"]["failed"] == 1
    assert rp["returncode"] == 1
    assert "1 failed" in rp["stdout_tail"]
    assert "AssertionError" in rp["stderr_tail"]

    # Per-phase parsed counts.
    assert report["counts"]["baseline"]["passed"] == 3
    assert report["counts"]["regression"]["failed"] == 1

    # Counts differential (removed nodes / passed delta) folded in.
    assert report["regression_delta"]["regressed"] is True
    assert report["regression_delta"]["removed_nodes"] == ["t::x"]

    # Layer-1 failures only (passing ones excluded).
    assert [g["artifact"] for g in report["golden_failures"]] == ["out/b.json"]
    assert [i["check_id"] for i in report["invariant_violations"]] == \
        ["row_count_matches_input"]
    assert [d["artifact"] for d in report["differential_regressions"]] == ["out/b.json"]

    # JSON-serializable.
    text = json.dumps(report)
    assert json.loads(text)["ok"] is False


def test_failure_report_is_fail_loud_on_clean_summary():
    """A summary with no failing phase and no Layer-1 failure must NOT yield an
    empty 'all clear' report -- build_failure_report raises instead."""
    clean = {
        "repo": "demo", "adapter": "python", "ok": True,
        "regression_delta": None,
        "phases": [
            {"phase": "baseline", "passed": True, "command": "x", "returncode": 0,
             "counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0,
                        "no_tests": False}, "stdout_tail": "", "stderr_tail": ""},
        ],
    }
    with pytest.raises(ValueError):
        evalloop.build_failure_report(clean, eval_data=None)


def test_failure_report_captures_layer1_only_failure():
    """A verify-green summary with a Layer-1 golden failure still builds a report
    (fail-loud recognizes the Layer-1 failure)."""
    summary = {
        "repo": "demo", "adapter": "python", "ok": True, "regression_delta": None,
        "phases": [
            {"phase": "baseline", "passed": True, "command": "x", "returncode": 0,
             "counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0,
                        "no_tests": False}, "stdout_tail": "", "stderr_tail": ""},
        ],
    }
    eval_data = {"golden": [{"match": False, "artifact": "out/b.json",
                             "diff_summary": "byte mismatch"}]}
    report = evalloop.build_failure_report(summary, eval_data)
    assert report["failing_phases"] == []
    assert report["golden_failures"][0]["artifact"] == "out/b.json"
    assert json.loads(json.dumps(report))  # serializable


# ---------------------------------------------------------------------------
# (4) acceptance-criteria confirmation gate (anti-circularity)
# ---------------------------------------------------------------------------


def test_propose_acceptance_criteria_flags_new_behavior():
    eval_data = {
        "invariants": [{"check_id": "no_nans", "held": True, "detail": ""}],
        "golden": [{"match": True, "artifact": "out/report.json"}],
    }
    spec = {"is_new_behavior": True, "criteria": ["multiply returns a*b"],
            "summary": "add multiply()"}
    proposal = evalloop.propose_acceptance_criteria(spec, eval_data)
    assert proposal["needs_human_confirmation"] is True
    assert proposal["confirmed"] is False
    sources = {c["source"] for c in proposal["criteria"]}
    # Stated + grounded-from-Layer-1 candidates are all surfaced.
    assert "stated" in sources
    assert "invariant" in sources
    assert "golden" in sources


def test_propose_acceptance_criteria_not_new_behavior_needs_no_confirmation():
    proposal = evalloop.propose_acceptance_criteria(
        {"is_new_behavior": False, "criteria": []}, eval_data=None
    )
    assert proposal["needs_human_confirmation"] is False


def test_acceptance_criteria_confirmed_predicate():
    # None -> nothing to gate.
    assert evalloop.acceptance_criteria_confirmed(None) is True
    # Non-new-behavior -> grounded oracles already exist.
    assert evalloop.acceptance_criteria_confirmed(
        {"is_new_behavior": False}) is True
    # New behavior unconfirmed -> NOT an oracle.
    assert evalloop.acceptance_criteria_confirmed(
        {"is_new_behavior": True, "confirmed": False}) is False
    # New behavior confirmed -> oracle.
    assert evalloop.acceptance_criteria_confirmed(
        {"is_new_behavior": True, "confirmed": True}) is True


def test_evaluate_refuses_unconfirmed_new_behavior_criteria(tmp_path):
    """evaluate() must NOT run the oracle while new-behavior criteria are
    unconfirmed; it returns needs_confirmation with a specific ask and spends NO
    iteration (so it cannot "pass by agreeing with itself")."""
    def _boom(*a, **k):
        raise AssertionError("evaluate must not run a cycle before confirmation")

    # If the gate is honored, run_iteration_cycle is never reached.
    import evalloop as el
    original = el.run_iteration_cycle
    el.run_iteration_cycle = _boom
    try:
        acceptance = el.propose_acceptance_criteria(
            {"is_new_behavior": True, "criteria": ["new thing"]}, None
        )
        state = el.LoopState()
        decision = el.evaluate(
            str(tmp_path), _scripted_adapter(),
            {"acceptance": acceptance, "max_iterations": 3}, state,
        )
    finally:
        el.run_iteration_cycle = original
    assert decision["status"] == "needs_confirmation"
    assert decision["reason"] == "acceptance_criteria_unconfirmed"
    assert "anti-circularity" in decision["ask"]
    assert state.iteration == 0  # no iteration spent


def test_evaluate_proceeds_once_criteria_confirmed(monkeypatch, tmp_path):
    """Once the (live-session) human confirmation is recorded, evaluate() treats
    the criteria as an oracle and runs a cycle."""
    acceptance = evalloop.propose_acceptance_criteria(
        {"is_new_behavior": True, "criteria": ["new thing"]}, None
    )
    acceptance["confirmed"] = True  # the live human confirmation happened

    def _green_cycle(repo, adapter, options):
        return {
            "status": "green", "iteration": options.get("iteration"),
            "remaining_iterations": options.get("remaining_iterations"),
            "summary": {"ok": True}, "eval_data": None,
            "failure_report": None,
            "budget_advisory": {"budget": 4000, "estimated_tokens": 10,
                                "within_budget": True, "warning": None},
        }

    monkeypatch.setattr(evalloop, "run_iteration_cycle", _green_cycle)
    state = evalloop.LoopState()
    decision = evalloop.evaluate(
        str(tmp_path), _scripted_adapter(),
        {"acceptance": acceptance, "max_iterations": 3}, state,
    )
    assert decision["status"] == "green"
    assert state.iteration == 1


# ---------------------------------------------------------------------------
# (1) iteration cap / stop + (2) green-path (driven deterministically)
# ---------------------------------------------------------------------------


def test_evaluate_green_path_stops_immediately(monkeypatch, tmp_path):
    def _green_cycle(repo, adapter, options):
        return {
            "status": "green", "iteration": options["iteration"],
            "remaining_iterations": options["remaining_iterations"],
            "summary": {"ok": True}, "eval_data": None, "failure_report": None,
            "budget_advisory": {"budget": 4000, "estimated_tokens": 5,
                                "within_budget": True, "warning": None},
        }

    monkeypatch.setattr(evalloop, "run_iteration_cycle", _green_cycle)
    state = evalloop.LoopState()
    decision = evalloop.evaluate(str(tmp_path), _scripted_adapter(),
                                 {"max_iterations": 3}, state)
    assert decision["status"] == "green"
    assert decision["iteration"] == 1  # stopped on the first green cycle
    assert state.iteration == 1


def test_evaluate_persistent_red_stops_at_cap_with_ask(monkeypatch, tmp_path):
    """A cycle that stays RED must stop AT the cap with a specific ask, and the
    iteration counter must NEVER exceed max_iterations."""
    seen_iterations = []

    def _red_cycle(repo, adapter, options):
        seen_iterations.append(options["iteration"])
        return {
            "status": "needs_fix", "iteration": options["iteration"],
            "remaining_iterations": options["remaining_iterations"],
            "summary": {"ok": False},
            "eval_data": None,
            "failure_report": {
                "ok": False,
                "failing_phases": [{"phase": "regression", "counts": {"failed": 1}}],
                "counts": {}, "regression_delta": None,
                "golden_failures": [], "invariant_violations": [],
                "differential_regressions": [],
            },
            "budget_advisory": {"budget": 4000, "estimated_tokens": 50,
                                "within_budget": True, "warning": None},
        }

    monkeypatch.setattr(evalloop, "run_iteration_cycle", _red_cycle)
    cap = 3
    state = evalloop.LoopState()
    decisions = []
    # Call evaluate() repeatedly as an orchestrator would (the model "fix"
    # between calls is simulated by the cycle staying red).
    for _ in range(10):  # far more than the cap; the loop must self-limit
        decision = evalloop.evaluate(str(tmp_path), _scripted_adapter(),
                                     {"max_iterations": cap}, state)
        decisions.append(decision)
        if decision["status"] == "stop":
            break

    final = decisions[-1]
    assert final["status"] == "stop"
    assert final["reason"] == "max_iterations_reached"
    assert final["iteration"] == cap
    assert "ask" in final and final["ask"]
    assert "max_iterations" in final["ask"]
    # The counter never exceeded the hard cap.
    assert state.iteration == cap
    assert max(seen_iterations) == cap
    # Exactly cap cycles ran (needs_fix for 1..cap-1, stop at cap).
    assert [d["status"] for d in decisions] == \
        ["needs_fix"] * (cap - 1) + ["stop"]
    # The stop carries the structured failure report for the human.
    assert final["failure_report"]["failing_phases"][0]["phase"] == "regression"


def test_evaluate_stop_includes_budget_advisory(monkeypatch, tmp_path):
    def _red_cycle(repo, adapter, options):
        return {
            "status": "needs_fix", "iteration": options["iteration"],
            "remaining_iterations": options["remaining_iterations"],
            "summary": {"ok": False}, "eval_data": None,
            "failure_report": {"ok": False, "failing_phases": [], "counts": {},
                               "regression_delta": None, "golden_failures": [],
                               "invariant_violations": [],
                               "differential_regressions": []},
            "budget_advisory": {"budget": 4000, "estimated_tokens": 10,
                                "within_budget": True, "warning": None},
        }

    monkeypatch.setattr(evalloop, "run_iteration_cycle", _red_cycle)
    state = evalloop.LoopState()
    for _ in range(5):
        decision = evalloop.evaluate(str(tmp_path), _scripted_adapter(),
                                     {"max_iterations": 1}, state)
        if decision["status"] == "stop":
            break
    assert decision["status"] == "stop"
    assert decision["iteration"] == 1  # cap of 1 -> stops after one red cycle
    assert "budget_advisory" in decision


# ---------------------------------------------------------------------------
# (5) token-budget advisory present + respects config
# ---------------------------------------------------------------------------


def test_budget_advisory_within_budget():
    advisory = evalloop._budget_advisory({"small": "payload"}, 4000)
    assert advisory["within_budget"] is True
    assert advisory["warning"] is None
    assert advisory["budget"] == 4000
    assert advisory["estimated_tokens"] >= 1


def test_budget_advisory_warns_on_exceed():
    big = {"blob": "x" * 10000}  # ~2500 tokens
    advisory = evalloop._budget_advisory(big, 100)
    assert advisory["within_budget"] is False
    assert advisory["warning"]
    assert "token_budget.iterate" in advisory["warning"]


def test_cycle_attaches_budget_advisory(monkeypatch, tmp_path):
    """run_iteration_cycle must attach a budget advisory derived from config."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Stub verify.verify to a green summary so no real subprocess runs.
    def _fake_verify(repo_path, **kwargs):
        return {"repo": "repo", "adapter": "python", "ok": True,
                "regression_delta": None,
                "phases": [{"phase": "baseline", "passed": True, "command": "x",
                            "returncode": 0,
                            "counts": {"passed": 1, "failed": 0, "error": 0,
                                       "skipped": 0, "no_tests": False},
                            "stdout_tail": "", "stderr_tail": ""}]}

    monkeypatch.setattr(verify, "verify", _fake_verify)
    result = evalloop.run_iteration_cycle(str(repo), {"name": "python"},
                                          {"layer1": False})
    assert result["status"] == "green"
    assert result["budget_advisory"]["budget"] == \
        evalloop.resolve_iterate_budget(str(repo))
    assert "estimated_tokens" in result["budget_advisory"]


# ---------------------------------------------------------------------------
# (6) static guard: no network / no LLM / no model; only the adapter command
# ---------------------------------------------------------------------------


def test_evalloop_makes_no_network_or_llm_calls():
    src_path = os.path.join(SCRIPTS_DIR, "evalloop.py")
    src = _read(src_path)
    forbidden = [
        "import requests",
        "import openai",
        "import anthropic",
        "import httpx",
        "urllib.request",
        "http.client",
        "socket.socket",
        "api.openai.com",
        "api.anthropic.com",
        "chat/completions",
    ]
    for token in forbidden:
        assert token not in src, "evalloop.py must not reference '{}'".format(token)


def test_evalloop_spawns_no_subprocess_of_its_own():
    """evalloop must not spawn its own subprocess: the ONLY process launch is the
    adapter's local command, run INDIRECTLY through verify/evaldata."""
    src_path = os.path.join(SCRIPTS_DIR, "evalloop.py")
    src = _read(src_path)
    for token in ("subprocess.run", "subprocess.Popen", "os.system", "os.popen"):
        assert token not in src, \
            "evalloop.py must spawn only via verify/evaldata, not '{}'".format(token)


def test_evalloop_only_runs_the_adapter_command(monkeypatch, tmp_path):
    """A full cycle's only subprocess is the adapter's local command (verify._run)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    real_run = verify._run

    def _record(argv, cwd):
        calls.append(argv)
        return real_run(argv, cwd)

    monkeypatch.setattr(verify, "_run", _record)
    # layer1 disabled so only the verify phases' adapter command runs.
    evalloop.run_iteration_cycle(
        str(repo), {"name": "python"},
        {"adapter_path": PY_ADAPTER, "layer1": False},
    )
    # Every spawned command is the adapter's python command (sys.executable),
    # never a network client or a model endpoint.
    assert calls, "a cycle must run the adapter's local command"
    for argv in calls:
        assert argv[0] == sys.executable


# ---------------------------------------------------------------------------
# (7) FULL deterministic iterate-to-green cycle on BOTH stacks
# ---------------------------------------------------------------------------


# Transient directories that must NOT be carried into a fresh sample copy: build
# output, bytecode/pytest caches, a persisted baseline, and produced (non-golden)
# artifacts. Copying a stale .pytest_cache in particular would let a prior run's
# lastfailed leak into the subprocess pytest run inside the copy.
_COPY_SKIP = ("build", "__pycache__", ".pytest_cache", ".apia-baseline.json",
              "out")


def _copy_sources(src, dst):
    """Copy a sample repo's sources (skipping build dirs, caches, produced out/)."""
    os.makedirs(dst)
    for name in os.listdir(src):
        if name in _COPY_SKIP:
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns(*_COPY_SKIP))
        else:
            shutil.copy2(s, d)
    return dst


def test_full_iterate_to_green_python(tmp_path):
    """REAL Python iterate-to-green: start RED (a deliberately broken existing
    test), one cycle -> structured failure, apply a DETERMINISTIC scripted fix
    (standing in for the live model fix), re-run -> GREEN; and assert the cap
    would have stopped it had it stayed red."""
    repo = _copy_sources(PYAPP, str(tmp_path / "pyapp"))
    adapter = verify.load_adapter(PY_ADAPTER)
    opts = {"adapter_path": PY_ADAPTER, "new_tests": ["tests/test_multiply.py"]}

    # --- START RED: break an EXISTING operation so its test fails. ---
    ops = os.path.join(repo, "calc", "operations.py")
    good = _read(ops)
    broken = good.replace("def add(a, b):\n    \"\"\"Return the sum of two numbers.\"\"\"\n    return a + b",
                          "def add(a, b):\n    \"\"\"Return the sum of two numbers.\"\"\"\n    return a - b")
    assert broken != good, "fixture must actually break add()"
    _write(ops, broken)
    _purge_bytecode(repo)

    state = evalloop.LoopState()
    red = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert red["status"] == "needs_fix", red
    report = red["failure_report"]
    # The structured failure names a failing phase and is JSON-serializable.
    assert report["failing_phases"], report
    assert json.loads(json.dumps(report, default=str))
    assert state.iteration == 1

    # --- SCRIPTED FIX (stands in for the LIVE model fix between cycles). ---
    _write(ops, good)
    _purge_bytecode(repo)

    green = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert green["status"] == "green", green
    assert state.iteration == 2  # one red + one green, within the cap

    # --- Prove the cap WOULD have stopped a persistent red at max_iterations. ---
    _write(ops, broken)
    _purge_bytecode(repo)
    cap_state = evalloop.LoopState()
    decisions = []
    for _ in range(10):
        d = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=2),
                              cap_state)
        decisions.append(d)
        if d["status"] == "stop":
            break
    assert decisions[-1]["status"] == "stop"
    assert decisions[-1]["reason"] == "max_iterations_reached"
    assert cap_state.iteration == 2  # never exceeded the cap of 2


@_requires_toolchain
def test_full_iterate_to_green_cpp(tmp_path):
    """REAL C++ iterate-to-green via CMake + GoogleTest + CTest: start RED
    (deliberately broken existing test), cycle -> structured failure, scripted
    fix, re-run -> GREEN; and prove the cap would have stopped a persistent red.
    Guarded by shutil.which(cmake/ctest) so the suite stays portable."""
    repo = _copy_sources(CPPAPP, str(tmp_path / "cppapp"))
    adapter = verify.load_adapter(CPP_ADAPTER)
    opts = {"adapter_path": CPP_ADAPTER}

    test_file = os.path.join(repo, "tests", "test_mathops.cpp")
    good = _read(test_file)
    broken = good.replace("mathops::add(2, 3), 5", "mathops::add(2, 3), 999")
    assert broken != good, "fixture must actually break an existing C++ test"

    # --- START RED. ---
    _write(test_file, broken)
    state = evalloop.LoopState()
    red = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert red["status"] == "needs_fix", red
    report = red["failure_report"]
    # ctest reports the failing regression phase; report is JSON-serializable.
    assert any(p["phase"] in ("regression", "baseline")
               for p in report["failing_phases"]), report
    assert json.loads(json.dumps(report, default=str))
    assert state.iteration == 1

    # --- SCRIPTED FIX (stands in for the LIVE model fix). ---
    _write(test_file, good)
    green = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert green["status"] == "green", green
    assert state.iteration == 2

    # --- Prove the cap stops a persistent red. ---
    _write(test_file, broken)
    cap_state = evalloop.LoopState()
    decisions = []
    for _ in range(10):
        d = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=2),
                              cap_state)
        decisions.append(d)
        if d["status"] == "stop":
            break
    assert decisions[-1]["status"] == "stop"
    assert cap_state.iteration == 2


# ===========================================================================
# PHASE 5 -- ITERATE-TO-BEST: the fused quality-gated decision
# (evalloop.evaluate_quality_gated). These tests GENUINELY execute the fused
# decision + reviewer-gate fusion + best-so-far + cap/stop-and-ask machinery;
# they would FAIL if the Phase-5 production path were reverted. The per-iteration
# code FIX is a LIVE Copilot action in real use; here a SCRIPTED change to the
# findings list stands in for that fix (clearly marked), exactly as prior phases
# materialized worked-example fixes. The DECISION machinery is real.
# ===========================================================================


def _mk(sev, cat="quality"):
    """Build a normalized scanner finding via scan.make_finding (same helper
    shape as test_scan._mk) so the gate judges a REAL canonical finding."""
    return scan.make_finding(sev, cat, "f.py", 1, "R1", "msg", "tool")


def _green_cycle_dict():
    """A PRECOMPUTED run_iteration_cycle result for an all-green verify.

    This mirrors the dict scaffold.decide_generation hands in as options['cycle']
    so the fused decision can judge the reviewer gate WITHOUT re-running verify.
    A single passing phase makes summary['ok'] True and gives score_iteration a
    real (empty) failing-phase set to score.
    """
    return {
        "status": "green",
        "iteration": None,
        "remaining_iterations": None,
        "summary": {
            "repo": "demo", "adapter": "python", "ok": True,
            "regression_delta": None,
            "phases": [{
                "phase": "baseline", "command": "python -m pytest",
                "returncode": 0, "passed": True,
                "counts": {"passed": 1, "failed": 0, "error": 0,
                           "skipped": 0, "no_tests": False},
                "stdout_tail": "1 passed", "stderr_tail": "",
            }],
        },
        "eval_data": None,
        "failure_report": None,
        "budget_advisory": {"budget": 4000, "estimated_tokens": 10,
                            "within_budget": True, "warning": None},
    }


# --- (1) fused acceptance: green verify + gate pass/fail ---------------------


def test_quality_gated_green_verify_gate_pass_is_converged(tmp_path):
    """GREEN verify + findings BELOW the threshold => status green/converged,
    carrying the passing reviewer gate and NO failure report."""
    findings = [_mk("low", "quality"), _mk("medium", "quality")]
    options = {
        "cycle": _green_cycle_dict(),
        "findings": findings,
        "thresholds": {"fail_on_severity": "high", "max_findings": {"security": 0}},
        "max_iterations": 3,
    }
    state = evalloop.LoopState()
    decision = evalloop.evaluate_quality_gated(str(tmp_path), None, options, state)
    assert decision["status"] == "green"
    # No failure report on a converged decision.
    assert "failure_report" not in decision or decision.get("failure_report") is None
    # The passing reviewer gate (scan.apply_thresholds output) rides along.
    assert decision["reviewer_gate"]["passed"] is True
    assert state.iteration == 1


def test_quality_gated_green_verify_gate_fail_folds_findings_into_report(tmp_path):
    """GREEN verify + a finding AT/ABOVE fail_on_severity => needs_fix, and the
    EXACT scanner findings AND the gate block are folded into the structured
    failure report while all pre-existing report keys are preserved (fail-loud:
    a green verify + a failing gate still produces a report)."""
    findings = [_mk("high", "quality"), _mk("low", "quality")]
    options = {
        "cycle": _green_cycle_dict(),
        "findings": findings,
        "thresholds": {"fail_on_severity": "high", "max_findings": {"security": 0}},
        "max_iterations": 3,
    }
    state = evalloop.LoopState()
    decision = evalloop.evaluate_quality_gated(str(tmp_path), None, options, state)
    assert decision["status"] == "needs_fix"
    report = decision["failure_report"]

    # The reviewer gate block (scan.apply_thresholds output) is folded in.
    assert report["reviewer_gate"]["passed"] is False
    assert "high" in report["reviewer_gate"]["failing_severities"]
    # The EXACT normalized findings are carried verbatim.
    assert report["scanner_findings"] == findings
    # All pre-existing build_failure_report keys are still present.
    for key in ("repo", "adapter", "ok", "failing_phases", "counts",
                "regression_delta", "golden_failures", "invariant_violations",
                "differential_regressions"):
        assert key in report, "missing pre-existing report key {!r}".format(key)
    # JSON-serializable (model-consumable).
    assert json.loads(json.dumps(report, default=str))


def test_quality_gated_security_over_max_findings_cap_fails(tmp_path):
    """A single SECURITY finding (below fail_on_severity) still FAILS the gate
    because review.thresholds caps security at 0 -- the max_findings path."""
    findings = [_mk("low", "security")]
    options = {
        "cycle": _green_cycle_dict(),
        "findings": findings,
        "thresholds": {"fail_on_severity": "critical",
                       "max_findings": {"security": 0}},
        "max_iterations": 3,
    }
    state = evalloop.LoopState()
    decision = evalloop.evaluate_quality_gated(str(tmp_path), None, options, state)
    assert decision["status"] == "needs_fix"
    gate = decision["failure_report"]["reviewer_gate"]
    assert gate["passed"] is False
    assert "security" in gate["failing_counts"]


def test_quality_gated_no_findings_degrades_to_tests_green(tmp_path):
    """Backward compatibility: no options['findings'] => reviewer gate NOT in
    play => the fused decision degrades to today's tests-green behavior (green on
    a green cycle, no reviewer_gate failure)."""
    options = {"cycle": _green_cycle_dict(), "max_iterations": 3}
    state = evalloop.LoopState()
    decision = evalloop.evaluate_quality_gated(str(tmp_path), None, options, state)
    assert decision["status"] == "green"
    # Gate not in play -> the reviewer_gate rides along as None.
    assert decision.get("reviewer_gate") is None


# --- (2) best-so-far selection across iterations -----------------------------


def test_quality_gated_tracks_best_so_far_not_last(tmp_path):
    """Drive the SAME state across three iterations whose scripted worked-example
    findings improve then regress: iter1 high, iter2 cleared to low (BEST), iter3
    regresses to high again. The tracked best-so-far must be iteration 2 (lowest
    residual severity), NOT the last attempt."""
    state = evalloop.LoopState()

    # Keep every iteration FAILING the gate (so none converges and all three are
    # ranked), while differing in residual severity. Under a 'medium' bar, iter2's
    # single medium is the LOWEST residual but still fails; iter1/iter3 are HIGH
    # residual. This isolates best-so-far ranking from early convergence.
    scripted = [
        [_mk("high", "quality"), _mk("high", "quality")],  # iter1: 2 high
        [_mk("medium", "quality")],                        # iter2: 1 medium BEST
        [_mk("high", "quality"), _mk("high", "quality")],  # iter3: 2 high again
    ]
    bar = {"fail_on_severity": "medium", "max_findings": {}}
    best_iters = []
    for findings in scripted:
        options = {"cycle": _green_cycle_dict(), "findings": findings,
                   "thresholds": bar, "max_iterations": 10}
        decision = evalloop.evaluate_quality_gated(str(tmp_path), None, options,
                                                   state)
        # Every scripted iteration fails the medium bar, so none converges.
        assert decision["status"] == "needs_fix", decision
        best_iters.append(state.best_so_far["iteration"])

    # The best-so-far settled on iteration 2 (the medium-only, lowest residual).
    assert state.best_so_far["iteration"] == 2, state.best_so_far
    assert state.best_so_far["score"]["severity_counts"] == {"medium": 1}
    # And it stayed iteration 2 even after iter3 regressed (not the last attempt).
    assert best_iters == [1, 2, 2]


# --- (3) cap => stop-and-ask carrying best-so-far + residual summary ---------


def test_quality_gated_cap_stops_and_asks_with_best_so_far(tmp_path):
    """A low cap with findings that NEVER clear the gate must STOP at the cap
    with reason max_iterations_reached, a SPECIFIC ask, and carry the BEST
    iteration reached (iteration 2, the lowest residual) plus a residual-findings
    summary -- not merely the last attempt."""
    bar = {"fail_on_severity": "medium", "max_findings": {}}
    state = evalloop.LoopState()
    scripted = [
        [_mk("high", "quality"), _mk("high", "quality")],  # iter1: 2 high
        [_mk("medium", "quality")],                        # iter2: 1 medium BEST
        [_mk("high", "quality")],                          # iter3: regresses (cap=3)
    ]
    final = None
    for findings in scripted:
        options = {"cycle": _green_cycle_dict(), "findings": findings,
                   "thresholds": bar, "max_iterations": 3}
        final = evalloop.evaluate_quality_gated(str(tmp_path), None, options,
                                               state)
    assert final["status"] == "stop"
    assert final["reason"] == "max_iterations_reached"
    assert state.iteration == 3  # never exceeded the cap
    # The ask is SPECIFIC: names the cap and the best-so-far residual summary.
    assert "max_iterations" in final["ask"]
    assert "best attempt was iteration 2" in final["ask"]
    # It carries the BEST attempt (iteration 2), not the last (iteration 3).
    assert final["best_so_far"]["iteration"] == 2
    assert final["best_so_far"]["score"]["severity_counts"] == {"medium": 1}
    # The residual summary describes the best attempt precisely.
    residual = evalloop._residual_summary(final["best_so_far"])
    assert "iteration 2" in residual
    assert "medium=1" in residual
    # The stop still carries the fused failure report for the human.
    assert final["failure_report"]["reviewer_gate"]["passed"] is False


# --- (4) config threshold tuning changes the converged/needs_fix outcome -----


def test_quality_gated_threshold_tuning_changes_outcome(tmp_path):
    """The SAME findings (one medium-severity quality finding) must FAIL a
    stricter bar (fail_on_severity: medium) and PASS a looser bar
    (fail_on_severity: critical) -- proving the quality bar is tunable and the
    tuning flips converged vs needs_fix. The quality bar IS the user-configurable
    definition of 'best'."""
    findings = [_mk("medium", "quality")]

    strict = {"fail_on_severity": "medium", "max_findings": {}}
    d_strict = evalloop.evaluate_quality_gated(
        str(tmp_path), None,
        {"cycle": _green_cycle_dict(), "findings": findings,
         "thresholds": strict, "max_iterations": 3},
        evalloop.LoopState())
    assert d_strict["status"] == "needs_fix"
    assert d_strict["failure_report"]["reviewer_gate"]["passed"] is False

    loose = {"fail_on_severity": "critical", "max_findings": {}}
    d_loose = evalloop.evaluate_quality_gated(
        str(tmp_path), None,
        {"cycle": _green_cycle_dict(), "findings": findings,
         "thresholds": loose, "max_iterations": 3},
        evalloop.LoopState())
    assert d_loose["status"] == "green"
    assert d_loose["reviewer_gate"]["passed"] is True


# --- (6-Python) EXECUTED fused loop: gate-fail -> scripted fix -> converge ----


def test_quality_gated_executed_fused_loop_fail_then_fix_then_converge(tmp_path):
    """EXECUTED fused-loop proof (Python): tests are GREEN but the reviewer gate
    FAILS on a worked-example high finding => needs_fix with the finding in the
    report; then a SCRIPTED 'fix' clears the findings (standing in for the LIVE
    Copilot fix) so the next iteration CONVERGES (green + gate passes). The
    decision/gate/report machinery is GENUINELY executed."""
    bar = {"fail_on_severity": "high", "max_findings": {"security": 0}}
    state = evalloop.LoopState()

    # --- Iteration 1: green verify, FAILING reviewer gate (worked-example). ---
    red = evalloop.evaluate_quality_gated(
        str(tmp_path), None,
        {"cycle": _green_cycle_dict(),
         "findings": [_mk("high", "security")],
         "thresholds": bar, "max_iterations": 3},
        state)
    assert red["status"] == "needs_fix", red
    assert red["failure_report"]["scanner_findings"][0]["severity"] == "high"
    assert red["failure_report"]["reviewer_gate"]["passed"] is False
    assert state.iteration == 1

    # --- SCRIPTED FIX (stands in for the LIVE Copilot fix): findings cleared. --
    green = evalloop.evaluate_quality_gated(
        str(tmp_path), None,
        {"cycle": _green_cycle_dict(), "findings": [],
         "thresholds": bar, "max_iterations": 3},
        state)
    assert green["status"] == "green", green
    assert green["reviewer_gate"]["passed"] is True
    assert state.iteration == 2  # one red + one green, within the cap


def test_quality_gated_executed_fused_loop_never_converges_hits_cap(tmp_path):
    """EXECUTED fused-loop proof: a never-converging reviewer gate hits the hard
    cap and STOPS with the best-so-far + residual summary (the DECISION/cap
    machinery is genuinely executed, never mocked)."""
    bar = {"fail_on_severity": "high", "max_findings": {"security": 0}}
    state = evalloop.LoopState()
    decisions = []
    for _ in range(10):  # far more than the cap of 2; the loop must self-limit
        d = evalloop.evaluate_quality_gated(
            str(tmp_path), None,
            {"cycle": _green_cycle_dict(),
             "findings": [_mk("high", "security")],
             "thresholds": bar, "max_iterations": 2},
            state)
        decisions.append(d)
        if d["status"] == "stop":
            break
    assert decisions[-1]["status"] == "stop"
    assert decisions[-1]["reason"] == "max_iterations_reached"
    assert state.iteration == 2  # never exceeded the hard cap
    assert decisions[-1]["best_so_far"] is not None
    assert "best attempt was iteration" in evalloop._residual_summary(
        decisions[-1]["best_so_far"])


# --- (7) the fused path still trips the no-LLM/no-network guard ---------------


def test_quality_gated_fused_path_imports_nothing_network_or_llm():
    """The fused decision must not introduce any network/LLM/model import. This
    extends the existing tripwire to the evaluate_quality_gated path explicitly
    (the function is defined in evalloop.py, already covered by the module-wide
    guard; this asserts the specific fused symbols exist and the source carries
    no forbidden token near them)."""
    assert hasattr(evalloop, "evaluate_quality_gated")
    assert hasattr(evalloop, "score_iteration")
    assert hasattr(evalloop, "resolve_review_thresholds")
    src = _read(os.path.join(SCRIPTS_DIR, "evalloop.py"))
    for token in ("import requests", "import openai", "import anthropic",
                  "import httpx", "urllib.request", "http.client",
                  "socket.socket", "api.openai.com", "chat/completions"):
        assert token not in src, \
            "fused path must not reference '{}'".format(token)
