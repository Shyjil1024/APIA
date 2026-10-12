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
   `characterization_test_template`.

2. **Regression guard.** Run the existing tests via the adapter's `test_command` to
   capture the current green baseline. If they are already red, surface that before
   proceeding.

3. **Characterization-first for untested code.** Decide whether the touched code has
   tests using the adapter's `no_tests_detection` (e.g. zero collected items). If it
   does not, generate **characterization tests first** - using the adapter's
   `characterization_test_template` - that pin the current behavior **before** the
   change is applied, so any regression becomes detectable.

4. **New tests.** Generate tests/verifications for the new behavior per the agreed
   change spec, following project conventions and the adapter's framework.

5. **Run existing + new tests** and confirm old behavior still passes and new behavior
   is verified. **Report pass/fail compactly** (stay within the `test` token budget in
   `.apia/config.yml`).

6. **STOP at the gate** for the user to review results before APPLY.

## Scriptable harness (same commands, no model needed)

The off-model harness `.apia/scripts/verify.py` runs exactly these phases through
the SAME adapter (`.apia/adapters/<stack>.yml`) so the human-facing loop and
automation stay in lockstep. It issues only the adapter's local `test_command`
(no LLM/network calls):

- Baseline + regression (existing tests): `python .apia/scripts/verify.py <repo>`
- New-behavior phase too: `python .apia/scripts/verify.py <repo> --new-tests tests/<new_test>.py`
- Machine-readable summary for tooling: add `--json`.

Use it to capture the green baseline before a change and to prove no regression
plus the new test afterward. See `docs/PROOF.md` for the end-to-end walkthrough
mapped to the five-stage loop.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Use the adapter's commands; do not hard-code stack-specific commands here.
