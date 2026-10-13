"""Behavioral tests for the off-model orchestration engine (orchestrate.py).

Coverage (per FEAT-001):
  (a) build_graph validation: unique ids, unknown dep rejected, unknown type
      rejected, self-dependency rejected;
  (b) detect_cycles finds a cycle AND build/topological_order reject it
      fail-loud;
  (c) ready_tasks / topological_order selection correctness (stable + topo);
  (d) dependency unblocking: completing a task flips its dependent to ready;
  (e) persist/load/resume round-trip is lossless and a resumed run continues
      from the saved frontier;
  (f) the GLOBAL cap triggers a stop with a specific reason;
  (g) the per-task cap REUSES evalloop.resolve_max_iterations (same value);
  (h) a FAILED task HALTS with its specific ask and leaves dependents blocked
      (does not loop);
  (i) the audit trail contains the expected events in order and render_audit is
      human-readable;
  (j) gate-reuse: decide_task on a code task delegates to evalloop.evaluate and
      on a generation task to scaffold.decide_generation, recording the engine's
      own verdict verbatim (proving no duplicated threshold/cap logic);
  (k) a static no-network/no-LLM guard mirroring test_scaffold's guard EXACTLY.

orchestrate.py is a pure off-model module: it spawns NO subprocess of its own,
makes ZERO network/LLM calls, and composes the existing engines (evalloop /
scaffold / scan) for every gate decision rather than reimplementing them.
"""

import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import orchestrate  # noqa: E402
import evalloop  # noqa: E402
import scaffold  # noqa: E402


# --- helpers -----------------------------------------------------------------

def _linear_tasks():
    """a -> b -> c (c depends on b, b depends on a)."""
    return [
        {"id": "a", "type": "code"},
        {"id": "b", "type": "test", "deps": ["a"]},
        {"id": "c", "type": "review", "deps": ["b"]},
    ]


def _diamond_tasks():
    """a -> {b, c} -> d."""
    return [
        {"id": "a", "type": "ingest"},
        {"id": "b", "type": "code", "deps": ["a"]},
        {"id": "c", "type": "code", "deps": ["a"]},
        {"id": "d", "type": "review", "deps": ["b", "c"]},
    ]


# --- (a) build_graph validation ----------------------------------------------

def test_build_graph_normalizes_and_accepts_valid():
    graph = orchestrate.build_graph(_linear_tasks())
    assert [n["id"] for n in graph] == ["a", "b", "c"]
    for node in graph:
        # Every canonical key is present after normalization.
        for key in ("id", "type", "deps", "status", "result", "approvals",
                    "ask"):
            assert key in node
        assert node["status"] == "pending"


def test_build_graph_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        orchestrate.build_graph([
            {"id": "a", "type": "code"},
            {"id": "a", "type": "test"},
        ])


def test_build_graph_rejects_unknown_dep():
    with pytest.raises(ValueError):
        orchestrate.build_graph([
            {"id": "a", "type": "code", "deps": ["missing"]},
        ])


def test_build_graph_rejects_unknown_type():
    with pytest.raises(ValueError):
        orchestrate.build_graph([
            {"id": "a", "type": "not_a_type"},
        ])


def test_build_graph_rejects_self_dependency():
    with pytest.raises(ValueError):
        orchestrate.build_graph([
            {"id": "a", "type": "code", "deps": ["a"]},
        ])


# --- (b) cycle detection is fail-loud ----------------------------------------

def test_detect_cycles_finds_cycle_and_order_rejects_it():
    graph = orchestrate.build_graph([
        {"id": "a", "type": "code", "deps": ["c"]},
        {"id": "b", "type": "code", "deps": ["a"]},
        {"id": "c", "type": "code", "deps": ["b"]},
    ])
    cycle = orchestrate.detect_cycles(graph)
    assert cycle is not None
    # The reported path begins and ends on the same id (a real cycle).
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b", "c"}
    # topological_order and new_run_state both reject the cyclic graph fail-loud.
    with pytest.raises(ValueError):
        orchestrate.topological_order(graph)
    with pytest.raises(ValueError):
        orchestrate.new_run_state("r1", "goal", graph)


def test_detect_cycles_returns_none_for_acyclic():
    graph = orchestrate.build_graph(_diamond_tasks())
    assert orchestrate.detect_cycles(graph) is None


# --- (c) frontier + topological order ----------------------------------------

def test_ready_tasks_initial_frontier():
    graph = orchestrate.build_graph(_diamond_tasks())
    # Only 'a' (no deps) is ready initially.
    assert [n["id"] for n in orchestrate.ready_tasks(graph)] == ["a"]


def test_topological_order_is_stable_and_valid():
    graph = orchestrate.build_graph(_diamond_tasks())
    order = orchestrate.topological_order(graph)
    assert order[0] == "a"
    assert order[-1] == "d"
    # b and c (parallel middle) appear in stable id order.
    assert order.index("b") < order.index("c")
    # Every dependency precedes its dependent.
    pos = {tid: i for i, tid in enumerate(order)}
    index = {n["id"]: n for n in graph}
    for node in graph:
        for dep in node["deps"]:
            assert pos[dep] < pos[node["id"]]
    assert set(order) == set(index)


# --- (d) dependency unblocking -----------------------------------------------

def test_mark_task_done_unblocks_dependent():
    graph = orchestrate.build_graph(_linear_tasks())
    assert [n["id"] for n in orchestrate.ready_tasks(graph)] == ["a"]
    # Completing 'a' makes 'b' ready (its only dep is done); 'c' still waits.
    ready = orchestrate.mark_task(graph, "a", "done")
    assert ready == ["b"]
    ready = orchestrate.mark_task(graph, "b", "done")
    assert ready == ["c"]


def test_diamond_both_parents_required_before_join_is_ready():
    graph = orchestrate.build_graph(_diamond_tasks())
    orchestrate.mark_task(graph, "a", "done")
    assert set(n["id"] for n in orchestrate.ready_tasks(graph)) == {"b", "c"}
    # Completing only ONE parent does NOT make the join ready.
    orchestrate.mark_task(graph, "b", "done")
    assert [n["id"] for n in orchestrate.ready_tasks(graph)] == ["c"]
    # Completing the second parent makes the join ready.
    ready = orchestrate.mark_task(graph, "c", "done")
    assert ready == ["d"]


# --- (e) persist / load / resume round-trip ----------------------------------

def test_run_state_round_trip_is_lossless(tmp_path):
    graph = orchestrate.build_graph(_linear_tasks())
    run_state = orchestrate.new_run_state("run-1", "ship the feature", graph,
                                          global_max_tasks=5)
    path = str(tmp_path / "runs" / "run-1.json")
    orchestrate.save_run_state(path, run_state)
    loaded = orchestrate.load_run_state(path)
    assert loaded == run_state  # byte-for-byte object equality


def test_resumed_run_continues_from_saved_frontier(tmp_path):
    graph = orchestrate.build_graph(_linear_tasks())
    run_state = orchestrate.new_run_state("run-2", "goal", graph,
                                          global_max_tasks=10)
    path = str(tmp_path / "run-2.json")
    orchestrate.save_run_state(path, run_state)

    # Session 1: start + finish 'a', persist, "end session".
    rs1 = orchestrate.load_run_state(path)
    orchestrate.step_run(rs1, "a", "running")
    orchestrate.step_run(rs1, "a", "done")
    orchestrate.save_run_state(path, rs1)

    # Session 2 (fresh load): the frontier resumes at 'b'.
    rs2 = orchestrate.load_run_state(path)
    assert [n["id"] for n in orchestrate.ready_tasks(rs2["tasks"])] == ["b"]
    assert rs2["status"] == "running"
    # And it can keep going to completion.
    orchestrate.step_run(rs2, "b", "done")
    out = orchestrate.step_run(rs2, "c", "done")
    assert out["run_status"] == "done"


# --- (f) global cap halts fail-loud ------------------------------------------

def test_global_cap_triggers_stop_with_specific_reason(tmp_path):
    graph = orchestrate.build_graph(_diamond_tasks())
    # Cap of 2: after 2 tasks are consumed with work still pending, HALT.
    run_state = orchestrate.new_run_state("run-cap", "goal", graph,
                                          global_max_tasks=2)
    orchestrate.step_run(run_state, "a", "done")
    out = orchestrate.step_run(run_state, "b", "done")
    assert out["halted"] is True
    assert out["run_status"] == "stopped"
    assert "global_cap_reached" in out["stop_reason"]
    assert run_state["status"] == "stopped"
    # The cap event is recorded in the audit trail.
    events = [e["event"] for e in run_state["audit"]]
    assert "global_cap_reached" in events


# --- (g) per-task cap reuse --------------------------------------------------

def test_per_task_cap_reuses_evalloop_resolve_max_iterations():
    graph = orchestrate.build_graph(_linear_tasks())
    run_state = orchestrate.new_run_state("run-cap2", "goal", graph)
    # The run-state records the SAME per-task cap evalloop resolves -- there is
    # no second iteration cap defined in orchestrate.py.
    assert (run_state["per_task_max_iterations"]
            == evalloop.resolve_max_iterations())


def test_resolve_global_max_tasks_is_always_positive_int():
    assert orchestrate.resolve_global_max_tasks(override=0) == 1
    assert orchestrate.resolve_global_max_tasks(override=-5) == 1
    assert orchestrate.resolve_global_max_tasks(override="bad") >= 1
    assert isinstance(orchestrate.resolve_global_max_tasks(), int)


# --- (h) failed task halts with its ask, dependents stay blocked -------------

def test_failed_task_halts_with_ask_and_blocks_dependents(tmp_path):
    graph = orchestrate.build_graph(_linear_tasks())
    run_state = orchestrate.new_run_state("run-fail", "goal", graph,
                                          global_max_tasks=10)
    orchestrate.step_run(run_state, "a", "running")
    out = orchestrate.step_run(
        run_state, "a", "failed",
        ask="verify stayed red at the cap; adjust the approach?")
    assert out["halted"] is True
    assert out["run_status"] == "failed"
    assert "verify stayed red" in out["stop_reason"]
    assert run_state["status"] == "failed"
    # Dependents 'b' and 'c' are BLOCKED (not ready) -- the run does not loop.
    index = {n["id"]: n for n in run_state["tasks"]}
    assert index["b"]["status"] == "blocked"
    assert index["c"]["status"] == "blocked"
    assert orchestrate.ready_tasks(run_state["tasks"]) == []


# --- (i) audit trail ----------------------------------------------------------

def test_audit_trail_has_expected_events_in_order_and_renders(tmp_path):
    graph = orchestrate.build_graph(_linear_tasks())
    run_state = orchestrate.new_run_state("run-audit", "ship it", graph,
                                          global_max_tasks=10)
    orchestrate.step_run(run_state, "a", "running")
    orchestrate.step_run(run_state, "a", "done")
    orchestrate.step_run(run_state, "b", "running")
    orchestrate.step_run(run_state, "b", "done")
    orchestrate.step_run(run_state, "c", "done")

    events = [e["event"] for e in run_state["audit"]]
    # The graph_built opener comes first and run_done closes it.
    assert events[0] == "graph_built"
    assert events[-1] == "run_done"
    for expected in ("task_running", "task_done"):
        assert expected in events
    # Order sanity: a's running precedes a's done.
    run_idx = next(i for i, e in enumerate(run_state["audit"])
                   if e["event"] == "task_running" and e["task_id"] == "a")
    done_idx = next(i for i, e in enumerate(run_state["audit"])
                    if e["event"] == "task_done" and e["task_id"] == "a")
    assert run_idx < done_idx

    text = orchestrate.render_audit(run_state)
    assert "APIA orchestrator run audit" in text
    assert "ship it" in text
    assert "graph_built" in text
    # Human-readable: one line per audit entry (plus the header block).
    assert text.count("\n") >= len(run_state["audit"])


# --- (j) gate-reuse: decide_task delegates, records verdict verbatim ---------

def test_decide_task_code_delegates_to_evalloop_evaluate(monkeypatch, tmp_path):
    # Drive evalloop deterministically (same technique test_evalloop uses): a
    # scripted green cycle. decide_task must record evalloop.evaluate's OWN
    # verdict unchanged -- proving it routes, not re-decides.
    def _green_cycle(repo, adapter, options):
        return {
            "status": "green", "iteration": options.get("iteration"),
            "remaining_iterations": options.get("remaining_iterations"),
            "summary": {"ok": True}, "eval_data": None, "failure_report": None,
            "budget_advisory": {"budget": 4000, "estimated_tokens": 5,
                                "within_budget": True, "warning": None},
        }

    monkeypatch.setattr(evalloop, "run_iteration_cycle", _green_cycle)

    task = {"id": "t1", "type": "code"}
    inputs = {"adapter": {"stack": "python"},
              "options": {"max_iterations": 3},
              "state": evalloop.LoopState()}
    decision = orchestrate.decide_task(task, str(tmp_path), inputs)
    assert decision["engine"] == "evalloop.evaluate"
    assert decision["task_id"] == "t1"
    # The recorded verdict IS evalloop.evaluate's output shape (green here).
    assert decision["verdict"]["status"] == "green"
    assert decision["verdict"]["max_iterations"] == 3


def test_decide_task_generation_delegates_to_scaffold(tmp_path):
    # A confirmed, gate-passing, verified generation -> scaffold.decide_generation
    # returns 'green'; decide_task records that verdict verbatim.
    task = {"id": "g1", "type": "generation"}
    inputs = {
        "spec": {},
        "verify_result": {"ok": True, "partial": False},
        "findings": [],
        "acceptance": {"is_new_behavior": True, "confirmed": True,
                       "criteria": []},
        "state": evalloop.LoopState(),
    }
    decision = orchestrate.decide_task(task, str(tmp_path), inputs)
    assert decision["engine"] == "scaffold.decide_generation"
    # The verdict matches scaffold's OWN decision for the same inputs (no
    # duplicated gate/cap logic in orchestrate.py).
    expected = scaffold.decide_generation(
        {}, {"ok": True, "partial": False}, [],
        acceptance={"is_new_behavior": True, "confirmed": True, "criteria": []},
        repo_path=str(tmp_path), state=evalloop.LoopState())
    assert decision["verdict"]["status"] == expected["status"] == "green"
    assert decision["verdict"]["gate"] == expected["gate"]


def test_decide_task_live_stage_records_verdict_unchanged(tmp_path):
    task = {"id": "i1", "type": "ingest"}
    verdict = {"knowledge": "distilled", "confidence": 0.9}
    decision = orchestrate.decide_task(task, str(tmp_path),
                                       {"verdict": verdict})
    assert decision["engine"] == "live_stage"
    assert decision["verdict"] == verdict


def test_decide_task_rejects_unknown_type(tmp_path):
    with pytest.raises(ValueError):
        orchestrate.decide_task({"id": "x", "type": "mystery"},
                                str(tmp_path), {})


# --- (k) static no-network / no-LLM guard ------------------------------------

def test_orchestrate_makes_no_network_or_llm_calls():
    """Static guard: orchestrate.py must not import network/LLM SDKs or endpoints.

    This mirrors test_scaffold.test_scaffold_makes_no_network_or_llm_calls
    EXACTLY (same forbidden-substring tripwire list). It is a tripwire, not a
    proof: the real guarantee is structural -- orchestrate.py is stdlib + guarded
    PyYAML only, spawns no subprocess of its own, and composes the existing
    engines for every gate decision (see the module docstring's HARD
    CONSTRAINT)."""
    src_path = os.path.join(SCRIPTS_DIR, "orchestrate.py")
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
        assert token not in src, \
            "orchestrate.py must not reference '{}'".format(token)
