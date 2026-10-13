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
  config.yml                 # token budget, confidence threshold, stacks, knowledge_dir
  scripts/                   # off-model preprocessing (indexing, test discovery)
  adapters/                  # per-stack build/test command maps
```

The `.apia/knowledge/` directory is **committed on purpose** so the learned
knowledge is shared across the team and survives sessions.

## Primary stacks

Python, C++, C#, and Groovy are first-class. **Python and C++ are supported
today** (C++ via CMake + MSBuild, with GoogleTest/Catch2/CTest/VSTest); C# and
Groovy ship as adapter stubs and reach parity in Phase 2.

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
pip install pytest

# green baseline: all tests pass
python3 -m pytest samples/pyapp -q

# legacy case: no tests are collected
python3 -m pytest samples/legacy_pyapp -q
```

See `docs/ARCHITECTURE.md` for the full design and `docs/PHASE-1-TASKS.md` for the
Phase-1 task breakdown.
