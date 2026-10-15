# APIA - Autonomous Project Intelligence Agent

APIA is a **portable GitHub Copilot skill package**. You commit it into your own
repository and it teaches Copilot how to understand and safely change *that
specific project* through a human-gated loop:

```
ingest -> clarify -> modify -> test -> apply
```

Every arrow is a **human-approval gate**: APIA stops, shows a compact summary, and
waits for you.

## Key properties

- **Rides your existing Copilot session.** APIA **never** calls a separate or
  external LLM API. All model inference is your Copilot session; external *data*
  is fetched via MCP/Copilot tool-use only. No LLM API key is ever required.
- **Token/credit efficient.** Off-model preprocessing scripts do the mechanical
  work; a cached, committed knowledge base avoids re-reading the repo; heavy
  stages run in forked contexts. See `docs/ARCHITECTURE.md` section 5.
- **Anti-hallucination.** Below a confidence threshold, APIA asks numbered
  clarifying questions instead of guessing. See `docs/ARCHITECTURE.md` section 6.
- **Legacy-aware.** Handles projects with no tests by generating characterization
  tests that pin current behavior before any change.
- **Grounded iterate-to-green (Layer 1 + Layer 2).** Verification grades changes
  against repo-derived eval data (characterization > golden > invariant >
  differential), and a bounded self-correction loop iterates to green under a hard
  `max_iterations` cap. The verifier is **deterministic tooling, never a second
  model**; the off-model scripts call no model and the fix step is your live Copilot
  session only. New-behavior acceptance criteria need one human confirmation (the
  anti-circularity gate). See `docs/ARCHITECTURE.md` sections 3a-3c.
- **Iterate-to-Best (green AND the reviewer gate), bounded and HONEST.** The iterate loop
  no longer stops at merely "green" (tests pass): each iteration's acceptance criterion is
  tests/build green **AND** the reviewer gate (`scan.apply_thresholds` vs
  `review.thresholds`) passing. A single shared decision
  (`evalloop.evaluate_quality_gated`) defines "best" once and is reused by all three entry
  points (`verify.py --iterate` with `--findings`, the greenfield generation gate, the
  orchestrator), for both Python and C++. On a failing gate the EXACT scanner findings fold
  into the EXISTING structured failure report; the loop tracks **best-so-far** and, on the
  hard `max_iterations` cap, STOPS and asks carrying the BEST attempt (not the last) plus a
  specific residual summary. The quality bar is the tunable `review.thresholds`; the only
  new knob is `eval_data.iterate_to_best.best_so_far_tie_break`. "best" means **meets your
  configured deterministic thresholds, or the closest bounded attempt with an honest
  residual report** - not provably optimal code (the ceiling is still the Copilot model, the
  tests, and what the scanners detect). The scripts call no model; the per-iteration fix is
  your live Copilot session. See `docs/ARCHITECTURE.md` section 3b and `docs/PROOF.md`.
- **Continuous Reviewer (security / quality / perf / limitations).** An off-model
  engine (`.apia/scripts/scan.py`) runs real **local** scanners (bandit/pip-audit/
  ruff/flake8/radon; cppcheck/clang-tidy), normalizes their output into one canonical
  finding shape, and applies a composable severity gate; the model only summarizes and
  prioritizes the grounded findings - it never invents one, and a missing scanner is
  reported, not faked. The findings report and the shared gate are a **composable
  interface** the greenfield-generation gate reuses. See
  `docs/ARCHITECTURE.md` section 3d.
- **Greenfield generation (Python / C++), two-tier and HONEST.** From one prompt and a
  single batched clarify, APIA generates a working, **tested, runnable** project: an
  off-model engine (`.apia/scripts/scaffold.py`) detects the project shape, emits the
  skeleton, and verifies it end to end. The **five fully-verifiable shapes**
  (CLI, library, web API, C++ console, C++ library) are built, run, and unit-tested
  **all-green**; shapes that cannot be auto-verified headless (GUI/embedded/driver) are
  reported **honest-partial, never a faked green**, with a plain-language note on what you
  must check. Each generated increment passes the SAME reviewer gate as a human change.
  It is **local-only** (no auto-push/PR/CI) and runs on **VS 2022 as a single agent**
  via `/apia-new`. See `docs/ARCHITECTURE.md` section 3e and `docs/PROOF.md`.
- **Orchestrator (multi-task autonomy), bounded and HONEST.** A whole feature or app is
  many tasks. The orchestrator breaks a larger goal into an **ordered task graph** and
  drives each task through the loops above, running autonomously **between batched human
  checkpoints** instead of one gate per task. An off-model engine
  (`.apia/scripts/orchestrate.py`) does the deterministic graph/state mechanics (build +
  validate, cycle detection, the ready frontier, a resumable run-state + human-readable
  audit log under `.apia/runs/`, the global stop bound); the live Copilot session does the
  per-task work. It is **bounded + fail-loud** (global stop bound + the REUSED per-task
  `max_iterations` cap + stop-and-ask on a stuck task), reuses the SAME reviewer/eval/
  generation gates with no new knobs, is **strictly local** (no auto-push), and runs on
  **VS 2022 as a single agent** via `/apia-run`. It is **minimal interaction, not zero
  interaction** -- the human still approves at the batched checkpoints and the final apply.
  See `docs/ARCHITECTURE.md` section 3f and `docs/PROOF.md`.
- **LOCAL knowledge & capability sharing (git-based).** Teams share through the git repo,
  exactly like `.apia/knowledge/`. An off-model engine (`.apia/scripts/share.py`) provides
  versioned **knowledge packs** (safe no-clobber merge + opt-in `--force`), shared
  **org/team profiles** (same `config.yml` keys, LOCAL overrides win), a **local
  plugin/extension** contract for adapters/scanners/shapes (committed files, additive,
  **no download/marketplace/network**), and a **capability manifest** (reusing the doctor +
  detection). It makes **zero LLM/network calls** and runs on **VS 2022 as a single agent**
  via `/apia-share`. External/hosted/cross-org sharing is **intentionally out of scope**
  (a separate product with its own security/hosting/governance model), **not** a deferred
  phase. See `docs/ARCHITECTURE.md` section 3g and `docs/PROOF.md`.
- **Portable.** Degrades gracefully by host capability (Tier 0/1/2). See
  `docs/PORTABILITY.md`.

## Install model

APIA is installed by **committing it into your repository**. The portable
artifacts live under `.github/` and the cached knowledge base, config, scripts,
and per-stack adapters live under `.apia/`:

```
.github/
  copilot-instructions.md   # Tier 0: universal core protocol (always-on)
  prompts/                   # Tier 1: portable slash-command entrypoints
  skills/                    # Tier 2: rich, token-efficient skills (VS Code host)
  agents/                    # Tier 2: per-stage custom agents + approval-gate handoffs
.apia/
  knowledge/                 # cached, committed project knowledge base (shared with the team)
  runs/                      # committed resumable run-state + audit log (orchestrator)
  config.yml                 # token budget, confidence threshold, stacks, knowledge_dir
  scripts/                   # off-model preprocessing (indexing, test discovery, sharing)
  adapters/                  # per-stack build/test command maps
  plugins/                   # LOCAL team plugins (adapters/scanners/shapes); committed, additive
  profiles/                  # example committable org/team profile (same keys as config.yml)
```

The `.apia/knowledge/` and `.apia/runs/` directories are **committed on purpose** so the
learned knowledge and any long autonomous run are shared across the team and survive
sessions and windows.

## Primary stacks

Python, C++, C#, and Groovy are first-class. **Python and C++ are supported
today** (C++ via CMake + MSBuild, with GoogleTest/Catch2/CTest/VSTest); C# and
Groovy ship as adapter stubs and reach parity in Phase 2.

## Roadmap status

APIA's roadmap **ends at LOCAL, git-based knowledge & capability sharing** (delivered: the
reviewer, greenfield generation, the orchestrator, iterate-to-best, and now the Phase-6a
sharing layer). The remaining genuinely-deferred items are C#/Groovy adapter parity,
concrete MCP **data** sources, auto-push/PR/MR/CI/packaging/signing, and an optional opt-in
self-hosted second-opinion reviewer. **External / hosted / cross-org sharing** (a hosted
knowledge service, an MCP server exposing APIA, cross-org APIs, collaborative workspaces,
marketplace distribution) is **intentionally out of scope** -- a separate product with its
own security/hosting/governance model -- **not** a deferred APIA phase. See
`docs/ARCHITECTURE.md` section 7.

## Running the Phase-1 proof

The `samples/` directory contains two sample Python repos used to prove the loop:

- `samples/pyapp/` - a tiny calculator package with **existing passing tests**
  (the regression-guard green baseline).
- `samples/legacy_pyapp/` - a legacy module with **no tests** (the
  characterization-test path demonstrated in FEAT-004).

The `samples/generated/` directory holds the Phase-3 greenfield-generation proof
projects (Python CLI/library/web-API, C++ console/library, plus an honest-partial
`gui`), each generated, built, run, and tested through `scaffold.py`; see
`docs/PROOF.md` for the executed-vs-fixture split.

Set up the toolchain and run the baselines:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

# green baseline: all tests pass
python3 -m pytest samples/pyapp -q

# legacy case: no tests are collected
python3 -m pytest samples/legacy_pyapp -q
```

`pip install -r requirements-dev.txt` is the single supported setup command: it
provisions everything needed to run the full test suite green and exercise the
Python Reviewer scanners -- the test runner (`pytest`), the runtime dependency
(`PyYAML`, via `requirements.txt`), and the Python scanners the Python adapter
invokes (`bandit`, `pip-audit`, `ruff`, `flake8`, `radon`).

**System prerequisites (not pip).** A few tools the loop uses are native/system
packages, installed via `apt`/`brew` rather than pip, and are deliberately NOT
in the requirements files:

- `cppcheck` and `clang-tidy` -- the C++ Reviewer scanners invoked by
  `.apia/adapters/cpp.yml` (e.g. `apt install cppcheck clang-tidy`).
- `cmake`, `ctest`, and a C++ compiler -- the C++ build/test toolchain for the
  C++ samples (e.g. `apt install cmake g++`). The C++ e2e tests `shutil.which`-
  guard these and skip cleanly when they are absent.

See `docs/ARCHITECTURE.md` for the full design and `docs/PHASE-1-TASKS.md` for the
Phase-1 task breakdown.

## Measuring effectiveness (apia-eval)

`apia-eval` is a small, offline, deterministic benchmark that measures APIA's
deterministic engines (gate / defect-detection / verify / ingest) against seven
scenarios with fixed, known ground truth, and reports a fixed metric set defined
before the run. After the one-time `pip install -r requirements-dev.txt`
bootstrap above (which installs the scanners the scored scenarios need), run it
with the single documented command:

```sh
.venv/bin/python .apia/scripts/evalbench.py --out .apia/eval/baseline/latest.json
```

See `.apia/eval/README.md` for the metric definitions, thresholds, scenario list,
and how to read the results, and `.apia/eval/baseline/` for the committed
baseline snapshot (`baseline.json`) and its interpretation (`BASELINE.md`).

**Scope boundary.** apia-eval measures only the deterministic tooling's behavior
on fixed inputs — fully local, no network, no LLM — and makes **no** claim about
the quality of AI/LLM-generated code.

## Running a multi-task orchestrator run

For a goal bigger than one change -- a whole feature on an existing project, or a whole
greenfield app -- use the orchestrator instead of running the loop by hand per task. In
your Copilot chat, start it with the Tier-1 prompt:

```
/apia-run add a rounding option to the pyapp calculator report
```

The orchestrator plans the goal into an **ordered task graph** (tasks with
dependencies), asks **everything knowable up front** in one batched clarify, and then
runs **uninterrupted to the next checkpoint** instead of stopping at every task. The
off-model engine does the deterministic mechanics; you drive it with the same script the
agent uses:

```sh
# 1. Build + validate the graph and write the initial run-state (one process).
.venv/bin/python .apia/scripts/orchestrate.py --init "add a rounding option to the pyapp calculator report" \
    --tasks .apia/runs/example-tasks.json --run-state .apia/runs/my-run.json --run-id my-run

# 2. See the ready frontier at any time.
.venv/bin/python .apia/scripts/orchestrate.py --status --run-state .apia/runs/my-run.json

# 3. Advance a task (running -> done); a 'done' unblocks its dependents.
.venv/bin/python .apia/scripts/orchestrate.py --step --run-state .apia/runs/my-run.json --task-id ingest-pyapp --to running
.venv/bin/python .apia/scripts/orchestrate.py --step --run-state .apia/runs/my-run.json --task-id ingest-pyapp --to done

# 4. Read the human-readable audit trail.
.venv/bin/python .apia/scripts/orchestrate.py --audit --run-state .apia/runs/my-run.json
```

(The step status flag is `--to` / `--status-value`; argparse reserves `--status` for the
status action.)

### Batched-checkpoint UX

You answer a batch of clarifying questions and grant a batch of approvals **once**, up
front, then the run advances task by task on its own until it reaches a meaningful
checkpoint or hits **genuine uncertainty**. You still approve at the batched checkpoints
and at the final apply, and nothing is ever auto-pushed. This is **minimal, well-placed
interaction -- not zero interaction**: a stuck task **stops and asks a specific
question** rather than looping forever or silently applying something broken.

### Stop and resume across sessions and windows

The run-state and audit log are written under `.apia/runs/` and are **committed on
purpose** (like `.apia/knowledge/`). You can **stop** a run at any point -- just stop
interacting -- and **resume** it later from a fresh session or another window: a new
`orchestrate.py` process reads the saved run-state and continues from exactly the frontier
where it stopped. Because the files are committed, a teammate can inspect or continue the
same run. A committed example is in `.apia/runs/example-run.json` +
`.apia/runs/example-run.audit.log` (see `docs/PROOF.md`).

### Bounded and fail-loud

Every run is bounded three ways so it can never grind forever or ship something broken:
a **global stop bound** (`orchestrate.global_max_tasks` in `.apia/config.yml`), the
**REUSED per-task** `max_iterations` cap on each code/test task's self-correction loop,
and **stop-and-ask** on a failed task (it halts the run, leaves dependents blocked, and
surfaces a specific question). A cyclic graph is **rejected** at init, never run.

### Visual Studio 2022

VS 2022 loads the Tier-1 **prompt** and the concise Tier-0 instructions but **not** the
Tier-2 agents/skills. The orchestrator is designed for this: `/apia-run` is a Tier-1
prompt and the orchestration protocol is summarized in `.github/copilot-instructions.md`,
so a **single agent** runs the whole task-graph flow on VS 2022 (the same single-agent
degradation as `/apia-review` and `/apia-new`). See `docs/PORTABILITY.md` for the full
tier matrix.
