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

### `scanners` (Phase-2 Reviewer, used by `python.yml` and `cpp.yml`)

An **optional** top-level section that wires the per-stack LOCAL scanner tools
the off-model scanner engine (`.apia/scripts/scan.py`) runs for the Reviewer. It
maps a **finding category** to one or more declarative scanner definitions. An
adapter without it (e.g. a stub) is **refused** by `scan.py`
(`StubAdapterError`) rather than reporting a false clean bill of health.
`python.yml` and `cpp.yml` declare `scanners:`; `csharp.yml` and `groovy.yml` remain
**stubs with no `scanners:` section** until Phase 2 fills them in.

```yaml
scanners:
  <category>:            # security | quality | perf
    - tool: <id>         # human-readable scanner id (also used by doctor)
      command: <string>  # FULL command string (see below)
      format: <parser>   # selects the normalizer
```

| Sub-key | Type | Meaning |
|---|---|---|
| `tool` | string | Scanner id (e.g. `bandit`, `cppcheck`). Reported by `scan.py --doctor` for presence. |
| `command` | string | **Full** command string run from the repo root. A `python -m <mod> ...` command is rewritten to run under the SAME interpreter that launched `scan.py` (the project's `.venv`), exactly like `verify.py`'s test commands; a native binary or an absolute path is run as-is. |
| `format` | string | Selects the normalizer via the `source_tool -> parser` dispatch: `bandit` \| `pip_audit` \| `ruff` \| `flake8` \| `radon` \| `cppcheck` \| `clang_tidy`. |

`format` is the key contract (the scanning counterpart to `result_format`): every
normalizer maps its tool's native output into ONE canonical **finding** shape
`{severity, category, file, line, rule, message, source_tool}`, where `severity`
is the ordered vocabulary `critical > high > medium > low > info` and `category`
is `security | quality | perf` (`limitations` is reserved for the model-facing
synthesis stage). The engine aggregates all findings, applies the composable
gate `apply_thresholds(findings, review.thresholds)` from `.apia/config.yml`, and
emits a JSON-serializable report whose `gate` block both the standalone reviewer
and a future greenfield-generation gate read.

Scanners are **advisory**: a MISSING tool degrades to a structured
`tool_unavailable` marker (never a crash, never a fabricated finding), and their
presence is reported under the non-gating `scanner` / `scanner_cpp` doctor
categories. `cppcheck` is declared **once** and **self-categorizes** (its own
severity kind routes each finding to security/quality/perf), so a single
`--enable=all` run feeds all three categories without duplication. Scope scanner
commands to the project's own source tree (e.g. the C++ adapter scans
`src include`, not `.`) so mining never sweeps a generated `build/` tree, mirroring
the `golden.artifact_glob` scoping discipline.

### The findings-report INTERFACE (the stable contract other components consume)

`scan.py`'s `scan(repo, adapter, options)` returns a JSON-serializable report that is
the **stable contract** a standalone reviewer, a future greenfield-generation gate, and
the future iterate-to-best eval-loop fusion all read:

```
{
  "repo", "repo_path", "stack",       # scanned repo + resolved adapter name
  "adapter_source",                   # how the adapter was chosen (explicit/auto-detected/default)
  "category",                         # the scanned category (security|quality|perf|all)
  "findings": [ {severity, category, file, line, rule, message, source_tool}, ... ],
  "counts_by_severity": {critical, high, medium, low, info},
  "counts_by_category": {security, quality, perf, ...},
  "threshold": { ...effective thresholds (review.thresholds, CLI-overridable)... },
  "gate": {                           # the composable decision
    "passed": bool,
    "fail_on_severity": <level or null>,
    "failing_severities": [...],
    "failing_counts": {category: {limit, actual}},
    "reasons": [human-readable strings]
  },
  "tools": { ...scanner presence (doctor-style)... },
  "scanners": [ ...per-scanner meta... ],
  "unavailable_tools": [ ...missing tool names... ]
}
```

The `gate` block is produced by the SINGLE shared function
`apply_thresholds(findings, thresholds)`, so the decision is **composable without
rework**: `thresholds` comes from the `review` block in `.apia/config.yml`
(`fail_on_severity` plus optional per-category / `total` `max_findings`), and both a
standalone review and a future generation gate call `apply_thresholds()` with the SAME
signature.

## Layer-1 eval-data sections (used by `python.yml` and `cpp.yml`)

Three **optional** top-level sections ground the self-correction loop in
observable project behavior rather than the agent's restatement of the request.
Like the adaptive sections they are optional: an adapter without them (e.g. a
stub) simply offers no golden/characterization/invariant signal and the loop
falls back to the differential delta. When present they preserve the base shape.
The off-model runners that consume them ship in `.apia/scripts/evaldata.py`
(Layer 1: `harvest_characterization`, `mine_golden`/`compare_artifact`,
`run_invariants`, `differential_artifacts`, `assemble_eval_data`) and are driven
by the Layer-2 loop in `.apia/scripts/evalloop.py`; both are off-model and make no
network/LLM calls. The loop prefers the highest-trust signal in the order
`characterization > golden > invariant > differential` (see
`.apia/config.yml` `eval_data.trust_order`).

### `golden`

A committed golden-file comparison: the project treats the artifacts it produces
as the correctness truth, and the loop diffs freshly produced artifacts against a
committed golden tree.

| Sub-key | Type | Meaning |
|---|---|---|
| `artifact_glob` | list[string] | Globs (relative to repo root) for produced artifacts the project treats as correct output. Scope these to the project's **produced-output directory** (e.g. `out/**/*.json`) rather than the whole tree, so mining never sweeps generated build output (a CMake `build/` tree emits tool `*.json` such as `compile_commands.json`/`Labels.json` that have no golden counterpart). Empty when the stack mines no golden artifacts. |
| `golden_dir` | string / null | Committed golden tree holding the known-good copies of those artifacts. The produced (non-golden) artifacts live in a gitignored path; only the sources and this committed golden tree are tracked. `mine_golden` compares each produced artifact against the same-named file under `golden_dir`, skipping the golden tree itself. |
| `compare` | string \| map | How to compare produced vs. golden: `bytes` \| `text` \| `json`. Default is the language-neutral `bytes` (exact match); `text` normalizes line endings; `json` compares parsed structure. May be a single global string applied to every glob, OR a **per-glob mapping** `{glob: mode, ..., default: mode}` so a block whose `artifact_glob` mixes artifact kinds (e.g. `*.json` + `*.csv`) grades each kind under a mode valid for it; a glob with no entry falls back to the mapping's `default` (else `bytes`). `mine_golden` resolves the mode per matched glob. |

### `characterization`

Pins the **current observed behavior** before modifying legacy code.

| Sub-key | Type | Meaning |
|---|---|---|
| `capture_command` | string / null | Command that runs the existing code and prints deterministic output to stdout; the captured output becomes the characterization baseline. `null` when the adapter has no generic capture command. |

### `invariants`

Built-in structural properties that must always hold regardless of the specific
request.

| Sub-key | Type | Meaning |
|---|---|---|
| `description` | string | Human-readable rule. |
| `checks` | list[string] | Built-in invariant ids the Layer-1 invariant runner recognizes (e.g. `row_count_matches_input`, `no_nans`, `roundtrip_identity`); unknown ids are ignored. |

## Phase-3 `generate` section (used by `python.yml` and `cpp.yml`)

An **optional** top-level section carrying the minimal **deterministic emission
facts** the off-model generation engine (`.apia/scripts/scaffold.py`) consults
when it emits a greenfield project of a given **shape**. Like the eval-data
sections it is optional: `scaffold.py`'s `_adapter_fragment()` reads
`generate.templates.<shape>` (a dict per shape) **when present** and falls back
to its built-in templates otherwise, so an adapter without a `generate` section
(e.g. a stub) still generates correctly. The engine only reads the mechanical
facts; Copilot writes the feature logic live. The supported shape ids live in
`.apia/config.yml` under `generate.supported_shapes` (the authoritative list
`scaffold.py` resolves REPO-FIRST), and the generation gate reuses the SAME
`review.thresholds` + `max_iterations` -- no new thresholds are declared here.

```yaml
generate:
  templates:
    <shape>:               # a supported shape id (e.g. python_cli, cpp_console)
      build_file: <string>       # build-file emitted (pyproject.toml / CMakeLists.txt)
      skeleton: [<string>, ...]  # skeleton fragment hints (source/package paths)
      build_command: <string>    # OPTIONAL build/compile command (C++ reuses build_systems.cmake)
      run_command: <string>      # smoke/run command for the generated project
      test_template: <string>    # unit-test file the engine seeds under tests/
```

| Sub-key | Type | Meaning |
|---|---|---|
| `build_file` | string | The build/manifest file emitted for the shape (`pyproject.toml` for Python, `CMakeLists.txt` for C++, the latter mirroring `samples/cppapp`'s GoogleTest-via-FetchContent setup). |
| `skeleton` | list[string] | Skeleton **fragment hints**: the source/package paths the engine lays down for the shape (e.g. `<name>/__main__.py`, `src/main.cpp`). `<name>` stands for the project name resolved at emit time. |
| `build_command` | string | **Optional** build/compile command. For C++ it reuses the adaptive `build_systems.cmake` configure+build; Python needs none and may omit it. |
| `run_command` | string | The smoke/run command that proves the generated project runs (a `python -m <name>` / import for Python, the built executable or `ctest` for C++). |
| `test_template` | string | The unit-test file the engine seeds under `tests/` (a pytest smoke test for Python, a GoogleTest smoke suite for C++, the latter registered with CTest via `test_frameworks.googletest`). |

These keys are **deterministic facts only** (no model inference): `scaffold.py`
reads them WHEN PRESENT and otherwise uses its complete built-in templates, so
the section is a hook, never a requirement. `python.yml` and `cpp.yml` declare
`generate` for their supported shapes; `csharp.yml`/`groovy.yml` remain **stubs
and omit it**.

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
