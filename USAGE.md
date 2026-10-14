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
| `.github/prompts/*.prompt.md` | Tier-1 portable slash-command entrypoints: `apia-ingest`, `apia-feature`, `apia-clarify`, `apia-test`, `apia-apply`, `apia-iterate`, `apia-review`, `apia-new` (invoked as `/apia-ingest`, `/apia-new`, and so on). Work in VS Code, Visual Studio, and JetBrains. | READ |
| `.github/skills/*/SKILL.md` | Tier-2 Agent Skills: `apia-ingest`, `apia-feature-loop`, `apia-test-authoring`, the four reviewer scan skills (`apia-security-scan`, `apia-quality-scan`, `apia-perf-scan`, `apia-limitations`), and the two generation skills (`apia-scaffold`, `apia-e2e-verify`). The heavy stages run in `context: fork` for token isolation. VS Code Agent Host only. | READ |
| `.github/agents/*.agent.md` | Tier-2 custom agents: `apia-planner` (read-only), `apia-implementer` (edit), `apia-tester` (test), `apia-reviewer` (read-only), `apia-architect` (generation). Their `handoffs` implement the approval gates. VS Code Agent Host only. | READ |

### Config, scripts, adapters, and knowledge (`.apia/`)

| File / path | Purpose | Action |
| --- | --- | --- |
| `.apia/config.yml` | Per-stage `token_budget`, the `confidence_threshold: 0.75` anti-hallucination gate, the `stacks` list, and `knowledge_dir`. The one file users commonly tune. | EDIT |
| `.apia/adapters/python.yml` | Per-stack build/test command map for Python. **Complete** and fully supported today. | LEAVE (unless customizing) |
| `.apia/adapters/cpp.yml` | Per-stack command map for C++. **Complete** and supported today (CMake + MSBuild; GoogleTest/Catch2/CTest/VSTest; build-only phase; optional per-project override). The verify harness accepts it. | LEAVE (unless customizing) |
| `.apia/adapters/csharp.yml`, `.apia/adapters/groovy.yml` | Per-stack command maps for C# and Groovy. **Stubs** (`test_command: null`); the verify harness intentionally refuses to run against a stub. Fill these in to support those stacks. | EDIT (to enable the stack) |
| `.apia/scripts/ingest.py` | Off-model ingestion engine: static tree walk, manifest parse, test discovery, hashing. **No LLM/network calls.** Writes the knowledge base. | LEAVE |
| `.apia/scripts/verify.py` | Off-model test/verify harness: existing-tests regression guard (with a real before/after baseline delta via `--baseline-only`) plus new tests. Also the headless Layer-2 loop surface (`--iterate`, `--max-iterations N`, `--emit-failure-report`). Refuses stub adapters. **No LLM/network calls.** | LEAVE |
| `.apia/scripts/evaldata.py` | Off-model **Layer-1** grounded eval-data generation: characterization capture, golden-file compare, built-in invariants, differential. **No model calls.** | LEAVE |
| `.apia/scripts/evalloop.py` | Off-model **Layer-2** iterate-to-green loop: one deterministic cycle, the hard `max_iterations` cap, token-budget advisory, the acceptance-criteria gate, and a model-consumable structured failure report. **No model calls.** | LEAVE |
| `.apia/scripts/scan.py` | Off-model **Reviewer** scanner engine: runs the adapter's local scanners, normalizes their output into one canonical finding shape, applies the composable `apply_thresholds()` gate, and emits a prioritized JSON report. Auto-detects the stack and excludes generated/vendored dirs. **No LLM/network calls.** | LEAVE |
| `.apia/scripts/scaffold.py` | Off-model **greenfield-generation** engine: detects the project shape from an approved spec, emits the deterministic skeleton + build file, normalizes build/run/unit-test into the shared counts shape (full for the five verifiable shapes, honest-partial for gui/embedded/driver), and decides the generation gate by reusing `scan.apply_thresholds`. **No LLM/network calls.** | LEAVE |
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

## The iterative self-correction loop (Layer 1 + Layer 2)

Beyond a single verify pass, APIA can **iterate to green**: assemble repo-derived
eval data, generate a change, run the deterministic verifier, feed the exact
structured failure back and fix, re-run, and repeat until green **or** the hard
`max_iterations` cap in `.apia/config.yml` is reached - then it **stops and asks a
specific question**. It **never silently applies a broken change and never loops
forever**, and APPLY still gates on you.

Two hard rules make this trustworthy:

- **The verifier is deterministic tooling** (tests/build/diff/golden), **never a
  second model.** The off-model scripts (`evaldata.py`, `evalloop.py`, `verify.py`)
  **call no model**; the code-fix step between iterations happens **only on your live
  Copilot session**.
- APIA ships **no** second/external/hosted LLM verifier. An optional opt-in
  self-hosted second-opinion reviewer is a deferred future phase, off by default.
  (External/hosted/cross-org **sharing** -- a hosted knowledge service, an MCP server
  exposing APIA, cross-org APIs, collaborative workspaces, marketplace distribution -- is
  **intentionally out of scope**, not deferred; APIA's sharing is strictly LOCAL and
  git-based. See the "Local sharing" section below and `docs/ARCHITECTURE.md` section 7.)

### Layer 1 - repo-derived ground truth

Eval data comes from observable project behavior, in trust order
**characterization > golden > invariant > differential**:

- **characterization** - freeze the current code's deterministic stdout as the baseline;
- **golden** - diff freshly produced artifacts against a committed golden tree;
- **invariant** - built-in structural checks (`row_count_matches_input`, `no_nans`,
  `roundtrip_identity`);
- **differential** - the delta vs. the saved `.apia-baseline.json`.

### Acceptance-criteria confirmation gate (anti-circularity)

For acceptance criteria describing **NEW** behavior the code does not yet exhibit, APIA
asks for **one human confirmation** before trusting them
(`eval_data.acceptance_criteria_gate: true` in `.apia/config.yml`). This stops the same
model from both writing the code and inventing the test it grades itself against.
Criteria that merely pin existing behavior need no confirmation.

### Golden-file setup (Python and C++)

The golden flow treats the artifacts your project produces as the correctness truth:

1. Point the adapter's `golden.artifact_glob` at your **produced-output directory**
   (e.g. `out/**/*.json`), **not** the whole tree, so mining never sweeps generated
   build output. Set `golden.golden_dir` to a committed golden tree and `golden.compare`
   to `bytes` (default, exact), `text` (newline-normalized), or `json` (parsed structure).
2. Commit the known-good copies under `golden_dir`. Keep the produced (non-golden)
   artifacts in a **gitignored** path (e.g. `samples/**/out/`); only the sources and the
   committed golden tree are tracked.
3. The samples show both stacks: Python mines
   `samples/pyapp/tests/golden/{report.json,table.csv}` and C++ mines
   `samples/cppapp/tests/golden/report.json`.

### Running one iterate cycle (headless)

Each `--iterate` call runs **one** deterministic cycle and prints the decision
(`green` / `needs_fix` / `needs_confirmation` / `stop`). Between `needs_fix` cycles you
(the live Copilot session) apply the fix, then re-run:

```sh
# Python
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/python.yml \
    --iterate --max-iterations 3 --emit-failure-report --json

# C++ (CMake + GoogleTest + CTest)
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/cpp.yml \
    --iterate --max-iterations 3 --emit-failure-report --json
```

`--emit-failure-report` prints the compact, model-consumable structured failure to feed
back for the next fix. `--max-iterations N` overrides the config cap for a run (the loop
is **always** bounded by it). The command exits non-zero on any non-green decision so a
preflight can gate on it.

### Iterate-to-Best: green AND the reviewer gate

By default `--iterate` targets **green** (tests pass). Pass `--findings <json>` to turn on
the **reviewer gate** so the loop targets the stronger criterion **green AND the reviewer
gate passes** - i.e. it iterates toward the BEST solution your thresholds define, not merely
a passing test run. `--findings` accepts a raw findings list OR a `scan.py` report (which
has a `findings` key), so you feed it the scanner output directly. Use
`--json-only` to capture a clean JSON file (no human summary) you can redirect
straight into `--findings`:

```sh
# 1) scan the repo and capture the findings report (JSON only, no human text)
python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/python.yml --json-only > scan.json

# 2) iterate against green AND the reviewer gate (same review.thresholds the reviewer uses)
python3 .apia/scripts/verify.py <repo> --adapter .apia/adapters/python.yml \
    --iterate --max-iterations 3 --findings scan.json --emit-failure-report --json
```

An iteration is accepted only when tests/build are green **and** the reviewer gate passes
`review.thresholds`. On a failing gate the EXACT scanner findings
(`{severity, category, file, line, rule, message, source_tool}`) are folded into the SAME
structured failure report under `scanner_findings`, and the `apply_thresholds` block
(`passed / fail_on_severity / failing_severities / failing_counts / reasons`) under
`reviewer_gate`; you hand that back to the live session for the next fix. Without
`--findings` the behavior is the unchanged tests-green path, so existing invocations are
backward compatible.

**Convergence vs. stop-and-ask.** The decision is:

- `green` - tests/build green AND (when findings were supplied) the reviewer gate passes:
  **converged, best reached.** The loop stops; proceed to APPLY (still human-gated).
- `needs_fix` - a verify phase failed OR the reviewer gate failed; fix in the live session
  and re-run the next cycle.
- `needs_confirmation` - the acceptance-criteria gate is open (section above); surface the
  ask and STOP.
- `stop` (`reason: max_iterations_reached`) - the hard cap was hit without fully converging.
  The loop STOPS and asks a **specific** question, carrying the **best-so-far** attempt (the
  iteration with the fewest/lowest-severity residual findings + fewest failing verify
  phases, **not** necessarily the last) and a residual summary grouped by severity
  (e.g. `high=1`). It never loops forever and never silently accepts a sub-threshold result
  as "best".

**On VS 2022 (single agent):** run `/apia-iterate` and follow the Tier-0 protocol - the
fused loop (green + the reviewer gate, best-so-far, bounded stop-and-ask) is fully
expressible via Tier-0 + the Tier-1 prompt files as a single agent, with no dependency on
Tier-2 skills or custom agents, for both Python and C++.

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

## The Reviewer (security / quality / perf / limitations)

APIA can continuously scan a repo for **security**, **quality**, **performance**, and
**limitations** findings. The scanning is done by the **off-model** engine
`.apia/scripts/scan.py`, which runs real **local** scanner tools, normalizes their
output into one canonical finding shape, and applies a composable gate. Copilot only
**summarizes and prioritizes** the grounded findings - it never invents one, and if a
scanner is missing it says so. `scan.py` makes **zero LLM and zero network-to-model
calls**.

### Install the scanner tools

The scanners are ordinary local tools; install the ones for your stack.

- **Python:** `bandit`, `ruff` and/or `flake8`, `radon`, `pip-audit`:

  ```sh
  pip install bandit ruff flake8 radon pip-audit
  ```

- **C++:** `cppcheck` and `clang-tidy` (install via your package manager, e.g.
  `apt-get install cppcheck clang-tidy` or Homebrew). `clang-tidy` wants a compile
  database (`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` in your CMake configure).

A **missing** tool is only a warning: `scan.py` degrades it to a `tool_unavailable`
marker and still returns a valid report. Check presence any time with `--doctor`:

```sh
python3 .apia/scripts/scan.py --doctor
```

### Run the scan

```sh
python3 .apia/scripts/scan.py <repo>
```

With no `--adapter`, `scan.py` **auto-detects the stack** (reusing the ingester's
detection) and resolves the matching adapter from `.apia/config.yml`, and it **excludes
generated/vendored directories** (`build/`, `_deps/`, `.venv`, `node_modules`, ...) so a
CMake `build/` tree never becomes a finding. Useful flags:

```sh
# force a specific adapter (always wins over auto-detection)
python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/cpp.yml

# limit to one finding category
python3 .apia/scripts/scan.py <repo> --category security   # security|quality|perf|all

# emit the machine-readable JSON report (the composable interface)
python3 .apia/scripts/scan.py <repo> --json

# override the gate for this run (fail on anything at this severity or worse)
python3 .apia/scripts/scan.py <repo> --fail-on-severity high
```

The command's exit code reflects the **gate** decision (`0` = gate passed), so a
preflight can gate on it. The gate thresholds live in the `review` block of
`.apia/config.yml`; the standalone review, the greenfield-generation gate, AND the
iterate-to-best loop all read the **same** block.

### Tuning the "best" quality bar

The `review.thresholds` block is the single, tunable definition of "best" that the
standalone reviewer, the generation gate, and the iterate-to-best loop
(`verify.py --iterate --findings ...`) all share - so "best" means the same thing
everywhere. Tune it in `.apia/config.yml`:

```yaml
review:
  thresholds:
    fail_on_severity: high      # any finding at this severity or MORE severe fails the gate
    max_findings:
      security: 0               # per-category caps (security/quality/perf) + optional total

eval_data:
  iterate_to_best:
    best_so_far_tie_break: fewest_findings   # fewest_findings | latest | earliest
```

- Raise or lower `fail_on_severity` to move the bar (e.g. `critical` is more permissive than
  `high`; `medium` is stricter). Add or tighten `max_findings` per-category caps.
- `eval_data.iterate_to_best.best_so_far_tie_break` is the only iterate-to-best-specific
  knob: when two attempts rank EQUALLY on residual severity + failing-phase count, it picks
  `fewest_findings` (default), the `latest`, or the `earliest`.
- The loop stays bounded by the HARD `max_iterations` cap regardless of the thresholds.

Honest framing: "best" means **meets these configured deterministic thresholds (green tests
+ the reviewer gate), or the closest bounded attempt with an honest residual report** - it
is **not** a guarantee of globally optimal or provably perfect code. The ceiling is still
the Copilot model, the quality of your tests, and what the scanners can detect.

### Through Copilot

Run the Tier-1 slash-command `/apia-review` (or invoke the Tier-2 scan skills in the VS
Code Agent Host). The read-only `apia-reviewer` agent runs `scan.py` for both **Python**
and **C++**, reads the structured report, and presents a prioritized summary at a
human-approval gate.

**On Visual Studio 2022:** VS 2022 honors **only Tier-0 + Tier-1 prompt files**, not
Tier-2 skills/agents. The reviewer therefore runs as a **single Tier-0/Tier-1 agent**
driving `scan.py` via `/apia-review`; the Tier-2 scan skills and the `apia-reviewer`
custom agent are not honored there.

---

## Greenfield generation (new Python / C++ project)

APIA can generate a **working, tested, runnable** Python **or** C++ project from a
plain-language prompt, through the same human-gated discipline as every other stage.
The off-model engine `.apia/scripts/scaffold.py` does the deterministic work (detect
the project **shape**, emit the skeleton + build file, normalize build/run/test, decide
the gate) and your Copilot session writes the feature code. `scaffold.py` makes **zero
LLM and zero network-to-model calls**.

### The two-tier honest model

- **Tier 1 - generic shape generation:** the skeleton + build file + a runnable
  entrypoint + a smoke test, emitted deterministically for the detected shape.
- **Tier 2 - verified "it actually runs":** the generated project is **built, run, and
  unit-tested** end to end. The **five fully-verifiable shapes** (`python_cli`,
  `python_library`, `python_web_api`, `cpp_console`, `cpp_library`) must go **all-green**.
  Shapes that inherently cannot be auto-verified in a headless environment
  (`gui`/`embedded`/`driver`) are generated + build-checked + unit-tested and reported
  **honest-partial** - APIA plainly tells you "I can't fully auto-verify this shape; here
  is what I verified and what needs your manual check" and writes a `PARTIAL.md`. **A
  faked green is impossible.**

### The batched-clarify flow

Generation asks its clarifying questions **once, upfront** (one batched round), not
drip-fed:

```
your prompt -> ONE batched clarify (stack, shape, name, key features, acceptance criteria)
            -> you approve the spec -> APIA generates skeleton + core features, then
               builds/runs/tests -> human checkpoints (acceptance criteria, apply)
```

This is **minimal, well-placed human input - not zero interaction**. APIA never claims
one sentence becomes flawless production code.

### The acceptance-criteria confirmation checkpoint

Acceptance criteria that describe **NEW** behavior the generated code does not yet
exhibit must be **confirmed by you** before APIA grades the generation against them
(the anti-circularity gate, `eval_data.acceptance_criteria_gate: true`). This stops the
same model from both writing the code and inventing the test it grades itself against.

### Generate a Python project (end to end)

```sh
# 1) detect the shape from an approved spec (JSON or YAML)
python3 .apia/scripts/scaffold.py --detect-shape spec.json

# 2) emit the deterministic skeleton for the detected shape
python3 .apia/scripts/scaffold.py --emit spec.json --dest my_project

#    (Copilot now writes the core feature code + feature tests into my_project)

# 3) build + run + unit-test the generated project and normalize the result
python3 .apia/scripts/scaffold.py --verify my_project --shape python_cli \
    --adapter .apia/adapters/python.yml --json

# 4) run the generation gate (same reviewer thresholds a human change passes)
python3 .apia/scripts/scan.py my_project --adapter .apia/adapters/python.yml --json
python3 .apia/scripts/scaffold.py --gate findings.json
```

`--verify` exits 0 only when the shape is fully verified green; the gate exits 0 only
when the reviewer thresholds pass. The committed proof projects under
`samples/generated/` show each shape (see `docs/PROOF.md`).

### Generate a C++ project (end to end)

C++ uses the same commands with the `cpp.yml` adapter; the CMake template fetches and
builds GoogleTest via FetchContent (mirroring `samples/cppapp`), so the generated
project is self-contained:

```sh
python3 .apia/scripts/scaffold.py --emit spec.json --dest my_cpp --adapter .apia/adapters/cpp.yml
python3 .apia/scripts/scaffold.py --verify my_cpp --shape cpp_console \
    --adapter .apia/adapters/cpp.yml --json
```

### Which deps / tools to install

- **Python web-API shape (`python_web_api`):** a lightweight web framework. The sample
  uses **Flask** (`pip install flask`); the generated smoke test hits a real endpoint via
  Flask's in-process test client (no live socket). If you cannot install a framework, the
  skeleton still ships a framework-agnostic `health()` handler you can unit-test.
- **C++ shapes (`cpp_console` / `cpp_library`):** **cmake**, a C++ compiler (**g++** /
  MSVC), and **ctest** on `PATH`. GoogleTest is fetched by CMake; nothing else to install.
- Preflight any time with `python3 .apia/scripts/scaffold.py --doctor`.

### Through Copilot

Run the Tier-1 slash-command **`/apia-new`** (or, in the VS Code Agent Host, let the
`apia-architect` agent drive the `apia-scaffold` + `apia-e2e-verify` skills). APIA runs
the batched clarify, generates, verifies, and presents results at a human-approval gate.

**On Visual Studio 2022:** VS 2022 honors **only Tier-0 + Tier-1 prompt files**, not
Tier-2 skills/agents, so generation runs as a **single Tier-0/Tier-1 agent** via
`/apia-new` driving `scaffold.py` for both Python and C++ - the `apia-architect` agent
and the Tier-2 generation skills are not honored there. For a C++ project, run from the
**VS 2022 Developer Command Prompt** so cmake/ctest/the compiler are on `PATH`.

---

## The gated loop in practice

The five stages each pause at a **human-approval gate**:

1. **ingest** — confirm the learned knowledge summary.
2. **clarify** — answer the numbered clarifying questions APIA raises when its
   confidence is below `confidence_threshold`.
3. **modify** — approve the proposed diff.
4. **test** — review the regression results and the new test results.
   For legacy code with no tests, APIA writes **characterization tests first**
   to pin current behavior before changing anything. A change can **iterate to
   green** against Layer-1 eval data under the hard `max_iterations` cap
   (`/apia-iterate`); the verifier is deterministic tooling, never a second model.
5. **apply** — confirm the commit.

---

## Local sharing (knowledge packs / profiles / plugins / manifest)

Phase 6a lets a team share knowledge and capabilities the way `.apia/knowledge/` is
already shared: **through the git repo**. Everything is a committed file, pulled like any
other. There is **no server, no network, no download, no marketplace** -- the off-model
engine `.apia/scripts/share.py` only reads/writes LOCAL files and makes **zero LLM/network
calls**. One mutually-exclusive action per run, plus shared options
(`--repo` / `--knowledge-dir` / `--out` / `--pack` / `--profile` / `--force` / `--json`).

### Export / import a knowledge pack (safe no-clobber + `--force`)

```sh
# Export the 8 committed knowledge artifacts (incl. project-facts.md) + the index
# into one versioned pack (schema_version "1").
.venv/bin/python .apia/scripts/share.py --export-pack --repo . \
    --knowledge-dir .apia/knowledge --out team-pack.json

# Import into another checkout's knowledge dir. SAFE by default: a byte-identical or
# missing artifact is written; a DIVERGENT local artifact is a local correction -- it is
# KEPT and reported under conflicts/skipped_local, never silently overwritten.
.venv/bin/python .apia/scripts/share.py --import-pack --pack team-pack.json \
    --knowledge-dir .apia/knowledge --json

# Only when you deliberately want the pack copy to win over a local correction:
.venv/bin/python .apia/scripts/share.py --import-pack --pack team-pack.json \
    --knowledge-dir .apia/knowledge --force --json     # the overrides land in "forced"
```

A pack with an unknown/newer `schema_version` is **reported** (`unknown_version: true`)
and **not applied**, so a newer layout can never corrupt an older install.

### Apply an org/team profile (LOCAL overrides win)

A committable profile uses the **same keys as `.apia/config.yml`**. Copy the shipped
example to activate it, then apply it:

```sh
cp .apia/profiles/example-team.yml .apia/profile.yml   # activate (REPO-FIRST resolution)

.venv/bin/python .apia/scripts/share.py --apply-profile \
    --profile .apia/profile.yml --repo . --json
```

The profile is deep-merged **UNDER** your local config: a key your `.apia/config.yml` sets
**wins**; a key it leaves unset is **filled in** from the profile. The JSON output's
`provenance` map shows which keys came from `local` vs `profile`.

### Add a LOCAL plugin (adapter / scanner / shape)

Commit a plugin file under `.apia/plugins/` and it is discovered automatically (REPO-FIRST,
then the package). Plugins are strictly **additive** -- an absent dir changes nothing -- and
a **malformed** plugin is reported in `errors` and skipped, never a crash:

```
.apia/plugins/
  adapters/<stack>.yml   # a new/overriding adapter (same schema as .apia/adapters/*.yml; `name` required)
  scanners/<name>.yml    # {stack, category, scanner:{tool,command,format}} extending an adapter's scanners:
  shapes/<name>.yml      # {shape, ...} naming a new scaffold shape id
```

```sh
# See what was discovered + loaded (and any malformed-plugin errors):
.venv/bin/python .apia/scripts/share.py --plugins --repo . --json
```

The engines then consume it: `ingest` sees a plugin adapter during stack detection, `scan`
enumerates a plugin scanner/adapter, `scaffold` lists a plugin shape, and `verify` resolves
a plugin adapter by name. See `.apia/plugins/README.md` and the plugin contract in
`.apia/adapters/README.md`. Committed `example-*` plugins demonstrate each kind.

### Query capabilities (manifest / plugins / doctor)

```sh
# Full machine-readable capability manifest for THIS checkout:
.venv/bin/python .apia/scripts/share.py --manifest --repo . --json
# -> adapters complete-vs-stub (python/cpp complete; csharp/groovy stub), available
#    scanners, supported_shapes (incl. plugin shapes), loaded plugins + errors,
#    profile provenance, and versions {apia_version "6a", schema_version "1"}.

.venv/bin/python .apia/scripts/share.py --plugins --repo .    # just the plugin catalog
.venv/bin/python .apia/scripts/share.py --doctor  --repo .    # offline toolchain preflight
```

## Exercising every APIA feature (VS Code and Visual Studio 2022)

APIA degrades gracefully by host (see `docs/PORTABILITY.md`). **VS Code (Agent Host)**
loads all tiers: Tier-0 `.github/copilot-instructions.md`, the **Tier-1 prompts**
(`/apia-*`), the **Tier-2 skills** (rich, token-efficient, forked for heavy work), and the
**per-stage agents** with approval-gate handoffs. **Visual Studio 2022** loads **only
Tier-0 + the Tier-1 prompts** (no skills, no agents), so each flow runs as a **single
agent** driven by the named prompt. The off-model `.apia/scripts/*.py` engines are
identical on both hosts; only the orchestration surface differs.

| Flow | VS Code (Tier-0 + prompt + skill + agents) | Visual Studio 2022 (Tier-0 + Tier-1 prompt only) |
|---|---|---|
| Ingest / clarify / modify / test / apply | `/apia-ingest`, `/apia-clarify`, `/apia-feature`, `/apia-test`, `/apia-apply`; the `apia-ingest` / `apia-test-authoring` skills + per-stage agents (`apia-planner`/`apia-implementer`/`apia-tester`) hand off at each gate | the same prompts driven by one agent via Tier-0 |
| Feature loop (iterate-to-green) | `/apia-iterate` + the `apia-feature-loop` skill | `/apia-iterate` (single agent) |
| C++ | same loop; adapter auto-detected; GUI/embedded/driver reported honest-partial | same prompts; launch from the **VS 2022 Developer Command Prompt** so MSBuild/`cl` are on PATH |
| Eval / iterate-to-best (green AND reviewer gate) | `/apia-iterate` with the reviewer gate; `apia-feature-loop` skill | `/apia-iterate` (single agent) |
| Reviewer (security/quality/perf/limitations) | `/apia-review` + the `apia-security-scan` / `apia-quality-scan` / `apia-perf-scan` / `apia-limitations` skills + the `apia-reviewer` agent | `/apia-review` (single agent) |
| Greenfield generation | `/apia-new` + `apia-architect` / `apia-scaffold` / `apia-e2e-verify` agents | `/apia-new` (single agent) |
| Orchestrator (multi-task task graph) | `/apia-run` + the `apia-orchestrate` skill + `apia-orchestrator` agent | `/apia-run` (single agent) |
| Iterate-to-best | folded into the loops above via `evalloop.evaluate_quality_gated` (no separate prompt) | same, via the single agent |
| **Local sharing (packs/profiles/plugins/manifest)** | `/apia-share` + the `apia-share` skill | `/apia-share` (single agent) |

On **either** host the engines can also be driven directly from a terminal with the
`.venv/bin/python .apia/scripts/*.py ...` commands shown throughout this guide (ingest,
`verify.py --iterate`, `scan.py`, `scaffold.py`, `orchestrate.py`, and `share.py`); the
prompts/skills simply wrap those same off-model engines for the agent. Nothing is ever
auto-pushed on either host -- APIA stops at a local commit and the human pushes manually.

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
