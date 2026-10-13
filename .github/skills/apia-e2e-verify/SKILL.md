---
name: apia-e2e-verify
description: "APIA generation GATE. Build + run + smoke-test + unit-test a generated Python or C++ project and decide whether the increment is acceptable - full verification where the shape allows, honest-partial where it cannot. Run the off-model .apia/scripts/scaffold.py --verify (mechanical, no LLM/network) to drive build -> run/smoke -> unit tests and NORMALIZE the result, then run .apia/scripts/scan.py as the generation gate via apply_thresholds against review.thresholds. Failures feed back and ITERATE, bounded by the hard max_iterations cap (the same eval/test loop as verify.py --iterate). Use when the user asks to verify, build, run, or smoke-test a generated project. Runs in a forked context so heavy verification reasoning never pollutes the main conversation."
context: fork
---

# apia-e2e-verify

The generation GATE: prove a generated increment actually builds, runs, and passes its
tests - and report **honestly** when a shape cannot be fully verified. This stage runs in
a **forked context** for token isolation.

## Steps

1. **Build + run + smoke + unit test (off-model, normalized).** The host/user runs the
   off-model engine (mechanical, **no LLM and no network-to-model**):

   ```sh
   python3 .apia/scripts/scaffold.py --verify <project> --shape <shape> \
       --adapter .apia/adapters/<stack>.yml --json
   ```

   `verify_generated` drives build -> run/smoke -> unit tests and normalizes the result
   to one shape (`ok`, `partial`, phase results, failure detail), reusing the same build
   and test-result parsers as `verify.py`.

2. **Run the composable generation GATE.** The gate reuses the SAME policy as the
   Phase-2 reviewer - no new knobs:

   ```sh
   python3 .apia/scripts/scan.py <project> --adapter .apia/adapters/<stack>.yml \
       --category all --json
   ```

   `scaffold.generation_gate` calls `scan.apply_thresholds(findings, review.thresholds)`
   against the `review.thresholds` block in `.apia/config.yml` (`fail_on_severity`,
   per-category `max_findings`). A failing gate blocks acceptance.

3. **ITERATE, bounded by the hard cap.** On a failed build/test/gate, feed the exact
   structured failure back, fix the code live, and re-verify. This is the SAME eval loop
   as `/apia-iterate`:

   ```sh
   python3 .apia/scripts/verify.py <project> --adapter .apia/adapters/<stack>.yml \
       --iterate --max-iterations N --emit-failure-report --json
   ```

   The hard `max_iterations` cap in `.apia/config.yml` bounds it; on reaching the cap
   without green the loop FAILS LOUD and STOPS with a specific ask. Never loop forever.

## Full vs honest-partial (never fake green)

- **TIER-1 fully-verifiable** (`python_cli`, `python_library`, `python_web_api`,
  `cpp_console`, `cpp_library`): the increment must go **all-green** - built, run,
  smoke-tested, unit-tested, and past the gate.
- **TIER-2 honest-partial** (`gui`, `embedded`, `driver`): emitted and compile/unit
  checked, but NOT auto-runnable headless (no display / no target hardware / no
  privileged kernel). Report **PARTIAL** - exactly what was verified plus what a human
  must still check (as named in the generated `PARTIAL.md`). **Never fake green.**

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- The verifier and gate are **deterministic tooling, not a second model**; the fix step
  is this live session only.
- Strictly **local**: never auto-push, open a PR/MR, or run CI/packaging/signing.
