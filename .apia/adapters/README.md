# APIA stack adapters

An **adapter** is a declarative, per-stack command map. It tells the APIA
`ingest -> clarify -> modify -> test -> apply` loop how to build, test, and
author tests for a given language/stack, without hard-coding any of that into
the skills or scripts. Adapters are referenced from `.apia/config.yml` under
`stacks[].adapter`.

**Adapters contain NO model inference and issue NO network/LLM calls.** They are
plain YAML read by the off-model scripts (`.apia/scripts/`) and by the Tier-2
skills/agents. Model inference always runs on the user's existing Copilot
session (see `docs/ARCHITECTURE.md` sections 1-2).

`python.yml` and `cpp.yml` are **fully implemented**; `csharp.yml` and `groovy.yml`
remain documented **stubs** with `TODO(phase-2)` markers that Phase 2 fills to parity.
The `cpp.yml` adapter is **adaptive** and carries extra sections beyond the base schema
(`build_systems`, `test_frameworks` with `result_format`, and an optional per-project
`override`); these are documented below.

## Schema

Every adapter is a YAML mapping with the following keys.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Stack id. Must match the `stacks[].name` in `.apia/config.yml`. |
| `primary` | bool | yes | Whether this is the Phase-1 primary stack. |
| `language` | string | yes | Human-readable language name. |
| `test_command` | string / null | yes | Shell command (run from repo root) that runs existing + new tests. `null` in a stub. |
| `test_discovery_glob` | list[string] | yes | Globs used to discover test files for this stack. |
| `no_tests_detection` | map | yes | How to decide the repo/area has no tests (triggers the characterization-test path). See below. |
| `characterization_test_template` | string / null | yes | Path (relative to package root) to the test template the `apia-test-authoring` skill fills in. `null` in a stub. |
| `build_command` | string / null | yes | Build/compile command, or `null` if the stack needs none. |
| `lint_command` | string / null | no | Advisory lint command; skipped if the tool is absent. |
| `manifests` | list[string] | yes | Manifest filenames (literal or glob) that identify this stack. Mirrors `ingest.py` `STACK_RULES`. |
| `extensions` | list[string] | yes | Source file extensions for this stack. |
| `frameworks` | list[string] | yes | Recognized test frameworks (first = default). |

### `no_tests_detection`

| Sub-key | Type | Meaning |
|---|---|---|
| `description` | string | Human-readable rule. |
| `collect_command` | string / null | Command that lists/collects tests without running them. |
| `zero_tests_signal` | string / null | Substring in `collect_command` output that means "zero tests". |

## Adaptive sections (used by `cpp.yml`)

A completed adapter may be **adaptive**: the top-level `test_command`/`build_command`
are the portable defaults, and the sections below let the ingester + harness select
per-build-system / per-framework commands from the detected project-facts. These keys
are **optional** - an adapter without them (e.g. `python.yml`) just uses the top-level
commands.

### `build_systems`

A map keyed by build-system id (e.g. `cmake`, `msbuild`). FEAT-002 detection picks one
by inspecting manifests; the harness assembles the configure/build commands from the
selected entry.

| Sub-key | Type | Meaning |
|---|---|---|
| `detect_manifests` | list[string] | Manifest filenames/globs that select this build system (e.g. `CMakeLists.txt` -> cmake; `*.sln`/`*.vcxproj` -> msbuild). |
| `build_dir` | string / null | Build output directory, or `null` for an in-tree build (MSBuild). |
| `config` | string | Default build configuration (`Debug` for iteration, `Release` for a final guard). |
| `configs` | list[string] | Allowed configurations. |
| `configure_command` | string / null | Configure step, or `null` if the build system has none. |
| `build_command` | string | Compile command (full string; absolute tool paths tolerated). |

### `test_frameworks`

A map keyed by framework id (e.g. `googletest`, `catch2`, `ctest`, `gtest_binary`,
`boost_test`, `vstest`). The harness selects an entry from the detected framework.

| Sub-key | Type | Meaning |
|---|---|---|
| `run_command` | string | Command that runs the tests (full string). |
| `collect_command` | string | Command that lists tests without running them. |
| `result_format` | string | Which output parser to apply: `pytest` \| `ctest` \| `gtest` \| `msbuild`. |
| `zero_tests_signal` | string | Substring meaning "no tests were run" for this runner. |

`result_format` is the key contract: the off-model harness normalizes every parser
(`pytest`/`ctest`/`gtest`/`msbuild`) to the same counts shape
`{passed, failed, error, skipped, no_tests}`. It may also be declared top-level on an
adapter; if absent, the harness falls back to the first framework's `result_format`,
else `pytest`.

### `override` (optional per-project fallback)

A **documented escape hatch**, consumed only when present and never required for the
default path. For C++ a project may instead pin values in a `cpp:` block in
`.apia/config.yml` (`build_dir`, `cmake_path`, `build_config`, `build_system`,
`test_command_override`). DETECT FIRST: the adapter only falls back here on genuine
doubt or an unusual layout the detector gets wrong.

## Contract notes

- The **base** keys are **always present**; stub values are `null` (or a
  `TODO(phase-2)` string) rather than omitted, so consumers can rely on the shape.
  `csharp.yml`/`groovy.yml` are stubs (`test_command: null`, refused by the harness);
  `python.yml`/`cpp.yml` are complete.
- The adaptive sections (`build_systems`, `test_frameworks`, `override`) are optional:
  present on `cpp.yml`, absent on `python.yml`.
- `manifests` / `extensions` must stay consistent with the detection rules in
  `.apia/scripts/ingest.py` (`STACK_RULES`) so ingestion and the adapter agree
  on what counts as the stack.
- Commands are run from the **repository root** being analyzed, not from the
  package directory. Full command strings are used as-is, so absolute tool paths
  (e.g. a full path to `msbuild.exe`) drop in unchanged.

## Adding or completing a stack (Phase 2)

1. Copy `python.yml` as a starting shape.
2. Fill every `null` / `TODO(phase-2)` with the real command for that stack.
3. Keep `manifests`/`extensions` aligned with `ingest.py` `STACK_RULES`.
4. Add a characterization-test template under
   `.github/skills/apia-test-authoring/templates/` and point
   `characterization_test_template` at it.
5. Ensure `name` matches the entry in `.apia/config.yml`.
