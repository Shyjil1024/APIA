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

Phase 1 ships a fully implemented **`python.yml`**; `cpp.yml`, `csharp.yml`, and
`groovy.yml` are documented **stubs** with `TODO(phase-2)` markers that Phase 2
fills to parity.

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

## Contract notes

- Keys are **always present**; stub values are `null` (or a `TODO(phase-2)`
  string) rather than omitted, so consumers can rely on the shape.
- `manifests` / `extensions` must stay consistent with the detection rules in
  `.apia/scripts/ingest.py` (`STACK_RULES`) so ingestion and the adapter agree
  on what counts as the stack.
- Commands are run from the **repository root** being analyzed, not from the
  package directory.

## Adding or completing a stack (Phase 2)

1. Copy `python.yml` as a starting shape.
2. Fill every `null` / `TODO(phase-2)` with the real command for that stack.
3. Keep `manifests`/`extensions` aligned with `ingest.py` `STACK_RULES`.
4. Add a characterization-test template under
   `.github/skills/apia-test-authoring/templates/` and point
   `characterization_test_template` at it.
5. Ensure `name` matches the entry in `.apia/config.yml`.
