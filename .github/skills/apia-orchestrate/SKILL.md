---
name: apia-orchestrate
description: "APIA multi-task ORCHESTRATION stage. From a larger goal (a feature or a greenfield app spec), plan an ordered task graph, batch ALL knowable clarifying questions + approvals ONE round up front, then conduct each ready task through the EXISTING stages/gates (ingest -> clarify -> modify -> test, the eval/iterate loop, the scaffold/e2e-verify generation gate, the reviewer gate) autonomously between MEANINGFUL checkpoints. The off-model .apia/scripts/orchestrate.py (no LLM/network) owns graph/state/resume/stop/audit; the LIVE per-task reasoning/fix is this Copilot session - this stage CONDUCTS the existing engines, never reimplements them. Resumable run-state under .apia/runs/; bounded (global cap + per-task max_iterations) and fail-loud. Use to run a whole feature or app across many tasks with few checkpoints. Minimal interaction, not zero; strictly local. Runs in a forked context so heavy graph-planning never pollutes the main conversation."
context: fork
---

# apia-orchestrate

Conduct a larger goal through a **batched-checkpoint multi-task run**. This stage runs in
a **forked context** for token isolation (graph planning is heavy reasoning).

## Model vs off-model split (state it plainly)

- **Off-model (`orchestrate.py`, free, deterministic):** build + validate the task graph
  (unique ids, deps resolve, known types), reject cycles **fail-loud**, pick the ready
  frontier in a stable topological order, persist / reload a **resumable** run-state JSON,
  enforce the global stop bound + the per-task iteration cap, and keep a human-readable
  **audit trail**. It calls **no LLM and no network-to-model** and spawns no subprocess of
  its own.
- **On-model (this Copilot session):** planning what the tasks ARE from the goal, and for
  each task the actual reasoning / generation / fix work by invoking the EXISTING stages.
  This stage **conducts**; it never reimplements the graph mechanics or the stages.

## Steps

1. **Plan + validate the graph** from the goal (tasks with deps + a `type` each):

   ```sh
   python3 .apia/scripts/orchestrate.py --init "<goal>" --tasks tasks.json \
       --run-state .apia/runs/<run>.json --json
   ```

2. **ONE batched clarify + approval round up front.** Ask EVERYTHING knowable now - graph
   shape, scope, acceptance criteria, external contracts - as a single numbered round
   (below the `confidence_threshold` in `.apia/config.yml`, **never guess**). The user
   approves the plan + answers once; then the run proceeds to the next MEANINGFUL
   checkpoint without a gate per task.

3. **Drive each ready task through the EXISTING stage/gate and record the verdict.** Route
   by type and reuse the existing engines - never reinvent them:
   - **code/change** -> `apia-feature-loop` + `apia-test-authoring`; iterate via the
     `evalloop.py` loop under the hard `max_iterations` cap (`resolve_max_iterations`).
   - **generation** -> `apia-scaffold`/`apia-e2e-verify`; the gate reuses
     `scan.apply_thresholds` against `review.thresholds`.
   - **review** -> the reviewer GATE (same `review.thresholds`).

   Record each gate verdict with `--step` (the status value uses `--status-value`/`--to`;
   argparse reserves `--status` for the status action):

   ```sh
   python3 .apia/scripts/orchestrate.py --step --run-state .apia/runs/<run>.json \
       --task-id <id> --status-value done --result result.json --json
   ```

4. **Meaningful checkpoints + stop/resume.** Run autonomously between checkpoints; surface
   only at a MEANINGFUL checkpoint or GENUINE uncertainty. Inspect with `--status`/`--audit`;
   resume a stopped run across sessions/windows by reloading the same `--run-state` path
   (the run-state is a committable JSON document under `.apia/runs/`).

## Bounded + fail-loud

- Honor the **global stop bound** and the **per-task `max_iterations` cap**; never loop
  forever. A `failed` task leaves its dependents BLOCKED and HALTS the run.
- When a task cannot reach green / pass thresholds, **STOP and ask ONE specific question**
  (what was attempted, the exact failure, a best guess) - never silently apply something
  broken.

## Constraints

- **Minimal interaction, not zero:** few, well-placed human inputs instead of
  one-per-task - not a flawless zero-interaction auto-coder. The human approves at the
  batched checkpoints and the final apply.
- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Strictly **local**: never auto-push, open a PR/MR, or run CI/packaging/signing.
