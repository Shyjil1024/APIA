# APIA Usage Guide

APIA is a **portable GitHub Copilot skill package** that you install by
**committing it into your own repository**. It rides entirely on your existing
Copilot session: there is no external LLM backend, no separate API, and no LLM
API key to configure. All model inference runs on the Copilot session you
already have; the only external *data* comes through MCP/Copilot tool-use.

APIA drives changes through a human-gated loop:

```
ingest -> clarify -> modify -> test -> apply
```

Every arrow is a **human-approval gate**: APIA stops, shows a compact summary,
and waits for you to confirm before moving on. Off-model preprocessing scripts
do the mechanical work, and a cached, committed knowledge base avoids
re-reading the repo, so the loop stays token/credit efficient.

This guide covers the files worth looking into, what each does, and how to set
APIA up on your machine.

---

## Quick install (one command)

The fastest way to add APIA to your own repository is the one-line installer.
Run it **from your project root**. It fetches the APIA repo into a temporary
directory, copies only the `.github/` and `.apia/` directories into the current
directory, and cleans up the temp copy. Nothing in your project is deleted;
files are only added or merged.

**Windows PowerShell:**

```powershell
iwr https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.ps1 | iex
```

**macOS / Linux (bash):**

```sh
curl -fsSL https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.sh | bash
```

### What it does

- Shallow-clones `https://github.com/Shyjil1024/APIA` into a unique temp dir
  (outside your project). If `git` is not installed, it falls back to
  downloading the repo zip (via `Invoke-WebRequest` / `curl`).
- Copies **only** `.github/` (the Copilot-facing artifacts) and `.apia/` (the
  APIA engine, config, scripts, adapters) into `./`. `docs/`, `samples/`, and
  APIA's own `README`/`.gitignore` are skipped by default.
- Always removes the temp clone, even on failure.

### Optional flags

Append these to also copy the extras (default copies neither):

- `--with-samples` — also copy the `samples/` directory.
- `--with-docs` — also copy the `docs/` directory.

```sh
# bash: pass flags after `-s --`
curl -fsSL https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.sh | bash -s -- --with-samples --with-docs
```

```powershell
# PowerShell: download then invoke with switches
$s = iwr https://raw.githubusercontent.com/Shyjil1024/APIA/main/install.ps1
& ([scriptblock]::Create($s.Content)) -WithSamples -WithDocs
```

### Safety when `.github/copilot-instructions.md` already exists

If your repo already has a `.github/copilot-instructions.md`, the installer
will **not** overwrite it. It copies APIA's `prompts/`, `skills/`, and
`agents/` into `.github/`, writes APIA's protocol file alongside yours as
`.github/copilot-instructions.apia.md`, and prints a warning. Review that file
and merge the APIA protocol into your own `copilot-instructions.md` to activate
the loop.

### After installing

1. Install the off-model script dependencies: `pip install pytest pyyaml`
2. Reload VS Code (**Developer: Reload Window**).
3. Run `/apia-ingest` to learn the project.

The manual copy instructions below remain available as a fallback if you prefer
to copy the files yourself.

---

## Files to look into

For each entry below, **Action** tells you whether you normally **EDIT**,
**READ**, or just **LEAVE** the file.

### Portable Copilot artifacts (`.github/`)

| File / path | Purpose | Action |
| --- | --- | --- |
| `.github/copilot-instructions.md` | Tier-0 universal, always-on core protocol. Honored by **all** Copilot hosts, including JetBrains. This is the heart of the loop. | READ |
| `.github/prompts/*.prompt.md` | Tier-1 portable slash-command entrypoints: `apia-ingest`, `apia-feature`, `apia-clarify`, `apia-test`, `apia-apply` (invoked as `/apia-ingest`, `/apia-feature`, and so on). Work in VS Code, Visual Studio, and JetBrains. | READ |
| `.github/skills/*/SKILL.md` | Tier-2 Agent Skills: `apia-ingest`, `apia-feature-loop`, `apia-test-authoring`. Two run in `context: fork` for token isolation. VS Code Agent Host only. | READ |
| `.github/agents/*.agent.md` | Tier-2 custom agents: `apia-planner` (read-only), `apia-implementer` (edit), `apia-tester` (test). Their `handoffs` implement the approval gates. VS Code Agent Host only. | READ |

### Config, scripts, adapters, and knowledge (`.apia/`)

| File / path | Purpose | Action |
| --- | --- | --- |
| `.apia/config.yml` | Per-stage `token_budget`, the `confidence_threshold: 0.75` anti-hallucination gate, the `stacks` list, and `knowledge_dir`. The one file users commonly tune. | EDIT |
| `.apia/adapters/python.yml` | Per-stack build/test command map for Python. **Complete** and fully supported today. | LEAVE (unless customizing) |
| `.apia/adapters/cpp.yml` | Per-stack command map for C++. **Complete** and supported today (CMake + MSBuild; GoogleTest/Catch2/CTest/VSTest; build-only phase; optional per-project override). The verify harness accepts it. | LEAVE (unless customizing) |
| `.apia/adapters/csharp.yml`, `.apia/adapters/groovy.yml` | Per-stack command maps for C# and Groovy. **Stubs** (`test_command: null`); the verify harness intentionally refuses to run against a stub. Fill these in to support those stacks. | EDIT (to enable the stack) |
| `.apia/scripts/ingest.py` | Off-model ingestion engine: static tree walk, manifest parse, test discovery, hashing. **No LLM/network calls.** Writes the knowledge base. | LEAVE |
| `.apia/scripts/verify.py` | Off-model test/verify harness: existing-tests regression guard (with a real before/after baseline delta via `--baseline-only`) plus new tests. Refuses stub adapters. | LEAVE |
| `.apia/knowledge/` | The cached, **committed** project knowledge base. Intentionally committed (not git-ignored) so it is shared across the team and survives sessions. | LEAVE (generated) |

### Reference docs and samples

| File / path | Purpose | Action |
| --- | --- | --- |
| `README.md` | High-level overview of APIA. Start here. | READ |
| `docs/ARCHITECTURE.md` | Full design: loop stages, token efficiency, anti-hallucination. | READ as needed |
| `docs/PORTABILITY.md` | Per-IDE support matrix (Tier 0/1/2 by host). | READ as needed |
| `docs/PROOF.md` | Evidence the loop works end to end. | READ as needed |
| `docs/PHASE-1-TASKS.md` | Phase-1 task breakdown. | READ as needed |
| `samples/pyapp/` | Example repo with existing passing tests (regression-guard path). | LEAVE / optional (safe to delete) |
| `samples/legacy_pyapp/` | Example legacy repo with no tests (characterization-test path). | LEAVE / optional (safe to delete) |

The `samples/` directories only exist to prove the loop; a consumer repo can
delete them.

---

## Setup on your machine

### Prerequisites

- **An IDE with GitHub Copilot.** The **VS Code Agent Host** gives the richest
  experience: Tier-2 skills, custom agents, and `handoffs` (the approval gates)
  all work. **JetBrains** and **Visual Studio 2022** get the Tier-0 instructions
  and Tier-1 prompt files only (no Tier-2 skills/agents), so the loop still runs
  as a **single agent following the written protocol**. For a C++ project on
  **VS 2022** this is the supported path (see the VS 2022 notes below).
- **Python 3.9+** to run the off-model scripts.
- **pytest** and **PyYAML**:

  ```sh
  pip install pytest pyyaml
  ```

  (`pytest` runs the test harness; the scripts use PyYAML to parse
  `.apia/config.yml` and the adapters.)

### Install

Copy and commit the `.github/` and `.apia/` directories (and optionally
`docs/`) into the **root** of your own target repository:

```sh
cp -r .github .apia /path/to/your-repo/
# optional reference docs:
cp -r docs /path/to/your-repo/

cd /path/to/your-repo
git add .github .apia
git commit -m "chore: install APIA Copilot skill package"
```

Note that `.apia/knowledge/` is **committed on purpose** so the learned
knowledge is shared across the team.

### First run

Open the repo in a Copilot-enabled IDE, then kick off ingestion one of three
ways:

1. Run the Tier-1 slash-command `/apia-ingest`, **or**
2. Invoke the Tier-2 `apia-ingest` skill (VS Code Agent Host), **or**
3. Run the off-model preprocessor directly from the repo root:

   ```sh
   python3 .apia/scripts/ingest.py <repo>
   ```

   Useful flags:

   ```sh
   # force a complete re-ingest instead of the incremental cache
   python3 .apia/scripts/ingest.py <repo> --full

   # write the knowledge base to a custom directory
   python3 .apia/scripts/ingest.py <repo> --knowledge-dir DIR
   ```

Then review and confirm the knowledge summary at the ingest approval gate.

### Verifying a change with the harness (optional CLI usage)

Capture a pre-change green baseline first so the regression phase can compute a
true before/after delta:

```sh
# 1) capture the green baseline BEFORE your change
python3 .apia/scripts/verify.py <repo> --baseline-only --json

# 2) after the change, run the regression guard + the new tests
python3 .apia/scripts/verify.py <repo> --new-tests <path> --json
```

The harness drives only the stack adapter's local test command and refuses to
run against a stub adapter.

---

## C++ quickstart

C++ is **supported today** via the complete `cpp.yml` adapter (CMake + MSBuild;
GoogleTest/Catch2/CTest/VSTest). The portable default is **CMake + CTest**. From
your C++ project root:

```sh
# 0) preflight the toolchain (offline): cmake/ctest/compilers + VS shell warning
python3 .apia/scripts/ingest.py --doctor
python3 .apia/scripts/verify.py --doctor <repo>

# 1) learn the project (detects build system + test framework, writes project-facts)
python3 .apia/scripts/ingest.py <repo>

# 2) preview the plan without building or changing anything
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/cpp.yml --dry-run

# 3) compile-only regression signal (no tests run) - works even with zero tests
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/cpp.yml --build-only

# 4) capture the green baseline BEFORE your change
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/cpp.yml --baseline-only --json

# 5) after the change: build + regression guard + the new tests
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/cpp.yml --json
```

The harness runs a **BUILD** phase first (does it still compile?), then the
CTest-driven baseline/regression/new phases, parsing the adapter's
`result_format` (`ctest`/`gtest`) into the same pass/fail counts as the Python
path. If detection is wrong for an unusual layout, pin values in an optional
`cpp:` block in `.apia/config.yml` (a documented fallback, never required). See
`docs/PROOF.md` for a worked C++ walkthrough with real output.

### Visual Studio 2022 notes

VS 2022 honors **only Tier-0** (`.github/copilot-instructions.md`) **and Tier-1**
(the `*.prompt.md` slash-commands) - not Tier-2 skills or custom agents. So on VS
2022 you run the loop as a **single agent following the Tier-0 protocol**, invoking
the prompt files per stage. **MSBuild and `cl` require the VS 2022 Developer
Command Prompt** (or the Developer PowerShell); run the scripts from that shell so
the toolchain is on `PATH`. `verify.py --doctor` warns when the Developer shell is
not active.

### Windows verification checklist (run on your own machine)

The MSBuild/VSTest path cannot be executed in this Linux repo, so verify it on
Windows yourself:

1. Open the **VS 2022 Developer Command Prompt** (or Developer PowerShell).
2. Confirm the toolchain is present: `cmake --version`, `msbuild -version`
   (and `ctest --version` for the CMake path).
3. Run the preflight: `python .apia\scripts\verify.py --doctor <repo>` and
   confirm no required tool is reported missing.
4. Run the **build-only** compile check:
   `python .apia\scripts\verify.py <repo> --adapter .apia\adapters\cpp.yml --build-only`.
5. Capture the baseline, then run the tests:
   `python .apia\scripts\verify.py <repo> --adapter .apia\adapters\cpp.yml --baseline-only`
   before your change, then `... --json` after it.
6. Review the **before/after delta** the regression phase reports (passed/failed
   counts vs. the baseline, and any removed tests) to confirm no regression.

### Tuning

- Adjust `.apia/config.yml` to taste: raise or lower `confidence_threshold`
  (the clarify gate) and the per-stage `token_budget` values.
- Add or complete `.apia/adapters/*.yml` for non-Python stacks (fill in
  `test_command`, `build_command`, discovery globs, and so on).

---

## The gated loop in practice

The five stages each pause at a **human-approval gate**:

1. **ingest** — confirm the learned knowledge summary.
2. **clarify** — answer the numbered clarifying questions APIA raises when its
   confidence is below `confidence_threshold`.
3. **modify** — approve the proposed diff.
4. **test** — review the regression results and the new test results.
   For legacy code with no tests, APIA writes **characterization tests first**
   to pin current behavior before changing anything.
5. **apply** — confirm the commit.

---

## Stack support status

- **Python** — fully supported today (`.apia/adapters/python.yml` is complete).
- **C++** — **first-class / supported today** (`.apia/adapters/cpp.yml` is
  complete: CMake + MSBuild, GoogleTest/Catch2/CTest/VSTest, build-only phase,
  optional per-project override). The CMake + GoogleTest + CTest path is proven by
  execution; the MSBuild/VSTest path is unit-tested against fixtures (no Windows
  toolchain in CI). The verify harness accepts the completed cpp adapter.
- **C#, Groovy** — adapter **stubs** (`test_command: null`). Fill in
  `test_command` and the rest of the adapter to enable them.
- **Any other language** — needs a new adapter under `.apia/adapters/`.

The loop and reasoning are language-agnostic, but the test-and-validate
guarantee depends on an adapter existing for your stack.
