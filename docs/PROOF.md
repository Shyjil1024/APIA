# End-to-end proof (Python + C++)

This document shows how to run and interpret the **objective, Copilot-free proof**
that the APIA loop's mechanical stages work end-to-end on the sample repos. It maps
each step to the five-stage loop in [`ARCHITECTURE.md` section 3](ARCHITECTURE.md)
(`ingest -> clarify -> modify -> test -> apply`).

Everything here runs with **zero LLM/network calls**. The model stages (CLARIFY,
and the *authoring* of MODIFY/TEST content) run on the user's Copilot session in a
real session; this proof exercises the deterministic, off-model machinery that those
stages drive: the ingester (`.apia/scripts/ingest.py`) and the verification harness
(`.apia/scripts/verify.py`), reading the Python adapter (`.apia/adapters/python.yml`)
and the C++ adapter (`.apia/adapters/cpp.yml`).

## What the sample repos prove

| Sample | Stack | Starting state | Proves |
|---|---|---|---|
| `samples/pyapp` | Python | Has a green test suite | Regression guard + new test for a worked change |
| `samples/legacy_pyapp` | Python | **No tests** (legacy) | Characterization tests pin current behavior, then a safe change keeps them green + a new test passes |
| `samples/cppapp` | C++ (CMake + GoogleTest) | Has a green test suite | Build-only compile check + CTest regression guard + a worked change with a real before/after delta |
| `samples/legacy_cppapp` | C++ (CMake + GoogleTest) | Originally **no tests** | Characterization tests pin current behavior; build-only is the regression signal before they exist |

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

---

## C++ proof walkthrough (CMake + GoogleTest + CTest)

The C++ proof mirrors the Python one, through the same five stages, driven by the
completed C++ adapter (`.apia/adapters/cpp.yml`). GoogleTest is pulled in via CMake
**FetchContent** (pinned `v1.14.0`), so the samples are self-contained.

### Executed in-sandbox vs. unit-tested only (read this first)

- **EXECUTED for real in this Linux sandbox:** the **CMake + GoogleTest + CTest**
  path. The commands below were run and their output captured; FetchContent clones
  GoogleTest `v1.14.0` on the first configure.
- **UNIT-TESTED against captured fixture strings only (never executed):** the
  **MSBuild / VSTest** command-assembly and output parsing. There is **no Windows
  MSVC/MSBuild/VSTest toolchain in this sandbox**, so that path is proven by
  fixture-based parser tests in `.apia/scripts/tests/test_cpp_e2e.py`, not by a run.
  Verify it yourself on Windows with the checklist in `USAGE.md`.

### Step 1 - INGEST: detection + project-facts + confidence

```bash
.venv/bin/python .apia/scripts/ingest.py samples/cppapp
```

Walks the repo and writes the `.apia/knowledge/` artifacts, including
`project-facts.md`. **Interpretation:** `stack.md` reports `cpp`; `project-facts.md`
records the detected build system (**CMake**, from `CMakeLists.txt`) and test
framework (**GoogleTest via CTest**, from `*_test.cpp` + FetchContent) with a
**confidence score** vs. `confidence_threshold` (0.75). When confidence is below
threshold it records a precomposed, specific clarifying question instead of guessing.

Preflight the toolchain offline at any time. Passing the repo path lets the
doctor resolve which tools are **required** for that repo's detected stack (a C++
repo requires cmake/ctest/g++/make, not the Windows-only msbuild/cl):

```bash
.venv/bin/python .apia/scripts/verify.py --doctor samples/cppapp
```

Captured output (exit code **0** on this Linux host: every *required* C++ tool is
present, and the absent msbuild/cl/standalone-pytest are warned as
optional/other-platform, not counted as failures):

```
--doctor => cmake 3.22.2 / ctest 3.22.2 / g++ 11.5 / gcc / make / git present
            WARNS msbuild/cl (VS 2022 Developer Command Prompt) + standalone pytest missing
            Preflight: OK -- all required tools present (warnings are optional/other-platform)
```

### Steps 2-3 - CLARIFY / MODIFY: the worked change (dry-run)

As with Python, the model-driven stages are materialized as a committed worked
example for reproducibility: `samples/cppapp` gains a new function (covered by a new
GoogleTest case) without altering existing behavior. Preview exactly what the
verification WOULD run, changing nothing:

```bash
.venv/bin/python .apia/scripts/verify.py samples/cppapp \
    --adapter .apia/adapters/cpp.yml --dry-run
```

Captured output:

```
--dry-run => lists build / baseline / regression planned commands + reasoning
             (result_format: ctest); writes nothing; spawns nothing.
```

### Step 4 - TEST: build-only, then CTest baseline / regression / new

For C++ the harness runs a **BUILD** phase first (does it still compile?), then the
CTest-driven phases. Build-only on its own is the regression signal when there are
no tests yet:

```bash
.venv/bin/python .apia/scripts/verify.py samples/cppapp \
    --adapter .apia/adapters/cpp.yml --build-only
```

```
--build-only => [PASS] build compiled OK => ALL GREEN (no tests executed)
```

Full run (build + baseline + regression) on the green sample:

```bash
.venv/bin/python .apia/scripts/verify.py samples/cppapp \
    --adapter .apia/adapters/cpp.yml --json
```

Captured human summary:

```
[PASS] build      compiled OK
[PASS] baseline   5 passed, 0 failed, 0 error, 0 skipped
[PASS] regression 5 passed, 0 failed, 0 error, 0 skipped
=> ALL GREEN  (result_format=ctest)
```

### Real before/after delta

Capture the green baseline **before** the change with `--baseline-only`, then run
again after it. Captured delta (a new test added, no existing behavior changed):

```
before/after delta => passed +1 (5 -> 6); removed 0 node(s)
                   => no regression vs. baseline => ALL GREEN
```

And a genuine regression is caught - break an existing `EXPECT_EQ`:

```
[FAIL] baseline 4 passed, 1 failed; baseline delta: passed -1 (5 -> 4)
=> REGRESSION => VERIFICATION FAILED
```

### Characterization-first C++ case

`samples/legacy_cppapp` was **originally untested**. Its committed GoogleTest
characterization suite pins the current behavior (quirks included, e.g.
`apply_discount` does no validation) **before** a new `reorder_quantity` function is
added. Until those tests exist, `--build-only` is the only regression signal; once
they do, the regression + new phases run exactly as above. All stay green across the
safe change, proving behavior was preserved.

> Maps to **Stage 4 TEST** for C++: build-only compile check + CTest regression guard
> + new verification + the characterization-first legacy path, all via the cpp adapter.

### Step 5 - APPLY (C++)

Same as Python: APPLY is the human-gated commit, which the orchestrator performs. The
harness leaves the samples in a committable, green state and the generated `build/`
directories are gitignored.

---

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

`.apia/scripts/tests/test_cpp_e2e.py` adds the C++ coverage: fixture-based unit tests
for the ctest/gtest/msbuild(VSTest) parsers (so the MSBuild/VSTest path is proven
without a Windows toolchain), `--dry-run` and `--build-only` behavior, and - guarded by
`shutil.which('cmake')`/`shutil.which('ctest')` so the suite stays portable - the real
CMake + GoogleTest + CTest loop on `samples/cppapp` (build + baseline + regression green
with >= 3 passed, a before/after delta that catches a broken existing test) plus the
`samples/legacy_cppapp` characterization path. These RUN and pass in this sandbox.
