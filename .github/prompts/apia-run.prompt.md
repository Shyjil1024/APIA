---
description: "APIA ORCHESTRATE: drive a larger goal (a feature on an existing project or a greenfield app spec) through a batched-checkpoint multi-task run. Plan an ordered task graph with the off-model orchestrate.py, batch ALL knowable clarifying questions + approvals ONE round up front, then run each ready task through the EXISTING stages/gates (ingest -> clarify -> modify -> test, the eval/iterate loop, the scaffold/e2e-verify generation gate, the reviewer gate) autonomously between MEANINGFUL checkpoints - stop/resume from the persistent run-state, stay bounded (global cap + per-task max_iterations) and fail loud with a specific ask. Minimal interaction, not zero; strictly local; human-gated apply. Works on Visual Studio 2022 as a single Tier-0/Tier-1 agent."
agent: apia-orchestrator
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **ORCHESTRATE** stage: a larger goal becomes a batched-checkpoint multi-task
run. This prompt drives the **read-only** `apia-orchestrator` agent, so its tools match
the agent's declaration: no `edit/files`, no `run/terminal`. It carries the WHOLE
orchestration flow as a **single Tier-0/Tier-1 agent**, so it works on **Visual Studio
2022** and **JetBrains** too (those hosts honor only Tier-0 instructions + Tier-1 prompts,
not Tier-2 skills/agents - this one prompt carries the whole flow).

The off-model `orchestrate.py` is **deterministic tooling**, never a second model; it
makes **no LLM and no network-to-model calls** and spawns no subprocess of its own. It
owns the graph/state/resume/stop/audit mechanics. The LIVE per-task reasoning,
generation, and fixes are this Copilot session only. We **cannot set model temperature.**

1. **Plan + validate the task graph.** Turn the goal into tasks with dependencies and a
   `type` each (code/change, generation, review), then let the off-model engine validate
   and order it (it rejects cycles and unresolved deps **fail-loud**):

   ```sh
   python3 .apia/scripts/orchestrate.py --init "<goal>" --tasks tasks.json \
       --run-state .apia/runs/<run>.json --json
   ```

2. **ONE batched clarify + approval round up front.** Ask EVERYTHING knowable now - graph
   shape, scope, acceptance criteria, external contracts - as a single numbered round. If
   confidence is below the `confidence_threshold` in `.apia/config.yml`, keep asking;
   **never guess, never drip-feed**. The user approves the plan + answers once.
   **GATE: STOP for plan + batched-clarify approval.**

3. **Drive each ready task through the right EXISTING stage** (reuse, do not reinvent),
   then record the gate verdict back into the run-state. Route by type:
   - **code/change** -> ingest -> clarify -> modify -> test via `apia-feature-loop` +
     `apia-test-authoring`; iterate via `/apia-iterate` (the `evalloop.py` self-correction
     loop, bounded by the hard `max_iterations` cap).
   - **generation** -> `/apia-new` + `apia-scaffold`/`apia-e2e-verify`; the generation
     gate reuses `scan.apply_thresholds` against `review.thresholds`.
   - **review** -> `/apia-review` (the reviewer GATE, same `review.thresholds`).

   Record each verdict with `--step`. Note the step CLI uses `--status-value` (alias
   `--to`) for the new status value, because argparse reserves `--status` for the status
   action:

   ```sh
   python3 .apia/scripts/orchestrate.py --step --run-state .apia/runs/<run>.json \
       --task-id <id> --status-value done --result result.json --json
   ```

4. **Run between MEANINGFUL checkpoints, then stop/resume.** Run autonomously between
   checkpoints; surface only at a MEANINGFUL checkpoint or GENUINE uncertainty - not one
   gate per task. Inspect any time, and resume a stopped run across sessions/windows by
   reloading the same `--run-state` path:

   ```sh
   python3 .apia/scripts/orchestrate.py --status --run-state .apia/runs/<run>.json
   python3 .apia/scripts/orchestrate.py --audit  --run-state .apia/runs/<run>.json
   ```

5. **Stay bounded + fail loud.** Honor the global stop bound (`--global-max-tasks`) and
   the per-task `max_iterations` cap. When a task cannot reach green / pass thresholds,
   **STOP and ask ONE specific question** (what was attempted, the exact failure, a best
   guess) - never silently apply something broken, never loop forever. A `failed` task
   leaves its dependents BLOCKED and HALTS the run with its ask.

**Honest framing:** this is **minimal interaction, not zero** - few, well-placed human
inputs instead of one-per-task, not a flawless zero-interaction auto-coder. APIA stays
**strictly local**: never auto-push, open a PR/MR, or run CI/packaging/signing. The human
still approves at the batched checkpoints and the final apply.

Do not dump the whole repo; retrieve only the relevant slice. Do not call any external
LLM. Fetch external **data** only via MCP/tool-use.
