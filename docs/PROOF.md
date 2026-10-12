# Phase-1 end-to-end proof (FEAT-004)

This document shows how to run and interpret the **objective, Copilot-free proof**
that the APIA loop's mechanical stages work end-to-end on the sample repos. It maps
each step to the five-stage loop in [`ARCHITECTURE.md` section 3](ARCHITECTURE.md)
(`ingest -> clarify -> modify -> test -> apply`).

Everything here runs with **zero LLM/network calls**. The model stages (CLARIFY,
and the *authoring* of MODIFY/TEST content) run on the user's Copilot session in a
real session; this proof exercises the deterministic, off-model machinery that those
stages drive: the ingester (`.apia/scripts/ingest.py`) and the verification harness
(`.apia/scripts/verify.py`), both reading the Python stack adapter
(`.apia/adapters/python.yml`).

## What the two sample repos prove

| Sample | Starting state | Proves |
|---|---|---|
| `samples/pyapp` | Has a green test suite | Regression guard + new test for a worked change |
| `samples/legacy_pyapp` | **No tests** (legacy) | Characterization tests pin current behavior, then a safe change keeps them green + a new test passes |

## Prerequisites

A virtualenv with `pytest` and `PyYAML` (the adapter is YAML). In this workspace:

```bash
.venv/bin/python --version   # Python 3.9.x
```

Run the harness with the SAME interpreter that owns the test deps; `verify.py`
rewrites the adapter's `python -m pytest` to use `sys.executable`.

## Step 1 - INGEST (Stage 1): build the cached knowledge base, off-model

```bash
.venv/bin/python .apia/scripts/ingest.py samples/pyapp
.venv/bin/python .apia/scripts/ingest.py samples/legacy_pyapp
```

Each run walks the repo and writes compact Markdown artifacts under
`.apia/knowledge/` (`project-map.md`, `stack.md`, `test-inventory.md`, ...) plus an
incremental `.index.json`. **Interpretation:** `stack.md` should report `python`
(pytest), and `test-inventory.md` should list the discovered tests. For
`legacy_pyapp` the inventory now lists the generated characterization + new tests
(before FEAT-004 it reported "No tests found" - the legacy trigger).

> Maps to **Stage 1 INGEST**: deterministic preprocessing so the model only ever
> summarizes distilled facts (the biggest token saver).

## Steps 2-3 - CLARIFY / MODIFY (Stages 2-3): the worked change

These are the model-driven stages in a live session. For an objective proof they are
materialized as a **committed worked example** so the result is reproducible:

- `samples/pyapp`: `calc/operations.py` gained a **new** function `multiply` and the
  existing `divide` had its error message made more descriptive **without** changing
  its behavior (it still raises `ZeroDivisionError`). A new test
  `tests/test_multiply.py` covers the new behavior.
- `samples/legacy_pyapp`: `legacy_pyapp/inventory.py` gained a **new** function
  `reorder_quantity`, added **after** characterization tests pinned the module's
  current behavior, without touching any existing function.

> Maps to **Stage 2 CLARIFY** (agreed change spec) and **Stage 3 MODIFY** (the diff).

## Step 4 - TEST (Stage 4): the verification harness

The harness runs only the adapter's **local** `test_command` and reports three
phases: **baseline** (existing tests), **regression** (existing tests still green
with the change present), and **new** (the newly added tests).

### pyapp (regression + new behavior)

```bash
.venv/bin/python .apia/scripts/verify.py samples/pyapp \
    --new-tests tests/test_multiply.py --json
```

Expected human summary:

```
APIA verify :: pyapp (adapter: python)
  [PASS] baseline   7 passed, 0 failed, 0 error, 0 skipped
  [PASS] regression 7 passed, 0 failed, 0 error, 0 skipped
  [PASS] new        3 passed, 0 failed, 0 error, 0 skipped
  => ALL GREEN
```

**Interpretation:** `baseline`/`regression` green = no regression from the change;
`new` green = the added `multiply` behavior is verified.

### legacy_pyapp (characterization path)

```bash
.venv/bin/python .apia/scripts/verify.py samples/legacy_pyapp \
    --new-tests tests/test_reorder.py --json
```

Expected:

```
APIA verify :: legacy_pyapp (adapter: python)
  [PASS] baseline   13 passed, 0 failed, 0 error, 0 skipped
  [PASS] regression 13 passed, 0 failed, 0 error, 0 skipped
  [PASS] new        4 passed, 0 failed, 0 error, 0 skipped
  => ALL GREEN
```

**Interpretation:** the characterization tests (which pin the module's *current*
behavior, quirks included) stay green across the safe change = behavior preserved;
the `new` phase proves the added `reorder_quantity` behavior.

Capture the green baseline **before** a change with `--baseline-only`.

> Maps to **Stage 4 TEST**: regression guard + new verification + the legacy
> characterization-first path, all via the stack adapter.

### No LLM / network (hard constraint)

`verify.py` spawns exactly one kind of subprocess: the adapter's local test command.
Confirm it issues no external calls:

```bash
grep -rniE 'openai|anthropic|chat/completions|requests\.' .apia/scripts/verify.py
```

This returns no hits (any match is in prohibiting prose only).

## Step 5 - APPLY (Stage 5)

Out of scope for this off-model proof: APPLY is the human-gated commit/PR, which the
orchestrator performs. The harness leaves the samples in a committable, green state.

## One command: the automated end-to-end test

The entire proof is also encoded as a pytest so CI can assert it:

```bash
.venv/bin/python -m pytest .apia/scripts/tests -q
```

`.apia/scripts/tests/test_e2e_loop.py` runs `ingest(samples/pyapp)` -> checks the
knowledge artifacts exist -> runs `verify.py` and asserts baseline/regression/new are
green; then does the same for `samples/legacy_pyapp` (characterization + new). It uses
subprocess with each sample as the working directory, so each sample's own
`pyproject.toml` `pythonpath=["."]` applies regardless of the outer pytest rootdir.
A passing run of this file **is** the Phase-1 milestone proof.
