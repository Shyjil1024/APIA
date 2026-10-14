# Autonomous Project Intelligence Agent (APIA)

> A portable GitHub Copilot **skill package** that learns any project (legacy or greenfield, any
> stack) and drives changes semi-autonomously through an **ingest -> clarify -> modify -> test ->
> apply** loop, riding entirely on the user's existing Copilot session to conserve tokens/credits.

---

## 1. Vision and premise

Every project at the company already has GitHub Copilot. Copilot credits expire fast, so instead of
paying for a separate LLM backend we ship a **downloadable skill** (analogous to the "caveman" skill
on Claude) that the user commits into their repository. Once installed, the skill teaches Copilot how
to understand and safely change *that specific project*, with a human approving every major step.

The agent must:

- Learn the whole project: codebase, architecture, pipelines, dependencies, business workflows.
  Handle **legacy** code, not just greenfield.
- On a feature/change request: analyze -> modify -> verify/test -> apply, **semi-autonomously**.
- Gate on **human approval at every main event**.
- Verify against the project's **existing** tests (regression guard) **and** generate **new** tests
  for the change. Handle legacy projects with **no tests** (characterization tests first).
- **Minimize token/credit usage** while maximizing accuracy. This is a first-class design driver.
- When uncertain, **ask the user** (anti-hallucination clarifying-question gate) rather than guess.
- Fetch external **data** (not model inference) via MCP servers / APIs routed through Copilot tools.
- Even greenfield: generate what the user asked for, minimal tokens, best accuracy.

Primary stacks (first-class support priority): **Python, C++, C#, Groovy**. The design is
language-agnostic; these stacks ship with ready-made adapters first. **Python and C++ are
supported today** (C++ via **CMake + MSBuild**, with GoogleTest/Catch2/CTest and
VSTest); C#/Groovy remain adapter stubs (DEFERRED to Phase 2).

APIA follows a **DETECT-FIRST / ASK-ONLY-ON-GENUINE-DOUBT / ASK-SPECIFICALLY** discipline:
it infers the build system and test framework from real project evidence and only asks when
confidence falls below `confidence_threshold` (0.75), and then asks ONE specific, informed
question (see section 6). It stays **strictly local**: it never auto-pushes, opens PRs/MRs,
or runs CI/CD/packaging/signing - the user pushes manually.

### Hard architectural constraints (confirmed with the user)

1. **Delivery format**: a GitHub Copilot customization/skill package, **portable across any IDE that
   hosts Copilot**, installed by committing it into the user's repo.
2. **Model backend**: runs **entirely on the user's existing Copilot session**. It must **never**
   call a separate/external LLM API of its own. External *data* may be fetched via MCP/API through
   Copilot's tool-use.
3. **Autonomy**: **semi-autonomous** with a human-approval gate at every major event.
4. **Validation**: run existing tests as a regression guard **and** generate new tests; handle the
   no-tests legacy case with characterization-test generation before modifying.
5. **Cross-cutting first-class goals**: (a) aggressive token/credit efficiency; (b) anti-hallucination
   clarifying-question gate.

---

## 2. Portability: how Copilot customization actually works

Copilot's customization surface evolves and differs per IDE. The portability strategy below is based
on the current (verified) mechanisms. **Re-verify per release** - this is captured as a Phase-1 task.

### Verified mechanism matrix (as of this writing)

| Mechanism | File / location | VS Code | Visual Studio | JetBrains | GitHub.com / cloud agent / CLI | Notes |
|---|---|---|---|---|---|---|
| Repo-wide custom instructions | `.github/copilot-instructions.md` | Yes | Yes | Yes | Yes | **Universal baseline.** Always-on, sent with requests. |
| Path-specific instructions | `.github/instructions/*.instructions.md` (`applyTo` glob) | Yes | Yes | **No** | Yes | Not honored by JetBrains. |
| Agent instructions | `AGENTS.md` (nearest in tree wins) | Yes (Agent Host) | - | - | Yes | Cross-agent format. |
| Prompt files | `*.prompt.md` (`.github/prompts/`) | Yes (Local; **deprecated in Agent Host**) | Yes | Yes | - | Being migrated to Agent Skills in VS Code Agent Host. |
| **Agent Skills** | `SKILL.md` in `.github/skills/`, `.claude/skills/`, `.agents/skills/` | Yes | - | **No** | Copilot CLI, app, cloud agent, Codex | **Open standard** (agentskills.io). Not honored by JetBrains (see PORTABILITY caveats). Progressive 3-stage disclosure = token-efficient. `context: fork` runs in a subagent. |
| Custom agents | `*.agent.md` in `.github/agents/` | Yes | - | - | Org-level | Per-role tools/model, `handoffs`, `mcp-servers`. |

### Portability principle: layered graceful degradation

The package is authored once but **degrades gracefully** by host capability:

- **Tier 0 - Universal (every IDE + GitHub.com):** `.github/copilot-instructions.md` carries the
  core operating protocol (the loop, the approval gates, the clarifying-question discipline, the
  token budget, the stack preferences). This alone makes the agent work everywhere Copilot runs.
- **Tier 1 - Portable prompt entrypoints (VS Code, Visual Studio, JetBrains):** `*.prompt.md`
  slash-commands (`/apia-ingest`, `/apia-feature`, ...) give users explicit, low-token entrypoints
  into each stage of the loop. These are the manual invocation surface where Agent Skills are not
  yet honored.
- **Tier 2 - Rich skill package (VS Code Agent Host + skills-compatible agents):** `SKILL.md`
  skills with bundled scripts/templates and `context: fork` for heavy, token-isolated stages
  (ingestion, test-generation). Custom agents (`*.agent.md`) wire per-stage tool scopes and
  `handoffs` that implement the approval gates as explicit UI buttons.

**Phase-1 primary host = VS Code** (richest surface: skills + custom agents + handoffs). The same
`.github/copilot-instructions.md` + prompt files guarantee the loop still runs in JetBrains/VS, just
without the forked-context and handoff niceties.

> Because `copilot-instructions.md` is sent on **every** request, it must stay short and point to the
> heavier skills/prompt files that load **on demand**. This is the token-efficiency keystone.

---

## 3. The core loop: ingest -> clarify -> modify -> test -> apply

Each arrow is a **human-approval gate**. The agent stops, presents a compact summary, and waits.

```
                 +------------------- clarifying questions (any stage) -------------------+
                 |                                                                        v
[user request] --> (1) INGEST ==GATE== (2) CLARIFY ==GATE== (3) MODIFY ==GATE== (4) TEST ==GATE== (5) APPLY
                       |                     |                   |                 |              |
                 project knowledge      resolve all        proposed diff      regression +     commit/PR
                 (cached, incremental)  uncertainty        (not yet written)  new tests green  (user confirms)
```

### Stage 1 - INGEST (learn the project, cheaply)

Goal: build a **persistent, cached project knowledge base** so Copilot never has to re-read the whole
repo. Token efficiency lives here.

- **Incremental index, not full-context dump.** Walk the repo once, produce a compact
  `.apia/knowledge/` set of Markdown artifacts: `project-map.md` (tree + module responsibilities),
  `architecture.md`, `dependencies.md`, `pipelines.md` (CI/CD, build), `workflows.md` (business
  flows), `test-inventory.md`, `stack.md` (languages/frameworks detected).
- **Retrieval over re-reading.** For any later task, the agent loads only the relevant knowledge
  slice + the specific files it needs (via `search/codebase`, `search/usages`), never the whole repo.
- **Caching + invalidation.** Each artifact records the file hashes / paths it was derived from.
  Re-ingest only touches changed paths (git diff since last ingest). Stored under `.apia/` and
  committed so knowledge is shared across the team and survives sessions.
- **Deterministic preprocessing off-model.** Bundled scripts (ripgrep/tree walks, dependency-manifest
  parsing, test discovery) do the mechanical heavy lifting **without** spending Copilot tokens; the
  model only summarizes/structures the distilled output. This is the single biggest credit saver.
- **Legacy-aware.** No assumption of docs or tests. Detect stack from manifests
  (`requirements.txt`/`pyproject.toml`, `CMakeLists.txt`/`conanfile`, `*.csproj`/`*.sln`,
  `build.gradle`/Jenkinsfile Groovy) and from file extensions.
- **Detect-first with persisted facts.** The ingester records the detected build system and
  test framework (CMake vs MSBuild; GoogleTest/Catch2/CTest for C++) in a `project-facts.md`
  artifact with a **confidence score** against `confidence_threshold`. Below threshold it emits
  a **precomposed, specific clarifying question** (what was found, what was inferred, the
  uncertainty, a best guess) instead of guessing. Confirmed facts persist so they are not
  re-asked.
- Runs in a **forked context** (`context: fork`) so the large ingestion reasoning never pollutes the
  main conversation; only the distilled knowledge returns.

GATE: present the knowledge summary; user confirms it is accurate (and corrects misunderstandings -
corrections are folded back into `.apia/knowledge/`, themselves an anti-hallucination mechanism).

### Stage 2 - CLARIFY (anti-hallucination gate)

- The agent restates the request, lists **assumptions**, and asks a **numbered list of clarifying
  questions** for every point of uncertainty (ambiguous scope, missing acceptance criteria, unknown
  external contracts, conflicting conventions).
- **Hard rule: if confidence is below threshold, ask - never guess.** Encoded in
  `copilot-instructions.md` so it holds even in Tier-0 hosts.
- External data needed to answer (e.g. an API schema, a library's docs) is fetched via **MCP/API
  through Copilot tools**, not invented.
- Output: an agreed **change spec** (what, where, acceptance criteria, out-of-scope).

GATE: user answers questions / approves the change spec.

### Stage 3 - MODIFY (propose, do not yet write)

- Produce a **plan + proposed diff** grounded in the knowledge base and the change spec: files to
  touch, functions to add/alter, following detected project conventions.
- Nothing is written to disk yet; the diff is presented for review.

GATE: user approves the proposed diff (or requests changes -> loop back).

### Stage 4 - TEST (regression guard + new verification)

- **Regression guard:** run the project's **existing** tests first to capture the current green
  baseline. If they are already red, surface that before proceeding.
- **Legacy / no-tests case:** if no (or inadequate) tests cover the touched code, **generate
  characterization tests** that pin current behavior **before** modifying, so regressions are
  detectable.
- **Build-only compile check:** for C++ (and any project), the harness can run the adapter's
  `build_command` first (`verify.py --build-only`) as a fast compile-level regression signal -
  the only one available when the project has no tests.
- **New tests:** generate tests/verifications for the new behavior per the change spec. Stack-aware
  via adapters (pytest; GoogleTest/Catch2/CTest, VSTest for C++; xUnit/NUnit via `dotnet test`;
  Spock/JUnit via Gradle for Groovy). The off-model harness parses each adapter's declared
  `result_format` (pytest/ctest/gtest/msbuild) into one uniform pass/fail counts shape.
- Apply the change in a scratch/working state, run existing + new tests, confirm **old behavior still
  passes and new behavior is verified**. Report pass/fail compactly.

GATE: user reviews the test results.

### Cross-stack capabilities (implemented)

Four worth-adding features ship across stacks and are proven on both Python and C++:

1. **Doctor / preflight toolchain check** - `ingest.py --doctor` / `verify.py --doctor` report
   tool reachability + versions (cmake/ctest/compilers) and clearly warn when a required tool
   or the VS 2022 Developer Command Prompt is missing, so a run fails with an actionable message
   instead of a confusing subprocess error.
2. **Persisted project-facts / detection memory** - detected (and user-confirmed) build system,
   test framework, confidence, and evidence persist to `.apia/knowledge/project-facts.md`, so
   facts are not re-asked.
3. **Dry-run / explain-the-plan** - `verify.py --dry-run` resolves and prints the build/test
   commands that WOULD run plus the detection reasoning, writing nothing and spawning no
   build/test subprocess.
4. **Build-only validation phase** - `verify.py --build-only` compiles without running tests, a
   fast regression signal (and the only one when a project has no tests).

> **Visual Studio 2022 honors only Tier-0 (`.github/copilot-instructions.md`) + Tier-1 prompt
> files, NOT Tier-2 skills/agents.** The C++ loop must therefore run fully via the Tier-0 protocol
> as a single agent (see `docs/PORTABILITY.md`).

### Stage 5 - APPLY

- Write the approved changes, run the full relevant test suite once more, and prepare the commit
  (and/or PR). The agent **does not auto-push**; the user confirms the final apply/commit.

GATE: user confirms apply/commit.

Any stage can bounce back to CLARIFY if new uncertainty appears.

---

## 3a. Layer 1 - grounded eval-data generation

The TEST stage must grade a change against **observable project behavior**, never against
the model's own restatement of the request. Layer 1 is the off-model machinery
(`.apia/scripts/evaldata.py`) that assembles that ground truth. It is **language-neutral**
and **calls no model**: it only runs the adapter's local commands and compares bytes/text/
structure.

**Trustworthiness order** (`eval_data.trust_order` in `.apia/config.yml`), from MOST to
LEAST trustworthy; the loop prefers a higher-trust signal when more than one is available:

```
characterization > golden > invariant > differential
```

1. **characterization** - `harvest_characterization` runs the adapter's
   `characterization.capture_command`, freezes the deterministic stdout, and hashes it.
   Most trustworthy: it is the real output of the real code.
2. **golden** - `compare_artifact` + `mine_golden` implement a **first-class,
   language-neutral golden-file comparison**: freshly produced artifacts are diffed against
   a committed golden tree (`golden.golden_dir`). `compare` is `bytes` (the trust default,
   exact match), `text` (newline-normalized), or `json` (parsed structure). The
   produced (non-golden) artifacts live in a gitignored output path; only the sources and
   the committed golden tree are tracked, and `golden.artifact_glob` is scoped to the
   produced-output directory so mining never sweeps generated build output.
3. **invariant** - `run_invariants` checks built-in structural properties that must always
   hold: `row_count_matches_input`, `no_nans`, `roundtrip_identity` (unknown ids ignored).
4. **differential** - `differential_artifacts` is the artifact old-vs-new delta; the
   test-COUNTS differential remains in `verify.compare_to_baseline` (the saved
   `.apia-baseline.json`). `assemble_eval_data` ties these into one eval-data bundle.

Adapters declare the optional `golden` / `characterization` / `invariants` sections (see
`.apia/adapters/README.md`); a stack without them simply falls back to the differential
delta.

## 3b. Layer 2 - iterative self-correction loop

Layer 2 (`.apia/scripts/evalloop.py`) turns a single verify pass into a **bounded
iterate-to-green loop** that still gates on the human. Like Layer 1 it **calls no model**;
it assembles the structured failure a model will consume and decides green/stop/ask.

- `run_iteration_cycle` runs one deterministic verifier pass.
- `evaluate(repo, adapter, options, state=LoopState())` enforces the **HARD
  `max_iterations` cap** from `.apia/config.yml` and returns one of: `green`,
  `needs_fix`, `stop` (`reason: max_iterations_reached`, with a specific ask), or
  `needs_confirmation` (the acceptance-criteria gate, see 3c).
- `build_failure_report` emits a compact, JSON-serializable, model-consumable failure
  report and **fails loud** - when the cap is reached without going green the loop surfaces
  the last structured failure and STOPS rather than silently continuing or applying a
  broken change.
- The per-iteration `token_budget.iterate` is an **advisory** ceiling (a warning, not a
  hard stop); the hard stop is `max_iterations`. Iteration exists to converge quickly on
  grounded eval data and AVOID wasted tokens, not to grind indefinitely.
- `verify.py` exposes the loop headlessly: `--iterate`, `--max-iterations N`,
  `--emit-failure-report`. One `--iterate` call runs ONE deterministic cycle; the model
  fix between cycles is a **LIVE Copilot action, never performed by the scripts**.

**Single-agent / VS 2022.** The whole loop is expressible via the Tier-0 protocol run as a
single agent (see `docs/PORTABILITY.md`), so it runs on Visual Studio 2022, which honors
only Tier-0 + Tier-1 prompt files (`/apia-iterate`), not Tier-2 skills/agents.

### Iterate-to-Best (Phase 5): green AND the reviewer gate

Phase 5 fuses this iterate loop with the Phase-2 reviewer (section 3d) so iteration no
longer stops at merely "green" (tests pass) but continues toward the **BEST** solution the
configured thresholds define. The acceptance criterion for each iteration becomes BOTH:

1. the deterministic test/build verification already in place (regression guard + new
   tests + build-only + the Layer-1 eval data), AND
2. the **reviewer gate** - the `scan.py` findings evaluated via
   `scan.apply_thresholds` against the `review.thresholds` block in `.apia/config.yml`
   (security/quality/perf severities + per-category caps, section 3d).

An iteration is "good enough / best reached" **only** when tests are green AND the reviewer
gate passes the configured thresholds. A gate failure counts as a **real** failure
(fail-loud): a green-verify + failing-gate iteration still yields a report and keeps the
loop running.

**The ONE shared fused decision.** The fusion is a single function,
`evalloop.evaluate_quality_gated(repo_path, adapter, options=None, state=None)`. Its
statuses are `green` (converged: tests/build green AND Layer-1 green AND the reviewer gate
passes), `needs_fix`, `stop` (`reason: max_iterations_reached`), and `needs_confirmation`
(the anti-circularity gate of section 3c). `evaluate` is now a thin delegate to it and
stays backward compatible: when no `options['findings']` are supplied the reviewer gate is
not in play and the decision degrades to today's tests-green path.

**Three callers, one definition of "best."** So "best" means the same thing everywhere, all
three entry points delegate to `evaluate_quality_gated`:

- **`verify.py --iterate`** - the standalone loop. It now accepts `--findings <json>` (a raw
  findings list OR a `scan.py` report with a `findings` key) to turn the reviewer gate on;
  without `--findings` the behavior is unchanged tests-green. The human renderer prints the
  reviewer-gate line, the best-so-far attempt, and the residual summary.
- **`scaffold.decide_generation`** (section 3e) - maps the fused status back to its own
  contract `green | iterate | stop | ask`; an honest-partial generation can **never** be
  `green`. It passes a precomputed `run_iteration_cycle` result via `options['cycle']` so it
  delegates WITHOUT re-running the generation verify it already performed.
- **`orchestrate.decide_task`** (section 3f) - findings thread through
  `inputs['options']['findings']` into `evaluate`; the verdict is recorded **verbatim** (no
  re-decision), so an orchestrated task gets no weaker a bar than a human-authored change.

**Scanner findings fold into the EXISTING failure report.** On a failing reviewer gate the
EXACT normalized scanner findings (the `scan.make_finding` shape `{severity, category,
file, line, rule, message, source_tool}`) are folded into evalloop's EXISTING structured
failure report under `report['scanner_findings']`, and the exact `scan.apply_thresholds`
block (`passed / fail_on_severity / failing_severities / failing_counts / reasons`) under
`report['reviewer_gate']`. All pre-existing report keys are preserved and the report stays
JSON-serializable. APIA does not invent a parallel report; it extends `build_failure_report`
with two optional params (`scan_gate` + `findings`).

**Best-so-far + bounded convergence / stop / ask.** `score_iteration` ranks an iteration by
its residual severity (weighted) plus the failing verify-phase count (LOWER is better);
`LoopState` carries a `best_so_far` field tracked across iterations. On hitting the HARD
`max_iterations` cap the decision STOPS and asks, carrying the **BEST** iteration reached
(not necessarily the last) and a specific residual summary from `_residual_summary`, which
groups residuals by SEVERITY (e.g. `high=1`). The loop never loops forever and never
silently accepts a sub-threshold result as if it were best. The tie-break between two
attempts that rank EQUALLY is the knob `eval_data.iterate_to_best.best_so_far_tie_break`
(`fewest_findings` | `latest` | `earliest`).

**The configurable quality bar.** The tunable definition of "best" IS the
`review.thresholds` block (`fail_on_severity: high`, `max_findings: {security: 0}`) - the
SAME block the standalone reviewer and the generation gate already read. The only
Phase-5-specific knob is `eval_data.iterate_to_best.best_so_far_tie_break`; both live under
EXISTING config structures (no new top-level block). `config.yml` carries explanatory
comments for these.

**No external model; the fix is a LIVE Copilot action.** The off-model scripts assemble the
eval + scan results, apply the thresholds, decide converged / continue / stop, emit the
structured failure report, and enforce the cap - they NEVER call a model. The per-iteration
code fix is a LIVE Copilot action on the user's session (section 3c). Model temperature is
not settable; "best" is enforced by deterministic thresholds + grounded findings, not a
temperature setting. The whole fused loop is still expressible via Tier-0 as a single agent,
so it runs on VS 2022 (`/apia-iterate`).

**Honest framing (do not overclaim).** "best" means "meets the user-configured deterministic
thresholds (green tests + reviewer gate), OR the closest bounded attempt with an honest
residual report" - **NOT** a guarantee of globally optimal or provably perfect code. The
ceiling is still the underlying Copilot model, the quality of the tests, and what the
scanners can detect.

## 3c. Anti-circularity / acceptance-criteria safeguard

A model that both **writes the code** and **invents the test** could "pass" by grading
itself against its own guess of the requirement. The safeguard: when
`eval_data.acceptance_criteria_gate` is true, acceptance criteria that describe **NEW**
behavior the code does not yet exhibit must be **human-confirmed** before the loop is
allowed to treat them as truth. `propose_acceptance_criteria` surfaces them;
`acceptance_criteria_confirmed` is the gate the loop checks before trusting new-behavior
criteria (an unresolved gate returns `needs_confirmation`). Criteria that merely pin
EXISTING behavior (characterization/golden/invariant) need no confirmation because they are
already grounded in the real code.

**The model-fix step is live-session-only.** The deterministic scripts (`evaldata.py`,
`evalloop.py`, `verify.py`, `ingest.py`) **never call a model**; they build eval data, run
the verifier, emit a structured failure, enforce the cap/budget, and decide green/stop/ask.
The code generation and fixes happen only on the user's live Copilot session.

**No second/external LLM verifier - deliberately not built.** APIA does **not** ship a
second, external, open-source, or hosted LLM to act as a verifier or reviewer. The verifier
is **deterministic tooling** (tests, build, lint, diff, golden-file compare), because a
second model would (a) reintroduce the circularity this safeguard removes - one model's
guess cannot validate another's - (b) require a separate LLM backend, violating the hard
"runs entirely on the user's Copilot session, never a separate LLM API" constraint, and (c)
cost tokens/credits with no deterministic guarantee. An **optional, opt-in self-hosted
second-opinion reviewer** is explicitly **DEFERRED to a future phase** (see section 7) and
would remain opt-in and off by default.

---

## 3d. The Reviewer (Phase 2) and its composable-gate interface

The **Reviewer** adds continuous **security / quality / performance / limitations**
scanning to APIA. Like the TEST layers it splits cleanly into an **off-model scanning
engine** and a **model-facing summarize/prioritize step**, and it reuses the exact
anti-circularity framing of section 3c: the scanners are **deterministic LOCAL tools,
never a second model**.

### Off-model engine: `.apia/scripts/scan.py`

`scan.py` is the scanning counterpart to `verify.py`. Where `verify.py` normalizes
per-stack TEST output via a `result_format -> parser` dispatch, `scan.py` normalizes
per-stack SCANNER output via a `source_tool -> normalizer` dispatch. It:

- **Runs real LOCAL scanner subprocesses** declared in each stack adapter's
  `scanners:` section (bandit / pip-audit / ruff / flake8 / radon for Python;
  cppcheck / clang-tidy for C++) through the single `_run` chokepoint, with the same
  `python -m <mod>` interpreter rewrite `verify.py` uses so the Python scanners run in
  the project's `.venv`.
- **Normalizes heterogeneous output into ONE canonical finding shape**
  `{severity, category, file, line, rule, message, source_tool}`, where `severity` is
  the ordered vocabulary `critical > high > medium > low > info` and `category` is
  `security | quality | perf` (`limitations` is reserved for the model-facing
  synthesis stage). The dispatch mirrors `verify.py`'s `result_format -> parser` table.
- **Auto-detects the stack** when no `--adapter` is given, reusing
  `ingest.detect_stacks()` / `walk_repo()` and resolving the matching adapter from
  `config.yml` `stacks[].adapter` (an explicit `--adapter` always wins); the chosen
  adapter and its provenance are reported as `adapter_source`.
- **Excludes generated / vendored directories** (`build/`, `_deps/`, `.venv`,
  `node_modules`, `__pycache__`, ...) so a CMake `build/` tree that vendors
  GoogleTest's own files never becomes a finding - the report is grounded in the
  project's OWN sources.
- **Applies thresholds via the shared `apply_thresholds()` gate** and emits a
  **JSON-serializable, prioritized** report (findings sorted most-severe-first).
- **Calls ZERO model and ZERO network-to-model** (the same hard constraint as
  `ingest.py` / `verify.py`; a static guard test enforces it). The only subprocess it
  ever spawns is a local adapter-declared scanner tool. A MISSING tool degrades to a
  structured `tool_unavailable` marker, never a crash and never a fabricated finding.
- Scanner presence is reported through the **non-gating** `scanner` / `scanner_cpp`
  doctor categories via `--doctor`, reusing `ingest.doctor()` so the preflight is
  identical to `verify.py`'s.

### Model-facing step: summarize / prioritize / name limitations

The read-only **`apia-reviewer`** agent runs `scan.py`, reads its structured report,
and only **summarizes and prioritizes the REAL findings** grounded in the scanner
output and the cited code - it never invents a finding, reports a missing scanner
honestly, and asks ONE specific clarifying question when uncertain. Four Tier-2 skills
(`apia-security-scan`, `apia-quality-scan`, `apia-perf-scan`, `apia-limitations`; the
heavy scans use `context: fork`), the Tier-1 **`/apia-review`** prompt, and a concise
Tier-0 reviewer protocol express this step across every portability tier. On **Visual
Studio 2022** (Tier-0 + Tier-1 only, no Tier-2 skills/agents) the reviewer runs as a
single Tier-0/Tier-1 agent driving `scan.py` for both Python and C++.

### The composable-gate interface (the stable contract)

Both the off-model engine and the model-facing step consume one stable interface, so a
standalone review AND a **future greenfield-generation gate** can call it without
rework. `scan(repo, adapter, options)` returns a JSON-serializable report:

```
{
  "repo": <basename>, "repo_path": <abs>, "stack": <adapter name>,
  "adapter_source": <how the adapter was chosen>, "category": <scanned category>,
  "findings": [ {severity, category, file, line, rule, message, source_tool}, ... ],
  "counts_by_severity": {critical, high, medium, low, info},
  "counts_by_category": {security, quality, perf, ...},
  "threshold": { ...effective thresholds... },
  "gate": {
    "passed": bool,
    "fail_on_severity": <level or null>,
    "failing_severities": [...], "failing_counts": {category: {limit, actual}},
    "reasons": [human-readable strings]
  },
  "tools": { ...scanner presence (doctor-style)... },
  "scanners": [ ...per-scanner meta... ],
  "unavailable_tools": [ ...missing tool names... ]
}
```

The gate decision itself is produced by the SINGLE shared function:

```
apply_thresholds(findings, thresholds) -> gate dict
```

`thresholds` (from the `review` block in `.apia/config.yml`) may declare
`fail_on_severity` (any finding at that severity or more severe fails) and
`max_findings` (per-category caps plus an optional `total`). BOTH a standalone reviewer
and a future generation gate read this SAME `review.thresholds` block and call
`apply_thresholds()` with the SAME signature, so the gate is **composable without
rework**. The structured report feeds the **iterate-to-best** eval-loop
(`.apia/scripts/evalloop.py`) as a quality gate alongside the Layer-1 eval data: this
**fusion is DELIVERED in Phase 5** via the single shared `evalloop.evaluate_quality_gated`
(see "Iterate-to-Best (Phase 5)" in section 3b), which calls this SAME
`apply_thresholds()` against this SAME `review.thresholds` block.

> **The user's requirement:** once greenfield generation ships, **each generated
> increment passes through these SAME reviewer gates** (`scan.py` +
> `apply_thresholds()`) before it is accepted - the generator does not get a weaker
> bar than a human-authored change.

---

## 3e. Greenfield generation (Phase 3) and its two-tier honest verification

APIA generates a **working, tested, runnable** Python **or** C++ project from an
**approved** spec. Like every other capability it splits into an **off-model engine**
and a **model-facing step**, and it reuses the SAME composable gate from section 3d:
the off-model generation engine (`.apia/scripts/scaffold.py`) does only deterministic
work (shape detection, skeleton emission, build/run/test normalization, the gate
decision), and the LIVE Copilot session writes the feature code. `scaffold.py` makes
**zero network and zero LLM calls** (the same hard constraint as `ingest.py` /
`verify.py` / `scan.py`; a static guard test enforces it) and spawns **no subprocess
of its own** - every build/test run routes through `verify.py`'s existing single
`_run()` chokepoint.

### The two tiers

- **Tier 1 - generic shape generation (deterministic).** From an approved spec,
  `detect_shape` maps the request to a fixed project **shape**, and `emit_skeleton`
  writes the idiomatic base layout + build file (`pyproject.toml` / `CMakeLists.txt`),
  the package/source layout, a runnable entrypoint stub, and a `tests/` smoke test.
  Emission is deterministic and idempotent (re-emitting is byte-identical) and never
  invents feature logic.
- **Tier 2 - verified "it actually runs" (honest).** `verify_generated` drives
  **build -> run/smoke -> unit tests** through `verify.py`'s machinery and normalizes
  the result into the shared counts shape. For a **fully-verifiable** shape the
  generated project must BUILD (C++), run its unit tests, and be **all-green**. For a
  shape that inherently cannot be auto-verified in a headless environment the result is
  reported **honest-partial** and **never green** (see the matrix below).

### Shape detection

`detect_shape(spec)` maps an approved structured spec to one of the supported shapes
(an explicit `shape` wins; otherwise `stack` + `kind`/`target` hints and keyword probes
decide, honest-partial shapes checked first so a GUI intent is not mis-read as a plain
CLI). It **never guesses silently**: an ambiguous spec still returns a best-effort shape
but marks `confidence: "low"` and records why in `reasons`, so the LIVE clarify gate (the
model's job, not the script's) can ask. The authoritative shape list lives in
`.apia/config.yml` under `generate.supported_shapes` (resolved REPO-FIRST), with a
built-in fallback.

### Shape -> verification matrix

| Shape | Stack | Tier | What is auto-verified | Why / what a human still checks |
|---|---|---|---|---|
| `python_cli` | Python | full | import + run the `python -m <name>` entrypoint + unit tests all green | fully deterministic |
| `python_library` | Python | full | import the package + unit tests all green | fully deterministic |
| `python_web_api` | Python | full | the health/route handlers served + exercised via the framework test client (no live socket) + unit tests all green | fully deterministic; a real network deployment is still a human/CD step |
| `cpp_console` | C++ | full | CMake build (GoogleTest via FetchContent) + the console target links + CTest unit tests all green | fully deterministic |
| `cpp_library` | C++ | full | CMake build + the library target links + CTest unit tests all green | fully deterministic |
| `gui` | Py/C++ | honest-partial | skeleton builds/compiles + unit tests run | window/widget/user interaction cannot be auto-verified without a human at a display |
| `embedded` | C++ | honest-partial | skeleton builds/compiles + unit tests run | on-device behavior needs the target hardware/emulator |
| `driver` | C++ | honest-partial | skeleton builds/compiles + unit tests run | driver load/runtime needs the target kernel/privileges |

An honest-partial shape emits a plain-language `PARTIAL.md` naming what a human must
still check, and `verify_generated` returns `partial: True`, `ok: False`, and a
non-empty `unverified_aspects` list. **A faked green is impossible for these shapes.**

### The generation gate (reuses the Phase-2 reviewer + the eval loop)

The gate adds **no new knobs**. Each generated increment passes through:

1. **The reviewer severity gate** - `generation_gate(findings)` calls the SAME
   `scan.apply_thresholds` against the SAME `review.thresholds` from `.apia/config.yml`
   (section 3d). The generator gets no weaker a bar than a human-authored change.
2. **The eval loop** - `decide_generation` reuses `evalloop.resolve_max_iterations`
   (the HARD `max_iterations` cap) so a persistently-red generation **iterates then
   STOPS** at the cap with a specific ask, never looping forever and never silently
   shipping a broken increment.
3. **The anti-circularity acceptance gate** - `decide_generation` reuses
   `evalloop.propose_acceptance_criteria` / `acceptance_criteria_confirmed`
   (section 3c): acceptance criteria describing NEW behavior the code does not yet
   exhibit must be **human-confirmed** before the loop grades the generation against
   them (it returns `status: "ask"` until then).

### The honest definition of "done"

"Any Python/C++ project from one prompt" is delivered **honestly**, not as a
one-sentence-to-flawless-production claim. The real flow is:

```
prompt -> ONE batched clarify -> approved spec -> generated, TESTED, RUNNABLE
          skeleton + core features -> human checkpoints (spec approval, acceptance
          criteria, apply)
```

That is **minimal, well-placed human input - not zero interaction**, and the Tier-2
shapes are plainly reported partial rather than overclaimed. The generator rides the
user's Copilot session (no external LLM), stays token-efficient (the off-model engine
does the mechanical work for free), and stays **strictly local** (no auto-push/PR/CI).

### Deferred (clearly labeled)

The following remain **DEFERRED** (see section 7): the **C#/Groovy** adapters to parity;
concrete **MCP data sources**; auto-push/PR/MR/CI integration.
(`apia-orchestrator` task-graph autonomy is now **DELIVERED** -- see section 3f; the full
**iterate-to-best** threshold fusion is now **DELIVERED** -- see "Iterate-to-Best (Phase 5)"
in section 3b; **LOCAL git-based knowledge & capability sharing + plugins** is now
**DELIVERED** -- see section 3g, with external/hosted sharing **intentionally out of scope**
per section 7.)

---

## 3f. The Orchestrator (Phase 4) and its task-graph autonomy

A single change (or a single generated project) is **one** trip through the loop with
a human at every gate. A whole feature on an existing project, or a whole greenfield
app, is **many** such trips. The orchestrator's one job is to **reduce manual
interaction**: it takes a larger goal, breaks it into an **ordered task graph**, and
drives each task through the loops already built in sections 3a-3e, running
autonomously **between batched human checkpoints** instead of stopping one-per-task.
Like every other capability it splits into an **off-model engine** and a
**model-facing step**, and it is a **conductor, not a reimplementation**: it reuses the
SAME gates from sections 3b/3d/3e verbatim.

The off-model engine (`.apia/scripts/orchestrate.py`) does ONLY deterministic work --
build + validate the graph, detect cycles fail-loud, pick the ready topological
frontier, apply one state transition at a time (unblocking dependents), persist and
reload a resumable run-state, enforce the global stop bound, and render the audit
trail. It makes **zero network and zero LLM calls** (the same hard constraint as
`ingest.py` / `verify.py` / `scan.py` / `scaffold.py` / `evalloop.py`; a static guard
test enforces it) and spawns **no subprocess of its own** -- any build/test run reaches
`verify.py`'s single `_run()` chokepoint only INDIRECTLY, through the engines it
composes. The LIVE Copilot session does the per-task reasoning/generation/fix and the
batched checkpoint conversation.

### The task-graph model

A run is an ordered list of **task nodes**, each a dict with a stable shape:

| Key | Meaning |
|---|---|
| `id` | Unique, non-empty string id (unique across the graph). |
| `type` | One of `ingest`, `code`, `test`, `generation`, `review` -- what the task does and therefore which engine routes it. |
| `deps` | Ids this task depends on; a task is **ready** only when every dep is `done`. A self-dependency or a dep on an unknown id is rejected. |
| `status` | Lifecycle: `pending` -> `running` -> `done`, or `failed`; `blocked` when a dependency failed. |
| `result` | The verdict object the delegated engine (or the live stage) returned, recorded **verbatim** (no re-decision). |
| `approvals` | The batched human approvals recorded at a checkpoint. |
| `ask` | The specific question surfaced when the task cannot proceed (fail-loud, never a silent loop). |

`build_graph` validates the graph fail-loud (unique ids, known types/statuses, deps
reference existing ids, no self-dependency). `detect_cycles` returns the offending
cycle path and `topological_order` **rejects a cyclic graph** rather than silently
running it. `ready_tasks` returns the stable, id-sorted topological **frontier** -- the
set the live session may dispatch next. The run is **strictly local**; nothing is ever
auto-pushed.

### Batched checkpoints

The orchestrator asks **everything knowable up front** and then runs **uninterrupted to
the next checkpoint**, instead of interrupting at every file or task. The live planner
turns the goal into the task graph and gathers the clarifying questions and approvals
for the whole upcoming batch in one turn (bounded by the advisory `token_budget.
orchestrate` ceiling); the deterministic engine then advances the frontier task by task
without further prompting until it reaches a meaningful checkpoint or genuine
uncertainty. The human still approves at the batched checkpoints and at the final apply
-- this is **minimal, well-placed interaction, not zero interaction**.

### Resumable run-state + human-readable audit log

Every transition is persisted. `new_run_state` captures the resolved bounds, the
normalized task nodes, and an opening `graph_built` entry; `save_run_state` writes the
document **atomically** (temp file + `os.replace`, so a crash mid-write never leaves a
half-written run-state) and `load_run_state` is a lossless round-trip. The run-state
schema is `{run_id, goal, repo, created, updated, global_max_tasks,
per_task_max_iterations, status, stop_reason, tasks, audit}`.

The documents live under `.apia/runs/` and are **committed on purpose** -- the same
discipline as `.apia/knowledge/` -- so a long autonomous run **survives sessions and
windows**, can be stopped in one session and resumed in another (a fresh process reads
the saved frontier and continues), and is shared with the team. `render_audit` prints a
plain-text, chronological **audit trail** (`graph_built`, `task_ready`, `task_running`,
`task_done`, `task_failed`, `task_blocked`, `global_cap_reached`, `run_stopped`,
`run_done`) readable without parsing JSON. A committed example lives at
`.apia/runs/example-run.json` + `.apia/runs/example-run.audit.log` (see `docs/PROOF.md`).

### Gate reuse (the conductor adds no new knobs)

Each task is ROUTED to the EXISTING gate engine; the orchestrator records the verdict
and **never re-decides**:

1. **`code` / `test`** -> `evalloop.evaluate`, which already enforces the HARD
   `max_iterations` cap (section 3b) and applies the reviewer severity gate via
   `scan.apply_thresholds(findings, review.thresholds)` (section 3d). The per-task cap
   REUSES `evalloop.resolve_max_iterations` -- there is **no second per-task cap**.
2. **`generation`** -> `scaffold.decide_generation` / `scaffold.generation_gate`, the
   SAME two-tier honest generation gate from section 3e.
3. **`ingest` / `review`** -> live Copilot stages with no deterministic gate engine of
   their own; the orchestrator records the live stage's supplied verdict as-is.

So the generator and every orchestrated task get **no weaker a bar** than a
human-authored change, and the thresholds/caps live in exactly one place
(`.apia/config.yml`).

### Bounded + fail-loud behavior

The run can never grind indefinitely or silently ship something broken. Three
independent bounds enforce this:

- **Global stop bound** -- `orchestrate.global_max_tasks` in `.apia/config.yml` caps how
  many tasks a single run may move to running/done; reaching it **HALTS** the run
  `stopped` with a `global_cap_reached` reason (exit non-zero), rather than continuing.
- **Per-task iteration cap** -- the REUSED `max_iterations` bounds each code/test task's
  self-correction loop; a persistently-red task stops at the cap with a specific ask.
- **Stop-and-ask on a stuck task** -- a task set `failed` **HALTS** the whole run
  (status `failed`, exit non-zero) and leaves its dependents **blocked**, surfacing that
  task's specific `ask` so a human can resolve it, rather than looping or guessing. A
  graph that can no longer progress (nothing ready, not all done) stops as a named
  deadlock.

### The honest definition of "done"

The orchestrator makes a multi-task run require **few, well-placed human inputs** instead
of one-per-task. It does **not** turn APIA into a flawless zero-interaction auto-coder:
the human still approves at the batched checkpoints and the final apply, a stuck task
stops and asks rather than forcing a green, and nothing is auto-pushed. "**Minimal
interaction, not no interaction**" -- the same honesty discipline as the Tier-2 shapes
in section 3e. It rides the user's Copilot session (no external LLM), stays
token-efficient (the off-model engine does the graph/state mechanics for free), and
stays **strictly local**.

### Deferred (clearly labeled)

The following remain **DEFERRED** (see section 7): the **C#/Groovy** adapters to parity;
concrete **MCP data sources**; **auto-push/PR/MR/CI/packaging/signing** (APIA stays
strictly local; the user pushes manually). (The full **iterate-to-best** threshold fusion
is now **DELIVERED** -- see "Iterate-to-Best (Phase 5)" in section 3b; **LOCAL, git-based
knowledge & capability sharing** is now **DELIVERED** in Phase 6a -- see section 3g.
External/hosted/cross-org sharing is **intentionally out of scope**, not deferred -- see
section 7.)

---

## 3g. LOCAL knowledge & capability sharing (Phase 6a)

APIA teams already share one thing through the git repo: the committed
`.apia/knowledge/` base. Phase 6a generalizes that single idea -- **sharing is a
committed file, pulled like any other** -- into four capabilities, all driven by the
off-model engine `.apia/scripts/share.py` and all **strictly LOCAL**. There is **no
server, no network service, no auth/multi-tenancy, no download, and nothing leaves the
machine**: `share.py` makes **zero LLM and zero network calls** (a static guard test
enforces it, exactly like `scan.py` / `orchestrate.py`). It never reasons; it does only
mechanical, reproducible work and **composes the existing engines** (`ingest`, `scan`,
`scaffold`) rather than duplicating them.

CLI (one mutually-exclusive action per run, plus shared options
`--repo` / `--knowledge-dir` / `--out` / `--pack` / `--profile` / `--force` / `--json`):

```bash
share.py --export-pack --repo . --knowledge-dir <dir> --out pack.json
share.py --import-pack --pack pack.json --knowledge-dir <dir> [--force]
share.py --apply-profile --profile <profile.yml> --repo .
share.py --manifest  --repo . [--json]
share.py --plugins   --repo . [--json]
share.py --doctor    --repo .
```

### (1) Versioned knowledge packs (safe no-clobber merge)

`export_pack()` reads the **8 committed knowledge artifacts** (`ingest.ARTIFACTS`, incl.
`project-facts.md`) plus the `.index.json` index and emits one portable JSON pack with a
top-level `schema_version` ("1"), the source repo, a timestamp, and per-artifact
`{content, hash}`. `import_pack()` does a **SAFE no-clobber merge** into a target
knowledge dir: a missing or byte-identical artifact is written freely; a **divergent
local artifact is treated as a local correction** -- it is **KEPT**, recorded under
`conflicts` / `skipped_local`, and surfaced, **never silently overwritten**. This is the
same respect for user-corrected knowledge the rest of APIA applies. An explicit opt-in
`--force` overrides a divergent copy (recorded under `forced`). An **unknown/newer**
`schema_version` is **REPORTED** (`unknown_version: true`) and **not applied** -- a newer
pack layout can never corrupt an older install.

### (2) Shared org/team profiles (LOCAL overrides win)

A committable profile (`.apia/profile.yml`; an example ships as
`.apia/profiles/example-team.yml`) uses the **SAME keys as `.apia/config.yml`** -- no new
top-level concepts are invented. `apply_profile()` **deep-merges the profile UNDER the
local config**, so a key the local config sets ALWAYS **wins**, and a key the local config
leaves unset is **filled in** from the profile. The result carries a **provenance map**
(dotted key -> `local` | `profile`) so the manifest/report shows exactly which keys came
from where. Precedence resolution is **REPO-FIRST** (the consuming repo's config, then the
package fallback), the same resolution `scaffold._load_config` uses.

### (3) LOCAL plugins / extensions (committed files, additive)

Team-provided plugins live under a committable `.apia/plugins/{adapters,scanners,shapes}/`
dir (see `.apia/plugins/README.md` and the plugin contract in `.apia/adapters/README.md`).
`discover_plugins()` / `load_plugins()` scan the dir **REPO-FIRST then package**; the
existing engines **consult** the loader -- `ingest` (stack detection), `scan` (adapter +
scanner enumeration), `scaffold` (supported shapes), and `verify` (adapter path
resolution). Plugins are **strictly additive**: an absent dir changes nothing. Discovery
reads **only committed local files** -- **no download, no fetch, no marketplace, no
network**. A **malformed** plugin (bad YAML / missing required keys) is captured in the
loader's `errors` list and **skipped**; the loader **never raises**. Committed `example-*`
plugins (an `example-lua` adapter, an `example-python-perf` radon-mi scanner, an
`example-cpp-plugin` shape) make the mechanism demonstrable and testable.

### (4) Capability manifest (reuses doctor + detection)

`build_manifest()` emits one machine-readable description of the install, **REUSING**
`ingest.doctor()` plus the adapter/scanner/shape detection: every adapter marked
**complete-vs-stub** (python complete, cpp complete, csharp stub, groovy stub, plus any
plugin adapter), the available **scanners**, the **supported_shapes** (incl. a plugin
shape such as `cpp_plugin`), the **loaded plugins** (incl. any `errors`), the applied
profile's **provenance**, and **versions** (`apia_version: "6a"`, `schema_version: "1"`).
It is the single query a human or an agent runs to see what *this* checkout can do.

All four capabilities ride the user's Copilot session for any reasoning (the engine itself
reasons about nothing), stay token-efficient, and stay **strictly local**: sharing is a
`git commit` + `git pull` away, exactly like the knowledge base. See `docs/PROOF.md` for
the executed round-trip / precedence / plugin-load / manifest evidence and `USAGE.md` for
the command recipes.

---

## 4. Component inventory (what ships in the package)

```
<repo>/
  .github/
    copilot-instructions.md         # Tier 0: universal core protocol (short, always-on)
    prompts/                         # Tier 1: portable slash-command entrypoints
      apia-ingest.prompt.md
      apia-feature.prompt.md
      apia-clarify.prompt.md
      apia-test.prompt.md
      apia-apply.prompt.md
      apia-iterate.prompt.md
      apia-review.prompt.md          # Tier 1: drives the read-only reviewer (VS 2022-safe)
      apia-new.prompt.md             # Tier 1: drives greenfield generation (VS 2022-safe)
      apia-run.prompt.md             # Tier 1: drives the orchestrator multi-task run (VS 2022-safe)
    skills/                          # Tier 2: rich, token-efficient skills (VS Code host)
      apia-ingest/SKILL.md           # context: fork
      apia-feature-loop/SKILL.md
      apia-test-authoring/SKILL.md   # context: fork
      apia-security-scan/SKILL.md    # context: fork  (reviewer scan skill)
      apia-quality-scan/SKILL.md     # context: fork  (reviewer scan skill)
      apia-perf-scan/SKILL.md        # context: fork  (reviewer scan skill)
      apia-limitations/SKILL.md      # context: fork  (model synthesis over findings + KB)
      apia-scaffold/SKILL.md         # context: fork  (generation: detect shape + emit skeleton)
      apia-e2e-verify/SKILL.md       # context: fork  (generation: build+run+test + gate)
      apia-orchestrate/SKILL.md      # context: fork  (orchestrator: task-graph / run-state / resume)
    agents/                          # Tier 2: per-stage custom agents + handoffs (approval gates)
      apia-planner.agent.md          # read-only tools
      apia-implementer.agent.md      # edit tools
      apia-tester.agent.md           # test/terminal tools
      apia-reviewer.agent.md         # read-only tools (runs scan.py, summarizes findings)
      apia-architect.agent.md        # generation: drives scaffold.py (shape -> skeleton -> verify -> gate)
      apia-orchestrator.agent.md     # orchestrator: plans the task graph, drives orchestrate.py, batched checkpoints
  .apia/
    knowledge/                       # cached, committed project knowledge base
    runs/                            # committed resumable run-state + human-readable audit log (orchestrator)
    config.yml                       # token budget, max_iterations cap, eval_data policy, review thresholds, generate.supported_shapes
    scripts/                         # off-model preprocessing + eval machinery (no model calls)
      ingest.py                      #   static indexing / detection / doctor preflight
      verify.py                      #   deterministic verifier + --iterate loop surface
      evaldata.py                    #   Layer 1: grounded eval-data generation
      evalloop.py                    #   Layer 2: iterate-to-green loop + acceptance gate
      scan.py                        #   Reviewer: off-model scanners -> canonical findings + composable gate
      scaffold.py                    #   Phase 3: shape detect + skeleton emit + build/run/test normalize + gate
      orchestrate.py                 #   Phase 4: task-graph build/validate + run-state/resume + global stop + audit
      share.py                       #   Phase 6a: LOCAL knowledge packs / org-team profiles / plugin loader / capability manifest
    adapters/                        # per-stack build/test + scanner + generate maps (py + cpp complete; csharp/groovy stubs)
    plugins/                         # Phase 6a: committed LOCAL plugins (adapters/scanners/shapes); additive, no download
    profiles/                        # Phase 6a: example committable org/team profile (same keys as config.yml)
  samples/
    generated/                       # committed generated proof projects (sources only; build/ + out/ gitignored)
      python_cli/ python_library/ python_web_api/ cpp_console/ cpp_library/   # fully verified
      gui/                           # honest-partial proof (reports partial, never green)
      specs/                         # the approved specs each sample was emitted from
  docs/
    ARCHITECTURE.md                  # this document
```

- **MCP wiring (deferred to Phase 2):** external data sources are declared declaratively (custom-agent
  `mcp-servers` for `target: github-copilot`, or workspace MCP config) so data fetches route through
  Copilot's tool-use - never a separate LLM call. **Phase 1 wires NO concrete MCP data source** (see
  open question #4): the architecture, prompts, and agents describe the mechanism and the routing
  contract, but no issue tracker, package index, or API catalog is connected in this milestone. The
  `web/fetch` tool on `apia-planner` is the only live external-data path in Phase 1, and even that is
  data-only (never model inference). Concrete MCP server wiring is a Phase-2 deliverable; nothing in
  Phase 1 depends on it, so the loop runs fully offline today.

---

## 5. Token / credit efficiency strategy (first-class)

1. **Off-model preprocessing.** Mechanical work (tree walk, manifest parse, test discovery, hashing)
   runs as bundled scripts; the model only summarizes distilled output.
2. **Progressive disclosure.** Skills load name/description first, body on match, bundled files only
   when referenced (native Agent Skills behavior).
3. **Cached knowledge base.** Ingest once, reuse forever; incremental re-ingest on `git diff` only.
4. **Retrieval over dumps.** Load the relevant knowledge slice + specific files, never the whole repo.
5. **Forked contexts.** Heavy stages (`ingest`, `test-authoring`) run in `context: fork` so their
   reasoning does not bloat the main conversation.
6. **Lean always-on file.** `copilot-instructions.md` stays short and defers detail to on-demand
   skills/prompts.
7. **Scoped tools per agent.** Read-only planner avoids accidental expensive edit/test cycles.
8. **Token budget in `config.yml`** with soft warnings when a stage's context would exceed budget.

---

## 6. Anti-hallucination strategy (first-class)

1. **Confidence threshold -> ask.** Encoded in Tier-0 instructions; applies in every host.
2. **Explicit assumptions list** surfaced at the CLARIFY gate for user correction.
3. **Grounding in the cached knowledge base** and real file reads, not recalled generalities.
4. **External facts via MCP/API**, never invented.
5. **User corrections persisted** back into `.apia/knowledge/` so the same mistake is not repeated.
6. **Tests as ground truth.** Behavior is validated by execution, not by the model's assertion.

---

## 7. Phased roadmap

### Phase 1 - End-to-end loop on one host (THIS MILESTONE)

Deliver a **minimal working skill package for VS Code** that proves
`ingest -> clarify -> modify -> test -> apply` on **one sample repository**, with:

- Tier-0 `.github/copilot-instructions.md` (core protocol, gates, clarify rule, token budget).
- Tier-1 prompt-file entrypoints for each stage (portable).
- Tier-2 `apia-ingest` and `apia-feature-loop` skills (+ `apia-test-authoring`), custom agents with
  handoffs as approval gates.
- `.apia/` knowledge base, off-model preprocessing scripts, and **stack adapters for Python first**
  (with stubs for C++, C#, Groovy).
- A **sample Python repo** under `samples/` to prove the loop, including the no-tests ->
  characterization-test path.
- A verification harness proving existing-tests-pass + new-tests-added.

Phase 1 is decomposed into concrete tasks under `.tasks/` (see `docs/PHASE-1-TASKS.md`).

### Phase 2 - Multi-stack hardening + continuous insight (high level)

- **C++ is now implemented** (CMake + MSBuild adapter, GoogleTest characterization template,
  build-only phase, CTest/GoogleTest result parsing - MSBuild/VSTest unit-tested only). The four
  cross-stack features (doctor, project-facts memory, dry-run, build-only) ship with it. **DEFERRED
  within Phase 2:** C#/Groovy adapters to parity, and concrete **MCP data sources**.
- **DELIVERED - Continuous security/quality/perf/limitations scanning (the Reviewer)**: dependency
  CVE checks (pip-audit), SAST-style vulnerability patterns (bandit; cppcheck security),
  lint/complexity/duplication/dead-code (ruff/flake8/radon; clang-tidy/cppcheck), and perf smells
  (radon; cppcheck performance) are run by the **off-model `scan.py` engine** and surfaced as a
  prioritized, grounded findings report; the model only summarizes/prioritizes (never invents a
  finding), respecting the `token_budget.review` ceiling. See section 3d. The findings report and
  the shared `apply_thresholds()` gate are exposed as a **composable interface** so a future
  greenfield-generation gate can reuse them.
- **DEFERRED - Improvement-opportunity suggestions** grounded in the knowledge base.
- **DELIVERED (Phase 3) - Greenfield generation** (`apia-architect` / `apia-scaffold` /
  `apia-e2e-verify` / `/apia-new`, backed by the off-model `scaffold.py`): a from-scratch,
  two-tier, HONEST generation flow for Python and C++ (see section 3e). Each generated increment
  passes through the SAME reviewer gates (`scan.py` + `apply_thresholds()`) and the TEST layers
  before it is accepted - the generator gets no weaker a bar than a human-authored change. The
  **five fully-verifiable shapes** (`python_cli`, `python_library`, `python_web_api`,
  `cpp_console`, `cpp_library`) are generated, built, run, and unit-tested green end-to-end
  in-sandbox; the **honest-partial shapes** (`gui`/`embedded`/`driver`) are generated + build-checked
  + unit-tested and reported PARTIAL, never faked green. Committed proof projects live under
  `samples/generated/` (see `docs/PROOF.md`). **DEFERRED within Phase 3:** the full iterate-to-best
  threshold tuning/fusion, the C#/Groovy generation adapters, and MCP data sources (listed below).
- **DELIVERED (Phase 4) - `apia-orchestrator` task-graph autonomy**: multi-task planning/execution
  as a dependency graph, backed by the off-model `orchestrate.py` engine. It drives a larger goal
  through the EXISTING loops (ingest/clarify/modify/test, the eval/iterate loop, the greenfield
  generation gate, the reviewer gate) as an ordered task graph, running autonomously between BATCHED
  human checkpoints and keeping a persistent, resumable run-state + human-readable audit log under
  `.apia/runs/` (committed, like the knowledge base). It is bounded + fail-loud (global stop bound +
  the REUSED per-task `max_iterations` cap + stop-and-ask on a stuck task), reuses the SAME gates
  (`evalloop.evaluate`, `scan.apply_thresholds`, `scaffold.generation_gate`) with no new knobs, and
  stays strictly local (no auto-push). "Minimal interaction, not no interaction" (see section 3f).
- **DELIVERED (Phase 4) - Batched-upfront-clarification**: the orchestrator asks everything knowable
  up front, then runs uninterrupted to the next checkpoint (section 3f).
- **DELIVERED (Phase 5) - Iterate-to-Best fusion**: the reviewer findings report is wired
  into the `evalloop.py` iterate loop as a quality gate alongside the Layer-1 eval data,
  through the SINGLE shared decision `evalloop.evaluate_quality_gated`. Each iteration's
  acceptance criterion is now tests/build green AND Layer-1 green AND the reviewer gate
  (`scan.apply_thresholds` vs `review.thresholds`) passing; on a failing gate the EXACT
  scanner findings fold into evalloop's EXISTING structured failure report
  (`scanner_findings` + `reviewer_gate`); the loop tracks best-so-far and, on the HARD
  `max_iterations` cap, STOPS and asks carrying the BEST attempt (not the last) + a specific
  residual summary. All three entry points (`verify.py --iterate` with `--findings`,
  `scaffold.decide_generation`, `orchestrate.decide_task`) delegate to it so "best" means the
  same thing everywhere, for both Python and C++. The quality bar is the EXISTING tunable
  `review.thresholds`; the only new knob is `eval_data.iterate_to_best.best_so_far_tie_break`
  (both under existing config structures). "best" = meets the configured deterministic
  thresholds OR the closest bounded attempt with an honest residual report, **not** provably
  optimal code. See "Iterate-to-Best (Phase 5)" in section 3b and `docs/PROOF.md`.
- **DELIVERED (Phase 6a) - LOCAL, git-based knowledge & capability sharing**: four off-model
  capabilities in `.apia/scripts/share.py` (versioned **knowledge packs** with a safe no-clobber
  merge; shared **org/team profiles** layered under `config.yml` with LOCAL-overrides-win precedence;
  a **local plugin/extension** contract for adapters/scanners/shapes as committed files, additive and
  download-free; a **capability manifest** reusing `ingest.doctor()` + detection). Sharing is purely
  **via the git repo** -- committed files, pulled like `.apia/knowledge/` -- with no server, no
  network, and nothing leaving the machine. See section 3g and `docs/PROOF.md`.
- **DEFERRED - auto-push / PR / MR / CI / packaging / signing integration** (APIA stays strictly
  local; the user pushes manually).
- **DEFERRED** - an **optional, opt-in self-hosted second-opinion reviewer** (off by default).
  Phase 1/2 ship **no** second/external/hosted LLM verifier (see section 3c): the verifier stays
  deterministic tooling and the loop rides only the user's Copilot session.
- **OUT OF SCOPE (intentionally, not deferred) - external / hosted / cross-org sharing**: a hosted
  knowledge-base **service**, an **MCP server exposing APIA** to other agents, **cross-org / external
  APIs**, **collaborative-workspace** services, and **marketplace distribution** of plugins are a
  **separate product** with its own security, hosting, and governance model. APIA's roadmap **ends
  cleanly at LOCAL git-based sharing** (Phase 6a); these hosted/external directions are deliberately
  not an APIA deliverable.

### Phase 3 - Validation gates (high level)

- Formalize **security**, **quality**, and **testing** validation gates that a change must pass
  before APPLY: SAST/secret-scan, coverage/quality thresholds, test-adequacy checks. Each gate is a
  skill/agent with scoped tools; failures route back to CLARIFY/MODIFY.

### Phase 6a - LOCAL knowledge & capability sharing (DELIVERED)

- **DELIVERED.** Teams share knowledge and capabilities **LOCALLY, through the git repo** -- the
  same way `.apia/knowledge/` is already shared. The off-model `.apia/scripts/share.py` engine
  provides four committed-file capabilities: versioned **knowledge packs** (safe no-clobber merge +
  opt-in `--force` + version-field handling), shared **org/team profiles** (same `config.yml` keys,
  deep-merged UNDER local config so LOCAL overrides win, with a provenance map), a **local
  plugin/extension** contract for adapters/scanners/shapes (committed files, REPO-FIRST discovery,
  additive, malformed-plugin graceful-failure, **no download / no marketplace / no network**), and a
  **capability manifest** (reusing `ingest.doctor()` + adapter/scanner/shape detection). See section
  3g for the design and `docs/PROOF.md` for the executed evidence.

### Out of scope (intentionally - a separate product, NOT a deferred APIA phase)

- **External / hosted / cross-org sharing is deliberately NOT on APIA's roadmap.** A hosted
  shared-knowledge **service**, an **MCP server that exposes APIA** to other agents, **cross-org /
  external sharing APIs**, **collaborative-workspace** services, and **marketplace distribution** of
  plugins each require their own **security, hosting, auth/multi-tenancy, and governance** model that
  is fundamentally at odds with APIA's founding constraint of being strictly local (no server, no
  network, nothing leaves the machine). They belong to a **separate product**, not to APIA. APIA's
  roadmap **ends at LOCAL git-based sharing (Phase 6a)**; this is an explicit scope boundary, not a
  "coming later" deferral.
  (The legitimately-deferred items remain as labeled above: C#/Groovy adapter parity, concrete MCP
  **data** sources, auto-push/PR/MR/CI/packaging/signing, and the optional opt-in self-hosted
  second-opinion reviewer.)

---

## 8. Open questions for the user (flag, do not guess)

1. **Primary Phase-1 host confirmation.** Design assumes **VS Code** for the rich tier. Confirm, or
   name the IDE that must work first.
2. **`.apia/` committed vs. git-ignored.** Design commits the knowledge base (team sharing, survives
   sessions). Confirm that is acceptable vs. keeping it local/ignored.
3. **Sample repo stack.** Phase-1 proof uses a **Python** sample (fastest adapter). Confirm Python is
   an acceptable first proof, given C++/C#/Groovy are co-equal priorities.
4. **MCP data sources.** Which concrete external data sources (issue tracker, internal package index,
   API catalog) should Phase 1 wire, if any, versus deferring all MCP wiring to Phase 2.
   **Resolved for Phase 1: all MCP wiring is DEFERRED to Phase 2.** Phase 1 connects no concrete MCP
   data source; it only documents the mechanism and keeps the routing contract (data via Copilot
   tool-use, never a separate LLM call). The sole live external-data path is `apia-planner`'s
   `web/fetch`, which is data-only. Revisit in Phase 2 to wire the first concrete source.
5. **Apply boundary.** Does APPLY stop at a local commit, or should it also open a PR (the orchestrator
   handles push/PR today)?
