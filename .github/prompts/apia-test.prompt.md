---
description: "APIA stage 4 (TEST): run existing tests as a regression guard, generate characterization tests first when the touched code is untested, add new tests, and report compactly."
agent: apia-tester
tools: ["run/terminal", "run/tests", "search/codebase"]
---

Run the APIA **TEST** stage.

1. Load the stack adapter `.apia/adapters/<stack>.yml` for the detected stack and read
   its `test_command`, `test_discovery_glob`, and `no_tests_detection`.
2. **Regression guard:** run the project's existing tests to capture the current green
   baseline. If they are already red, surface that before proceeding.
3. **Legacy / no-tests case:** if the touched code has no (or inadequate) tests,
   generate **characterization tests first** (using the adapter's
   `characterization_test_template`) to pin current behavior **before** any change.
4. **New tests:** generate tests for the new behavior per the change spec.
5. Run existing + new tests and report pass/fail **compactly**.
6. **STOP at the gate** for the user to review results.

Do not call any external LLM. Use the adapter commands for the stack.
