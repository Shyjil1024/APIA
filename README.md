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

Python, C++, C#, and Groovy are first-class. **Python is the Phase-1 primary**
(its adapter ships first); the others ship as adapter stubs and reach parity in
Phase 2.

## Running the Phase-1 proof

The `samples/` directory contains two sample Python repos used to prove the loop:

- `samples/pyapp/` - a tiny calculator package with **existing passing tests**
  (the regression-guard green baseline).
- `samples/legacy_pyapp/` - a legacy module with **no tests** (the
  characterization-test path demonstrated in FEAT-004).

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
