---
description: "APIA stages 2-3 (CLARIFY -> MODIFY): turn a feature/change request into an agreed change spec and a proposed diff grounded in the knowledge base, stopping at each gate."
agent: apia-planner
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **feature loop** (CLARIFY -> MODIFY).

1. Restate the request in your own words and list your **assumptions**.
2. **Detect first, then ask only on genuine doubt, and ask specifically.** Infer the
   build system and test framework from real evidence in `.apia/knowledge/`
   (project-facts) and the files. If confidence is at or above the
   `confidence_threshold` in `.apia/config.yml`, **proceed and STATE the reasoning**.
   Only below it ask **ONE specific, informed** question - never guess, and never a
   generic one. Worked C++ example: "I detected a `CMakeLists.txt` with
   `FetchContent(googletest)` and `*_test.cpp` files, so I inferred build system =
   CMake, framework = GoogleTest via CTest (confidence 0.9). Proceeding." Below
   threshold instead: "I found both a `CMakeLists.txt` and a `*.sln`; I lean CMake
   (confidence 0.6). Which build system should I target - CMake or MSBuild?" Fetch any
   external data via MCP/tool-use, never invent it.
3. **STOP at the CLARIFY gate** until the user answers and approves a **change spec**
   (what, where, acceptance criteria, out-of-scope). Confirmed facts persist to
   `.apia/knowledge/` (project-facts) so they are **not re-asked** next time.
4. Produce a plan + **proposed diff** grounded in `.apia/knowledge/` and the specific
   files involved. Write nothing to disk yet. To preview what the verification would
   run without changing anything, note the **dry-run / explain-the-plan** option:
   `verify.py <repo> --dry-run` prints the planned build/test commands and reasoning.
5. **STOP at the MODIFY gate** for the user to approve the diff (or request changes,
   which loops back to CLARIFY).

Hand off to `apia-implementer` only after the diff is approved.
