# APIA orchestrator run-state / audit log

This directory holds the **resumable run-state** and **human-readable audit log**
the Phase-4 orchestrator writes while it drives a larger goal through APIA's
existing loops as an ordered task graph. It is **committed on purpose** -- the same
discipline as `.apia/knowledge/` -- so a long autonomous run survives sessions and
windows, can be inspected after the fact, and is shared with the team.

The off-model engine (`.apia/scripts/orchestrate.py`) is the only thing that reads
and writes these files; it makes **zero network and zero LLM calls** and spawns no
subprocess of its own (see `docs/ARCHITECTURE.md` "Orchestrator (Phase 4)" and
`docs/PROOF.md`).

## Files

| File | What it is |
|---|---|
| `example-tasks.json` | The small 3-task proof graph (`ingest-pyapp -> code-on-pyapp -> review-pyapp`) used to build the example run. The input to `orchestrate.py --init`. |
| `example-run.json` | The committed example **run-state** after the 3-task run completed, written by `orchestrate.py`. Shows the run-state schema (`run_id`, `goal`, bounds, per-task `status`/`result`/`deps`/`approvals`/`ask`, and the full `audit` trail). |
| `example-run.audit.log` | The same run rendered as a plain-text, chronological **audit trail** by `orchestrate.py --audit`. Human-readable without parsing JSON. |

The `example-run.*` files are an **example**: the per-task `result` verdicts are
worked-example records of the live Copilot stages (ingest / code / review). The
graph build, validation, topological frontier, dependency unblocking, persistence,
resume, stop condition, and audit trail were all **executed for real** across
separate `orchestrate.py` invocations (see the Orchestrator proof in
`docs/PROOF.md`). Delete these example files once you have your own runs; they do
not affect any test or any other APIA capability.

## Reproduce the example

```sh
# 1. Build + validate the graph and write the initial run-state (one process).
.venv/bin/python .apia/scripts/orchestrate.py --init "add a rounding option to the pyapp calculator report" \
    --tasks .apia/runs/example-tasks.json --run-state .apia/runs/example-run.json --run-id run-example

# 2. Drive each task running -> done across SEPARATE invocations (persistence is
#    real: each process loads, mutates, and saves the run-state file).
.venv/bin/python .apia/scripts/orchestrate.py --step --run-state .apia/runs/example-run.json --task-id ingest-pyapp --to running
.venv/bin/python .apia/scripts/orchestrate.py --step --run-state .apia/runs/example-run.json --task-id ingest-pyapp --to done

# 3. A FRESH process resumes from the saved frontier (nothing in memory carried over).
.venv/bin/python .apia/scripts/orchestrate.py --status --run-state .apia/runs/example-run.json

# ... continue code-on-pyapp then review-pyapp the same way until the run is done.
.venv/bin/python .apia/scripts/orchestrate.py --audit --run-state .apia/runs/example-run.json
```
