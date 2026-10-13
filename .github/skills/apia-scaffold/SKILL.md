---
name: apia-scaffold
description: "APIA greenfield GENERATION stage. From an APPROVED spec, lay down a working Python or C++ project and generate its core features, incrementally, with human checkpoints. Run the off-model .apia/scripts/scaffold.py --emit (mechanical, no LLM/network) to emit the deterministic skeleton/build files for the detected shape, then GENERATE the feature code LIVE on this Copilot session - that live code generation is the model's job, explicitly NOT scaffold.py's. Every generated increment goes through the generation gate (apia-e2e-verify). Use when the user asks to generate, scaffold, create a new project, or build a greenfield app. Runs in a forked context so heavy generation reasoning never pollutes the main conversation."
context: fork
---

# apia-scaffold

Generate a working project from the **APPROVED spec** produced by `apia-architect`. This
stage runs in a **forked context** for token isolation.

## Model vs off-model split (state it plainly)

- **Off-model (`scaffold.py`, free, deterministic):** shape detection, emitting the
  idiomatic skeleton + build files, build/run/smoke/test normalization, the gate
  decision. It calls **no LLM and no network-to-model**.
- **On-model (this Copilot session):** writing the actual feature CODE for the detected
  shape, live, incrementally. This is the model's job - `scaffold.py` never writes
  feature logic.

## Steps

1. **Emit the deterministic skeleton** from the approved spec:

   ```sh
   python3 .apia/scripts/scaffold.py --emit spec.json --dest <project> --json
   ```

   It lays down the idiomatic base layout and build files for the detected shape only
   (`python_cli`/`python_library`/`python_web_api`/`cpp_console`/`cpp_library`, or an
   honest-partial `gui`/`embedded`/`driver`), reporting `full` vs `partial`. It writes
   **no feature logic**.

2. **Generate the feature code LIVE**, one increment at a time, smallest feature first,
   grounded in the approved spec and the emitted skeleton. Keep each increment within the
   `token_budget.generate` ceiling in `.apia/config.yml`.

3. **Human checkpoint per increment.** Present the increment and STOP for review before
   moving on - minimal, well-placed human input, not zero interaction.

4. **Route every increment through the generation gate** (`apia-e2e-verify`): build +
   run + smoke + unit tests, then the composable severity gate. Failures feed back and
   iterate under the hard `max_iterations` cap; honest-partial shapes report partial,
   never faked green.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Strictly **local**: never auto-push, open a PR/MR, or run CI/packaging/signing.
- Do not generate past an approved increment without the human checkpoint; do not expand
  scope beyond the approved spec.
