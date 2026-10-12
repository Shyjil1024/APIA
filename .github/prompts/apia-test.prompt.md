---
description: "APIA stage 4 (TEST): run existing tests as a regression guard, generate characterization tests first when the touched code is untested, add new tests, and report compactly."
agent: apia-tester
tools: ["run/terminal", "run/tests", "search/codebase"]
---

Run the APIA **TEST** stage.

1. Load the stack adapter `.apia/adapters/<stack>.yml` for the detected stack and read
   its `test_command`, `test_discovery_glob`, and `no_tests_detection`. For **C++** the
   `cpp.yml` adapter is complete: it selects the build system (CMake or MSBuild) and the
   test framework (GoogleTest/Catch2/CTest, with its `result_format`) from the detected
   project-facts; the portable default is CMake + CTest (`ctest --test-dir build`).
2. **Build-only compile check (C++):** before running tests, run the adapter's
   `build_command` to confirm the project still compiles - `verify.py <repo> --build-only`.
   This compile-check is run first for C++ and is the only regression signal available
   when the project has no tests.
3. **Regression guard:** run the project's existing tests to capture the current green
   baseline. If they are already red, surface that before proceeding.
4. **Legacy / no-tests case:** if the touched code has no (or inadequate) tests,
   generate **characterization tests first** (using the adapter's
   `characterization_test_template`) to pin current behavior **before** any change. For
   C++ this uses the `cpp_characterization_test.cpp.tmpl` GoogleTest template.
5. **New tests:** generate tests for the new behavior per the change spec.
6. Run existing + new tests and report pass/fail **compactly**. The off-model harness
   `verify.py` parses the stack's `result_format` (pytest/ctest/gtest) into uniform
   pass/fail counts.
7. **STOP at the gate** for the user to review results.

Do not call any external LLM. Use the adapter commands for the stack.
