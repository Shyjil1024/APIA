#!/usr/bin/env python3
"""APIA off-model ORCHESTRATION engine (Phase-4, deterministic half).

This script is the DETERMINISTIC conductor for APIA's multi-task autonomy. It is
the task-graph / run-state / resume / stop / audit counterpart to scan.py (the
Reviewer engine), scaffold.py (the generation engine) and evalloop.py (the
self-correction loop): it does ONLY mechanical, reproducible work -- it builds
and validates a task graph, picks ready tasks in a stable topological order,
detects cycles, persists and reloads a resumable run-state document, enforces a
global stop condition plus the per-task iteration cap, and produces a
human-readable audit trail. It never reasons, never writes feature code, and
never invents correctness.

MODEL vs OFF-MODEL SPLIT (the same discipline as scan.py / scaffold.py /
evalloop.py). The LIVE Copilot session does the CREATIVE / per-task half --
planning what the tasks are from a goal, and for each task doing the actual
reasoning / generation / fix work (ingest, clarify, modify, generate, review),
expressed in the orchestrator agent / skill / prompt. This script does the
MECHANICAL half:

  (a) build_graph(tasks)        - validate + normalize the task graph (unique
                                  ids, deps reference existing ids, known types).
  (b) detect_cycles(graph)      - return the offending cycle path (fail-loud: a
                                  cyclic graph is REJECTED, never silently run).
  (c) ready_tasks / topological_order - the topological frontier + a full stable
                                  deterministic order (raises on a cycle).
  (d) mark_task(graph, ...)     - a single state transition + dependency
                                  UNBLOCKING; a `failed` task leaves dependents
                                  BLOCKED and the run HALTS with its ask.
  (e) new_run_state / save_run_state / load_run_state - a lossless, resumable
                                  JSON run-state document + audit trail.
  (f) decide_task(task, ...)    - GATE-REUSE wiring: ROUTE a code/test task to
                                  evalloop.evaluate and a generation task to
                                  scaffold.generation_gate / scaffold.decide_
                                  generation, recording their verdict verbatim.

HARD CONSTRAINT (identical to ingest.py / scan.py / scaffold.py / evalloop.py;
see docs/ARCHITECTURE.md sections 1-4): this module performs ZERO network and
ZERO LLM/model calls. It NEVER imports any HTTP client, networking SDK, or model
SDK (no requests / urllib networking / openai / anthropic / an async HTTP client
/ low-level HTTP / raw sockets), and it NEVER reaches a remote endpoint. Model
inference always runs
on the user's existing Copilot session. This module NEVER itself executes the
per-task LLM work and it spawns NO subprocess of its own: any build/test runs
only INDIRECTLY through verify.py's existing ``_run()`` chokepoint, reached via
the engines this module composes (evalloop / scaffold). The orchestrator is the
conductor, not a new implementation of the stages it drives.

    # assert: no network/LLM calls anywhere in this module -- no subprocess is
    # spawned here; any build/test subprocess goes through verify.py's _run()
    # via the reused engines.

Usage:
    python3 .apia/scripts/orchestrate.py --init GOAL --tasks TASKS.json \
        --run-state PATH [--json]
    python3 .apia/scripts/orchestrate.py --step --run-state PATH --task-id ID \
        --status STATUS [--result RESULT.json] [--ask TEXT] [--json]
    python3 .apia/scripts/orchestrate.py --status --run-state PATH [--json]
    python3 .apia/scripts/orchestrate.py --audit --run-state PATH
    python3 .apia/scripts/orchestrate.py --doctor [--json]

Exit 0 on a clean / continuing run, non-zero when the run is stopped/failed
(mirrors scan.py / scaffold.py).
"""

# NOTE: standard-library only (plus the optional PyYAML import guarded in
# _load_config for reading config.yml). There is deliberately NO networking/LLM
# import here -- see the module docstring's HARD CONSTRAINT. The scripts dir is
# this file's directory; make it importable regardless of the caller's cwd
# (mirrors scan.py / scaffold.py / evalloop.py). These are LOCAL stdlib-only
# modules whose own guard tests enforce no network/LLM calls.
import argparse
import datetime
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import scan      # noqa: E402  (apply_thresholds -- the single severity gate)
import evalloop  # noqa: E402  (resolve_max_iterations + evaluate -- per-task loop)

try:
    import scaffold  # noqa: E402  (generation_gate / decide_generation -- gen gate)
except ImportError:  # pragma: no cover - scaffold ships alongside
    scaffold = None

try:
    import ingest  # noqa: E402  (reused for the shared doctor() preflight)
except ImportError:  # pragma: no cover - ingest ships alongside
    ingest = None


# ---------------------------------------------------------------------------
# Vocabulary + defaults.
# ---------------------------------------------------------------------------

# Task types the orchestrator knows how to ROUTE. code/test flow through the
# eval/self-correction loop; generation flows through the scaffold gate; ingest/
# review are live Copilot stages whose verdict is recorded as supplied.
TASK_TYPES = ("code", "generation", "ingest", "review", "test")

# A task's lifecycle status.
TASK_STATUSES = ("pending", "running", "blocked", "done", "failed")

# The run-level status.
RUN_STATUSES = ("running", "stopped", "done", "failed")

# Fallback defaults when config.yml is unreadable (mirrors the committed
# .apia/config.yml so the run is ALWAYS bounded even without PyYAML).
_DEFAULT_GLOBAL_MAX_TASKS = 20
_DEFAULT_RUN_STATE_DIR = "runs/"


# ---------------------------------------------------------------------------
# Config resolution (REPO-FIRST, mirroring scaffold._load_config /
# evalloop._load_config).
# ---------------------------------------------------------------------------

def _package_config_path():
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    return os.path.join(apia_dir, "config.yml")


def _load_config(repo_path=None):
    """Return the merged config dict (REPO-FIRST, package fallback), or {}.

    Resolution mirrors scaffold._load_config / evalloop._load_config exactly: the
    target repo's own ``.apia/config.yml`` wins, then the APIA package config is
    the fallback. Returns the FIRST config file that parses to a dict, or {} when
    PyYAML is unavailable (callers fall back to the hard-coded defaults so the
    run stays bounded).
    """
    try:
        import yaml
    except ImportError:
        return {}
    candidates = []
    if repo_path:
        candidates.append(os.path.join(repo_path, ".apia", "config.yml"))
    candidates.append(_package_config_path())
    for cfg in candidates:
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_orchestrate_config(repo_path=None):
    """Return the merged orchestration settings with safe defaults.

    REPO-FIRST config resolution (same pattern as scaffold.resolve_supported_
    shapes). Reads the top-level ``orchestrate`` block from config.yml WHEN
    PRESENT and fills in safe defaults when the block (or a key) is absent, so
    this engine works before the config is wired. Returns::

        {global_max_tasks: int, run_state_dir: str}

    ``global_max_tasks`` is always a positive int (the global stop bound) and
    ``run_state_dir`` is always a non-empty string (the committable run-state
    location, relative to .apia/). The PER-TASK iteration cap is NOT returned
    here on purpose: it REUSES evalloop.resolve_max_iterations (there is no
    second cap).
    """
    cfg = _load_config(repo_path)
    block = cfg.get("orchestrate") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        block = {}

    gmax = block.get("global_max_tasks", _DEFAULT_GLOBAL_MAX_TASKS)
    try:
        gmax = int(gmax)
    except (TypeError, ValueError):
        gmax = _DEFAULT_GLOBAL_MAX_TASKS
    gmax = max(1, gmax)

    run_dir = block.get("run_state_dir", _DEFAULT_RUN_STATE_DIR)
    if not isinstance(run_dir, str) or not run_dir.strip():
        run_dir = _DEFAULT_RUN_STATE_DIR

    return {"global_max_tasks": gmax, "run_state_dir": run_dir}


def resolve_global_max_tasks(repo_path=None, override=None):
    """Return the GLOBAL stop bound (always a positive int).

    Mirrors evalloop.resolve_max_iterations' discipline: an explicit ``override``
    (e.g. a proof run) wins; otherwise the repo-first ``orchestrate.global_max_
    tasks`` is used, falling back to the default. A bad/zero/negative value can
    never turn the bound off -- the run is ALWAYS globally bounded.
    """
    if override is not None:
        try:
            value = int(override)
        except (TypeError, ValueError):
            value = _DEFAULT_GLOBAL_MAX_TASKS
        return max(1, value)
    return resolve_orchestrate_config(repo_path)["global_max_tasks"]


def resolve_run_state_dir(repo_path=None):
    """Return the committable run-state directory (relative to .apia/).

    Defaults to ``runs/`` (consistent with how knowledge_dir is committed under
    .apia/). The caller joins this under the resolved .apia/ directory.
    """
    return resolve_orchestrate_config(repo_path)["run_state_dir"]


# ---------------------------------------------------------------------------
# (a)/(b)/(c) TASK GRAPH model, validation, cycles, frontier + order.
# ---------------------------------------------------------------------------

def _normalize_task(task):
    """Return a normalized task node dict, filling the canonical keys.

    A task node has keys {id, type, deps, status, result, approvals, ask}. This
    normalizes a (possibly sparse) input dict into the full shape with safe
    defaults WITHOUT validating it (build_graph validates). Pure dict work.
    """
    task = dict(task or {})
    node = {
        "id": task.get("id"),
        "type": task.get("type"),
        "deps": list(task.get("deps") or []),
        "status": task.get("status") or "pending",
        "result": task.get("result"),
        "approvals": list(task.get("approvals") or []),
        "ask": task.get("ask"),
    }
    return node


def build_graph(tasks):
    """Validate + normalize a list of task dicts into a task graph.

    FAIL-LOUD validation (raises ValueError, never silently drops a bad task):
      * every task has a non-empty ``id`` and the ids are UNIQUE;
      * every ``type`` is one of TASK_TYPES;
      * every ``status`` (when present) is one of TASK_STATUSES;
      * every id in a task's ``deps`` references an existing task id, and no task
        depends on itself.

    Returns a graph: an ORDERED list of normalized task nodes (input order
    preserved; selection functions sort by id for determinism). This does NOT run
    cycle detection -- call detect_cycles / topological_order for that (both are
    fail-loud on a cycle).
    """
    if not isinstance(tasks, (list, tuple)):
        raise ValueError("tasks must be a list of task dicts")
    graph = [_normalize_task(t) for t in tasks]

    seen = set()
    for node in graph:
        tid = node["id"]
        if not tid or not isinstance(tid, str):
            raise ValueError("every task needs a non-empty string id; got "
                             "{!r}".format(tid))
        if tid in seen:
            raise ValueError("duplicate task id {!r}".format(tid))
        seen.add(tid)
        if node["type"] not in TASK_TYPES:
            raise ValueError("task {!r} has unknown type {!r}; known types are "
                             "{}".format(tid, node["type"], list(TASK_TYPES)))
        if node["status"] not in TASK_STATUSES:
            raise ValueError("task {!r} has unknown status {!r}; known statuses "
                             "are {}".format(tid, node["status"],
                                             list(TASK_STATUSES)))

    for node in graph:
        tid = node["id"]
        for dep in node["deps"]:
            if dep == tid:
                raise ValueError("task {!r} depends on itself".format(tid))
            if dep not in seen:
                raise ValueError("task {!r} depends on unknown task id "
                                 "{!r}".format(tid, dep))
    return graph


def _index(graph):
    """Return an {id: node} index for a graph."""
    return {node["id"]: node for node in graph}


def detect_cycles(graph):
    """Return a dependency cycle path as a list of ids, or None if acyclic.

    Deterministic DFS over the dependency edges (a task depends-on its deps). The
    returned path is the offending cycle (e.g. ['a', 'b', 'a']) so the caller can
    name it when rejecting the graph. Returns None when the graph is acyclic.
    Nodes are visited in stable id order so the reported cycle is deterministic.
    """
    index = _index(graph)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in index}
    stack = []

    def visit(tid):
        color[tid] = GREY
        stack.append(tid)
        for dep in sorted(index[tid]["deps"]):
            if dep not in index:
                # Unknown dep is a build_graph concern; ignore here.
                continue
            if color[dep] == GREY:
                # Found a back-edge: slice the stack from the first occurrence.
                start = stack.index(dep)
                return stack[start:] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[tid] = BLACK
        return None

    for tid in sorted(index):
        if color[tid] == WHITE:
            found = visit(tid)
            if found:
                return found
    return None


def topological_order(graph):
    """Return a full deterministic topological order of task ids.

    Kahn's algorithm with a stable id-sorted frontier so the order is
    deterministic for a given graph. FAIL-LOUD: raises ValueError (naming the
    offending cycle) when the graph is cyclic -- a cyclic graph is never silently
    ordered or run.
    """
    cycle = detect_cycles(graph)
    if cycle is not None:
        raise ValueError("task graph has a cycle: {}".format(" -> ".join(cycle)))
    index = _index(graph)
    indegree = {tid: 0 for tid in index}
    # A depends-on-dep edge means dep must come BEFORE the task.
    for node in graph:
        indegree[node["id"]] = len(node["deps"])
    dependents = {tid: [] for tid in index}
    for node in graph:
        for dep in node["deps"]:
            dependents[dep].append(node["id"])

    ready = sorted([tid for tid, d in indegree.items() if d == 0])
    order = []
    while ready:
        tid = ready.pop(0)
        order.append(tid)
        for child in sorted(dependents[tid]):
            indegree[child] -= 1
            if indegree[child] == 0:
                # Insert keeping the frontier id-sorted (stable determinism).
                ready.append(child)
                ready.sort()
    if len(order) != len(index):  # pragma: no cover - cycle caught above
        raise ValueError("task graph has a cycle (unresolved: {})".format(
            sorted(set(index) - set(order))))
    return order


def ready_tasks(graph):
    """Return the topological FRONTIER: pending tasks whose deps are all done.

    A task is ready when its status is ``pending`` and every dependency is
    ``done``. Returned in stable id order so selection is deterministic. This is
    the set the orchestrator may dispatch next (the live Copilot session executes
    exactly one of them, then calls mark_task).
    """
    index = _index(graph)
    out = []
    for node in sorted(graph, key=lambda n: n["id"]):
        if node["status"] != "pending":
            continue
        if all(index[dep]["status"] == "done" for dep in node["deps"]):
            out.append(node)
    return out


# ---------------------------------------------------------------------------
# (d) STATE TRANSITIONS + dependency unblocking.
# ---------------------------------------------------------------------------

def _recompute_blocked(graph):
    """Recompute pending<->blocked flags from dependency state (in place).

    A pending/blocked task is BLOCKED when any dependency is ``failed`` (directly
    or transitively) -- its path to running is severed, so it must not appear in
    the ready frontier. Otherwise it is pending (its deps may simply not be done
    yet; it becomes ready once they all are). ``running``/``done``/``failed``
    tasks are never touched. Returns the set of ids whose status changed.
    """
    index = _index(graph)

    # A task is "poisoned" when any transitive dependency has failed.
    poisoned = set()
    changed = True
    while changed:
        changed = False
        for node in graph:
            if node["id"] in poisoned:
                continue
            for dep in node["deps"]:
                dep_node = index.get(dep)
                if dep_node is None:
                    continue
                if dep_node["status"] == "failed" or dep in poisoned:
                    poisoned.add(node["id"])
                    changed = True
                    break

    flipped = set()
    for node in graph:
        if node["status"] in ("running", "done", "failed"):
            continue
        want = "blocked" if node["id"] in poisoned else "pending"
        if node["status"] != want:
            node["status"] = want
            flipped.add(node["id"])
    return flipped


def mark_task(graph, task_id, status, result=None, ask=None):
    """Apply ONE state transition to a task and recompute dependency blocking.

    Updates ``task_id`` to ``status`` (one of TASK_STATUSES), optionally
    recording ``result`` and/or ``ask``, then recomputes which pending/blocked
    tasks are blocked by a failed dependency. Completing a task ``done`` unblocks
    dependents whose remaining deps are all done (they become ready via
    ready_tasks). Setting a task ``failed`` leaves its dependents BLOCKED (not
    ready) so the run HALTS with that task's specific ``ask`` rather than looping.

    FAIL-LOUD: raises ValueError on an unknown task id or an unknown status.
    Returns the list of task ids that are now READY (the recomputed frontier) so
    the caller can see what the transition unblocked.
    """
    if status not in TASK_STATUSES:
        raise ValueError("unknown status {!r}; known statuses are {}".format(
            status, list(TASK_STATUSES)))
    index = _index(graph)
    node = index.get(task_id)
    if node is None:
        raise ValueError("unknown task id {!r}".format(task_id))

    node["status"] = status
    if result is not None:
        node["result"] = result
    if ask is not None:
        node["ask"] = ask

    _recompute_blocked(graph)
    return [n["id"] for n in ready_tasks(graph)]


# ---------------------------------------------------------------------------
# (e) RESUMABLE run-state + audit trail.
# ---------------------------------------------------------------------------

def _now():
    """Return a stable ISO-8601 UTC timestamp (no network clock)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _audit_entry(event, task_id=None, detail=None):
    """Build one audit entry {ts, event, task_id, detail}."""
    return {"ts": _now(), "event": event, "task_id": task_id,
            "detail": detail}


def append_audit(run_state, event, task_id=None, detail=None):
    """Append an audit entry to a run-state and return it.

    Every state transition records one entry so a stopped run is fully
    inspectable on resume. ``event`` is one of the documented events
    (graph_built, task_ready, task_running, task_done, task_failed,
    task_blocked, global_cap_reached, run_stopped, run_resumed, run_done, ...).
    """
    entry = _audit_entry(event, task_id=task_id, detail=detail)
    run_state.setdefault("audit", []).append(entry)
    run_state["updated"] = entry["ts"]
    return entry


def new_run_state(run_id, goal, graph, repo_path=None, global_max_tasks=None):
    """Produce the initial run-state document for a validated graph.

    Captures the resolved bounds (global cap + the REUSED per-task iteration cap
    from evalloop.resolve_max_iterations), the normalized task nodes, and an
    opening ``graph_built`` audit entry. Run-state schema::

        {run_id, goal, repo, created, updated, global_max_tasks,
         per_task_max_iterations, status, stop_reason, tasks, audit}

    The graph is validated for cycles here (fail-loud) so a cyclic graph can
    never be persisted as a runnable run-state.
    """
    # Validate acyclicity up front (raises on a cycle).
    topological_order(graph)
    gmax = resolve_global_max_tasks(repo_path, override=global_max_tasks)
    per_task = evalloop.resolve_max_iterations(repo_path)
    ts = _now()
    run_state = {
        "run_id": run_id,
        "goal": goal,
        "repo": os.path.abspath(repo_path) if repo_path else None,
        "created": ts,
        "updated": ts,
        "global_max_tasks": gmax,
        "per_task_max_iterations": per_task,
        "status": "running",
        "stop_reason": None,
        "tasks": [dict(n) for n in graph],
        "audit": [],
    }
    append_audit(run_state, "graph_built",
                 detail="validated graph of {} task(s); global_max_tasks={}, "
                        "per_task_max_iterations={}".format(
                            len(graph), gmax, per_task))
    for node in ready_tasks(run_state["tasks"]):
        append_audit(run_state, "task_ready", task_id=node["id"])
    return run_state


def save_run_state(path, run_state):
    """Write ``run_state`` to ``path`` as pretty JSON, atomically.

    Writes to a sibling temp file then os.replace()s it into place so a crash
    mid-write never leaves a half-written run-state. Creates parent dirs. The
    round-trip with load_run_state is lossless.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(run_state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def load_run_state(path):
    """Load a run-state JSON document from ``path`` (lossless round-trip)."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def render_audit(run_state):
    """Return the audit trail as a plain-text, chronological, human log.

    One line per entry: ``<ts>  <event>  [<task_id>]  <detail>``. Readable by a
    human reviewing a stopped/resumed run without parsing JSON.
    """
    lines = [
        "APIA orchestrator run audit",
        "  run_id : {}".format(run_state.get("run_id")),
        "  goal   : {}".format(run_state.get("goal")),
        "  status : {}".format(run_state.get("status")),
    ]
    stop_reason = run_state.get("stop_reason")
    if stop_reason:
        lines.append("  stop   : {}".format(stop_reason))
    lines.append("")
    for entry in run_state.get("audit", []):
        tid = entry.get("task_id")
        tid_part = "[{}]".format(tid) if tid else "[-]"
        detail = entry.get("detail")
        detail_part = "  {}".format(detail) if detail else ""
        lines.append("{ts}  {event:<18}{tid}{detail}".format(
            ts=entry.get("ts"), event=entry.get("event", ""),
            tid=tid_part, detail=detail_part))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# (f) RUN STEPPING: one transition + global stop condition.
# ---------------------------------------------------------------------------

def _count_consumed(run_state):
    """Return how many tasks have been moved to running/done/failed.

    This is the measure the GLOBAL cap bounds: a task that has started counts
    against the budget regardless of whether it finished (so a long run cannot
    exceed the bound by re-dispatching).
    """
    return sum(1 for t in run_state["tasks"]
               if t["status"] in ("running", "done", "failed"))


def step_run(run_state, task_id, status, result=None, ask=None):
    """Apply one transition to a persisted run-state + enforce the stop condition.

    Mutates ``run_state`` in place: records the transition via mark_task, appends
    the matching audit event, then evaluates the GLOBAL stop condition:

      (a) a task set ``failed`` -> HALT: run status ``failed``, stop_reason names
          the task + its ask (dependents are left BLOCKED by mark_task);
      (b) the number of tasks moved to running/done reaching the GLOBAL cap ->
          HALT: run status ``stopped``, stop_reason ``global_cap_reached``;
      (c) no ready tasks remain and not all tasks are done -> HALT (deadlock /
          blocked): run status ``stopped`` with a specific ask;
      (d) all tasks done -> run status ``done``;
      (e) otherwise the run keeps ``running`` and the recomputed frontier is
          returned.

    Returns a dict::

        {ready: [ids], run_status, stop_reason|None, halted: bool}

    The PER-TASK iteration cap is NOT enforced here -- it is owned by
    evalloop.evaluate (reached via decide_task); this function enforces only the
    GLOBAL bound + fail/deadlock halts.
    """
    event = {
        "running": "task_running",
        "done": "task_done",
        "failed": "task_failed",
        "blocked": "task_blocked",
        "pending": "task_ready",
    }.get(status, "task_" + status)

    ready = mark_task(run_state["tasks"], task_id, status, result=result,
                      ask=ask)
    append_audit(run_state, event, task_id=task_id,
                 detail=(ask if ask else None))

    tasks = run_state["tasks"]
    index = _index(tasks)
    node = index[task_id]

    # (a) A failed task halts the whole run with its specific ask.
    if status == "failed":
        reason = "task {!r} failed: {}".format(
            task_id, node.get("ask") or "no ask recorded")
        run_state["status"] = "failed"
        run_state["stop_reason"] = reason
        append_audit(run_state, "run_stopped", task_id=task_id, detail=reason)
        return {"ready": ready, "run_status": "failed", "stop_reason": reason,
                "halted": True}

    # (b) Global cap: too many tasks have been consumed.
    consumed = _count_consumed(run_state)
    if consumed >= run_state["global_max_tasks"]:
        all_done = all(t["status"] == "done" for t in tasks)
        if not all_done:
            reason = ("global_cap_reached: {} task(s) consumed reached the "
                      "global_max_tasks bound of {}; stopping fail-loud rather "
                      "than continuing".format(
                          consumed, run_state["global_max_tasks"]))
            run_state["status"] = "stopped"
            run_state["stop_reason"] = reason
            append_audit(run_state, "global_cap_reached", detail=reason)
            append_audit(run_state, "run_stopped", detail=reason)
            return {"ready": ready, "run_status": "stopped",
                    "stop_reason": reason, "halted": True}

    # (d) All tasks done -> the run is complete.
    if all(t["status"] == "done" for t in tasks):
        run_state["status"] = "done"
        run_state["stop_reason"] = None
        append_audit(run_state, "run_done",
                     detail="all {} task(s) done".format(len(tasks)))
        return {"ready": ready, "run_status": "done", "stop_reason": None,
                "halted": True}

    # (c) Deadlock: nothing is ready and not everything is done.
    running = [t for t in tasks if t["status"] == "running"]
    if not ready and not running:
        blocked = [t["id"] for t in tasks if t["status"] == "blocked"]
        reason = ("deadlock: no ready tasks remain and not all tasks are done "
                  "(blocked: {}). A dependency failed or the graph cannot "
                  "progress; resolve the blocked task(s) to continue.".format(
                      blocked))
        run_state["status"] = "stopped"
        run_state["stop_reason"] = reason
        append_audit(run_state, "run_stopped", detail=reason)
        return {"ready": ready, "run_status": "stopped", "stop_reason": reason,
                "halted": True}

    # (e) Still running: announce the newly-ready frontier.
    for tid in ready:
        append_audit(run_state, "task_ready", task_id=tid)
    run_state["status"] = "running"
    return {"ready": ready, "run_status": "running", "stop_reason": None,
            "halted": False}


# ---------------------------------------------------------------------------
# GATE-REUSE wiring: route to the EXISTING engines; never re-decide.
# ---------------------------------------------------------------------------

def decide_task(task, repo_path, inputs):
    """Route a task to the EXISTING gate engine and record its verdict verbatim.

    This COMPOSES the existing gates; it NEVER re-implements a threshold or cap:

      * ``code`` / ``test`` -> evalloop.evaluate(repo_path, adapter, options,
        state). evaluate now delegates to the SHARED fused decision
        evalloop.evaluate_quality_gated, which enforces the HARD max_iterations
        cap AND the reviewer gate (scan.apply_thresholds against review.thresholds)
        when the LIVE scanner findings are threaded through
        ``inputs['options']['findings']`` -- so "best" (green tests + reviewer
        gate) means the SAME thing here as in the standalone loop and the
        generation gate. decide_task passes the LIVE Copilot per-task work IN as
        ``inputs`` (adapter, options incl. optional findings, state), since the
        model executed verify/eval/fix in a live session -- this script never
        runs it, and the fused verdict is recorded VERBATIM.

      * ``generation`` -> scaffold.generation_gate / scaffold.decide_generation.
        The LIVE generation + its verification result + reviewer findings arrive
        in ``inputs`` (spec, verify_result, findings, acceptance, state) and the
        verdict is scaffold's own, recorded unchanged.

      * ``ingest`` / ``review`` -> no deterministic gate engine of their own;
        the LIVE stage's verdict is carried in ``inputs['verdict']`` and recorded
        as-is (the orchestrator only ROUTES and RECORDS it).

    Returns a dict::

        {task_id, type, verdict: <engine output>, engine: <name>}

    ``verdict`` is the EXACT object the delegated engine returned (or the
    supplied live-stage verdict), proving no duplication. decide_task raises
    ValueError on an unknown type (fail-loud).
    """
    inputs = inputs or {}
    ttype = task.get("type")
    tid = task.get("id")

    if ttype in ("code", "test"):
        adapter = inputs.get("adapter")
        options = inputs.get("options") or {}
        state = inputs.get("state")
        verdict = evalloop.evaluate(repo_path, adapter, options=options,
                                    state=state)
        return {"task_id": tid, "type": ttype, "verdict": verdict,
                "engine": "evalloop.evaluate"}

    if ttype == "generation":
        if scaffold is None:  # pragma: no cover - scaffold ships alongside
            raise RuntimeError("scaffold module unavailable for a generation task")
        spec = inputs.get("spec") or {}
        verify_result = inputs.get("verify_result") or {}
        findings = inputs.get("findings") or []
        acceptance = inputs.get("acceptance")
        state = inputs.get("state")
        verdict = scaffold.decide_generation(
            spec, verify_result, findings, acceptance=acceptance,
            repo_path=repo_path, state=state)
        return {"task_id": tid, "type": ttype, "verdict": verdict,
                "engine": "scaffold.decide_generation"}

    if ttype in ("ingest", "review"):
        # No deterministic gate engine of their own: the LIVE stage already
        # produced the verdict; the orchestrator records it unchanged.
        verdict = inputs.get("verdict")
        return {"task_id": tid, "type": ttype, "verdict": verdict,
                "engine": "live_stage"}

    raise ValueError("cannot decide task {!r}: unknown type {!r}".format(
        tid, ttype))


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _load_json(path):
    """Load a JSON document from a local file. Pure local read."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_tasks(path):
    """Load the tasks list for --init (a JSON list or a {'tasks': [...]} doc)."""
    data = _load_json(path)
    if isinstance(data, dict) and "tasks" in data:
        return data["tasks"]
    if isinstance(data, list):
        return data
    raise SystemExit("tasks file must be a JSON list or a doc with a 'tasks' "
                     "key: {}".format(path))


def _print_status(run_state):
    """Print a human-readable status summary + the ready frontier + audit tail."""
    print("run {} => {}".format(run_state.get("run_id"),
                                 run_state.get("status")))
    if run_state.get("stop_reason"):
        print("  stop: {}".format(run_state["stop_reason"]))
    frontier = [n["id"] for n in ready_tasks(run_state["tasks"])]
    print("  ready: {}".format(frontier or "(none)"))
    by_status = {}
    for t in run_state["tasks"]:
        by_status.setdefault(t["status"], []).append(t["id"])
    for st in TASK_STATUSES:
        if by_status.get(st):
            print("  {}: {}".format(st, by_status[st]))
    tail = run_state.get("audit", [])[-5:]
    if tail:
        print("  recent audit:")
        for entry in tail:
            print("    {} {} [{}] {}".format(
                entry.get("ts"), entry.get("event"),
                entry.get("task_id") or "-", entry.get("detail") or ""))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA off-model orchestration engine (deterministic task-graph / "
            "run-state / resume / stop / audit; no LLM/network calls)."))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", dest="init_goal", metavar="GOAL",
                       help="Build + validate a task graph and write the "
                            "initial run-state (requires --tasks + --run-state).")
    group.add_argument("--step", action="store_true",
                       help="Apply one transition to a run-state (requires "
                            "--run-state --task-id --status).")
    group.add_argument("--status", dest="status_action", action="store_true",
                       help="Print the current run-state + ready frontier + "
                            "audit tail (requires --run-state).")
    group.add_argument("--audit", dest="audit_action", action="store_true",
                       help="Print the human-readable audit trail (requires "
                            "--run-state).")
    group.add_argument("--doctor", action="store_true",
                       help="Run the shared offline toolchain preflight + exit.")

    parser.add_argument("--tasks", default=None,
                        help="Tasks JSON for --init (a list or {'tasks': [...]}).")
    parser.add_argument("--run-state", dest="run_state_path", default=None,
                        help="Path to the run-state JSON document.")
    parser.add_argument("--task-id", dest="task_id", default=None,
                        help="Target task id for --step.")
    parser.add_argument("--status-value", "--to", dest="status_value",
                        default=None,
                        help="New status for --step (one of {}).".format(
                            ", ".join(TASK_STATUSES)))
    parser.add_argument("--result", default=None,
                        help="Optional result JSON file for --step.")
    parser.add_argument("--ask", default=None,
                        help="Optional ask text for --step (e.g. for a failed "
                             "task).")
    parser.add_argument("--run-id", dest="run_id", default=None,
                        help="Explicit run id for --init (default: derived).")
    parser.add_argument("--global-max-tasks", dest="global_max_tasks",
                        type=int, default=None,
                        help="Override the global stop bound (for a proof run).")
    parser.add_argument("--repo", default=None,
                        help="Repo root for REPO-FIRST config resolution.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    repo = args.repo if args.repo and os.path.isdir(args.repo) else None

    if args.doctor:
        if ingest is None:
            parser.error("doctor preflight unavailable: could not import ingest.py")
        report = ingest.doctor(repo_path=repo)
        print(ingest.format_doctor_report(report), end="")
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.init_goal is not None:
        if not args.tasks or not args.run_state_path:
            parser.error("--init requires --tasks TASKS.json and --run-state PATH")
        tasks = _load_tasks(args.tasks)
        graph = build_graph(tasks)
        run_id = args.run_id or "run-{}".format(
            _now().replace(":", "").replace("-", ""))
        run_state = new_run_state(run_id, args.init_goal, graph,
                                  repo_path=repo,
                                  global_max_tasks=args.global_max_tasks)
        save_run_state(args.run_state_path, run_state)
        if args.json:
            print(json.dumps(run_state, indent=2, sort_keys=True))
        else:
            _print_status(run_state)
        return 0

    if args.step:
        if not args.run_state_path or not args.task_id or not args.status_value:
            parser.error("--step requires --run-state PATH --task-id ID "
                         "--status-value STATUS")
        run_state = load_run_state(args.run_state_path)
        result = _load_json(args.result) if args.result else None
        outcome = step_run(run_state, args.task_id, args.status_value,
                           result=result, ask=args.ask)
        save_run_state(args.run_state_path, run_state)
        if args.json:
            print(json.dumps({"outcome": outcome, "run_state": run_state},
                             indent=2, sort_keys=True))
        else:
            _print_status(run_state)
        # Exit 0 on a clean/continuing run; non-zero when stopped/failed.
        return 0 if outcome["run_status"] in ("running", "done") else 1

    if args.status_action:
        if not args.run_state_path:
            parser.error("--status requires --run-state PATH")
        run_state = load_run_state(args.run_state_path)
        if args.json:
            print(json.dumps(run_state, indent=2, sort_keys=True))
        else:
            _print_status(run_state)
        return 0 if run_state.get("status") in ("running", "done") else 1

    if args.audit_action:
        if not args.run_state_path:
            parser.error("--audit requires --run-state PATH")
        run_state = load_run_state(args.run_state_path)
        print(render_audit(run_state), end="")
        return 0 if run_state.get("status") in ("running", "done") else 1

    parser.error("no action selected")


if __name__ == "__main__":
    sys.exit(main())
