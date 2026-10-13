---
name: apia-test-authoring
description: "APIA stage 4 (TEST). Authors regression + new tests and runs them as the verification gate. First runs the project's existing tests as a regression guard; when the touched code has no (or inadequate) tests, generates CHARACTERIZATION tests first to pin current behavior BEFORE any modification (the legacy no-tests path), then generates new tests for the change per the change spec. Stack-aware via the per-stack adapter .apia/adapters/<stack>.yml (test_command, test_discovery_glob, no_tests_detection, characterization_test_template). Reports pass/fail compactly and STOPS at the human-approval gate. Use when a change needs tests, when verifying a proposed diff, or when onboarding tests to untested/legacy code. Runs in a forked context for token isolation."
context: fork
---

# apia-test-authoring

Author and run tests so a change is validated by **execution**, not assertion. This
stage runs in a **forked context** for token isolation.

## Steps

1. **Load the stack adapter** `.apia/adapters/<stack>.yml` for the detected stack and
   read `test_command`, `test_discovery_glob`, `no_tests_detection`, and
   `characterization_test_template`. The **C++** adapter (`cpp.yml`) is complete: it
   selects a build system (CMake or MSBuild) and a test framework and carries a
   `result_format` per framework - `ctest` (GoogleTest/Catch2/CTest via CTest) or
   `gtest` (a GoogleTest binary run directly) - so the harness parses the right output.

2. **Build-only compile check (C++).** For C++, run the adapter's `build_command`
   first (`verify.py <repo> --build-only`) to confirm the project still compiles. This
   is the only regression signal when a C++ project has no tests.

3. **Regression guard.** Run the existing tests via the adapter's `test_command` to
   capture the current green baseline. If they are already red, surface that before
   proceeding.

4. **Characterization-first for untested code.** Decide whether the touched code has
   tests using the adapter's `no_tests_detection` (e.g. zero collected items, or CTest
   reporting `Total Tests: 0`). If it does not, generate **characterization tests
   first** - using the adapter's `characterization_test_template` (the
   `cpp_characterization_test.cpp.tmpl` GoogleTest template for C++) - that pin the
   current behavior **before** the change is applied, so any regression becomes
   detectable.

5. **New tests.** Generate tests/verifications for the new behavior per the agreed
   change spec, following project conventions and the adapter's framework.

6. **Run existing + new tests** and confirm old behavior still passes and new behavior
   is verified. **Report pass/fail compactly** (stay within the `test` token budget in
   `.apia/config.yml`).

7. **STOP at the gate** for the user to review results before APPLY.

## Layer-1 eval data (repo-derived ground truth)

Verification is grounded in observable project behavior, never in the model's own
restatement of the request. The adapter may declare three optional Layer-1 sources,
consumed in trust order **characterization > golden > invariant > differential** (prefer
the highest-trust signal available):

- **characterization** (`characterization.capture_command`) - runs the existing code and
  freezes its deterministic stdout as the baseline; the most trustworthy signal because
  it is the real output of the real code.
- **golden** (`golden.artifact_glob` / `golden_dir` / `compare`) - the project treats the
  artifacts it produces as the correctness truth; the loop diffs freshly produced
  artifacts against a committed golden tree. `compare` is `bytes` (default, exact),
  `text` (newline-normalized), or `json` (parsed structure). Scope `artifact_glob` to the
  produced-output directory (e.g. `out/**/*.json`) so mining never sweeps generated build
  output.
- **invariant** (`invariants.checks`) - built-in structural properties that must always
  hold: `row_count_matches_input`, `no_nans`, `roundtrip_identity`.
- **differential** - the delta against the saved baseline (regressions vs. new),
  persisted via `--baseline-only` / `.apia-baseline.json`.

## Acceptance-criteria confirmation gate (anti-circularity)

For acceptance criteria that describe **NEW** behavior the code does not yet exhibit, the
loop requires **ONE human confirmation** before treating them as truth
(`eval_data.acceptance_criteria_gate` in `.apia/config.yml`). The same model must not both
write the code and invent the test it grades itself against.

## Iterate to green (Layer-2), deterministic verifier vs. live model fix

A verified change may **iterate to green** under the hard `max_iterations` cap (see
`/apia-iterate`): assemble Layer-1 eval data -> run the deterministic verifier -> on
failure feed the structured failure back and fix -> re-run -> repeat until green OR the
cap, then **STOP and ask**. Keep this separation strict: the **verifier is deterministic
tooling** (tests/build/diff/golden) and the off-model scripts call **no model**; the
**fix step is this live Copilot session only**. Never silently apply a broken change and
never loop forever - the cap is a hard stop and APPLY still gates on the human.

## Scriptable harness (same commands, no model needed)

The off-model harness `.apia/scripts/verify.py` runs exactly these phases through
the SAME adapter (`.apia/adapters/<stack>.yml`) so the human-facing loop and
automation stay in lockstep. It issues only the adapter's local `test_command`
(no LLM/network calls):

- Baseline + regression (existing tests): `python .apia/scripts/verify.py <repo>`
- New-behavior phase too: `python .apia/scripts/verify.py <repo> --new-tests tests/<new_test>.py`
- Build-only compile check (C++, or any project with no tests): `--build-only`.
- Preview the plan without changing anything: `--dry-run`.
- Machine-readable summary for tooling: add `--json`.

For C++ the harness runs a BUILD phase first, then CTest-driven baseline/regression/new,
parsing the adapter's `result_format` (`ctest`/`gtest`) into the same pass/fail counts as
the pytest path.

Use it to capture the green baseline before a change and to prove no regression
plus the new test afterward. See `docs/PROOF.md` for the end-to-end walkthrough
mapped to the five-stage loop.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Use the adapter's commands; do not hard-code stack-specific commands here.
