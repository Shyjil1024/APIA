---
description: "APIA greenfield GENERATION: turn an app prompt into a working, tested, runnable Python or C++ project. Architect a spec with grounded shape detection, ask ONE batched clarify + the acceptance-criteria confirmation gate up front, scaffold the deterministic skeleton and generate the feature code live, then build + run + smoke + unit test it through the composable generation gate and iterate to green under the hard max_iterations cap - honest-partial where a shape cannot be fully verified, never faked green. Works on Visual Studio 2022 as a single Tier-0/Tier-1 agent."
agent: apia-architect
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **GENERATE** stage: an app prompt becomes a working, tested, runnable
greenfield project. This prompt drives the **read-only** `apia-architect` agent, so its
tools match the agent's declaration: no `edit/files`, no `run/terminal`. It carries the
WHOLE generation flow as a **single Tier-0/Tier-1 agent**, so it works on **Visual Studio
2022** too (VS 2022 honors only Tier-0 instructions + Tier-1 prompts, not Tier-2
skills/agents - this one prompt carries the whole flow).

The off-model `scaffold.py` and `scan.py` are **deterministic tooling**, never a second
model; they make **no LLM and no network-to-model calls**. The live CODE GENERATION is
this Copilot session only. We **cannot set model temperature.**

1. **Architect the spec + batched clarify + acceptance-criteria gate.** Ground the shape
   with the off-model detector, then present a structured spec and resolve uncertainty in
   ONE batched round:

   ```sh
   python3 .apia/scripts/scaffold.py --detect-shape spec.json --json
   ```

   It prints the `shape` (one of `generate.supported_shapes` in `.apia/config.yml`),
   `stack`, `verifiable`, `confidence`, and `reasons`. The spec is features + project
   structure + dependencies + acceptance criteria (within `token_budget.generate`). If
   confidence is below the `confidence_threshold` in `.apia/config.yml`, ask ONE batched,
   numbered set of clarifying questions - including a **shape-confirmation** question when
   ambiguous. The **acceptance-criteria confirmation** is the anti-circularity gate: the
   user defines "done" once (`eval_data.acceptance_criteria_gate`); the model must not
   grade its own homework. **GATE: STOP for spec approval.**

2. **Scaffold the skeleton + generate feature code live.** Lay down the deterministic
   skeleton/build files (off-model), then generate the feature code LIVE, incrementally,
   smallest feature first:

   ```sh
   python3 .apia/scripts/scaffold.py --emit spec.json --dest <project> --json
   ```

   `--emit` writes the skeleton/build files only; the feature logic is the model's job,
   not `scaffold.py`'s. **GATE: a human checkpoint per increment.**

3. **E2E-verify + generation gate + bounded iterate.** Build + run + smoke + unit test
   each increment and run the composable gate:

   ```sh
   python3 .apia/scripts/scaffold.py --verify <project> --shape <shape> \
       --adapter .apia/adapters/<stack>.yml --json
   python3 .apia/scripts/scan.py <project> --adapter .apia/adapters/<stack>.yml \
       --category all --json
   ```

   The gate reuses `scan.apply_thresholds` against `review.thresholds` (no new knobs). On
   failure, feed the exact structured failure back and iterate via the SAME eval loop,
   bounded by the hard `max_iterations` cap:

   ```sh
   python3 .apia/scripts/verify.py <project> --adapter .apia/adapters/<stack>.yml \
       --iterate --max-iterations N --emit-failure-report --json
   ```

   TIER-1 shapes (`python_cli`, `python_library`, `python_web_api`, `cpp_console`,
   `cpp_library`) must go all-green; TIER-2 shapes (`gui`, `embedded`, `driver`) report
   **PARTIAL** honestly (what was verified + what a human must check), **never faked
   green**. On reaching the cap without green, FAIL LOUD and STOP with a specific ask.

4. **STOP at the apply gate.** Present the generated, verified project and wait. APIA
   stays **strictly local**: never auto-push, open a PR/MR, or run CI/packaging/signing.
   The user applies/pushes.

Do not dump the whole repo; retrieve only the relevant slice. Do not call any external
LLM. Fetch external **data** only via MCP/tool-use.
