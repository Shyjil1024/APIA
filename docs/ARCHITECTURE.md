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
language-agnostic; these stacks ship with ready-made adapters first.

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
- **New tests:** generate tests/verifications for the new behavior per the change spec. Stack-aware
  via adapters (pytest; GoogleTest/Catch2/CTest; xUnit/NUnit via `dotnet test`; Spock/JUnit via
  Gradle for Groovy).
- Apply the change in a scratch/working state, run existing + new tests, confirm **old behavior still
  passes and new behavior is verified**. Report pass/fail compactly.

GATE: user reviews the test results.

### Stage 5 - APPLY

- Write the approved changes, run the full relevant test suite once more, and prepare the commit
  (and/or PR). The agent **does not auto-push**; the user confirms the final apply/commit.

GATE: user confirms apply/commit.

Any stage can bounce back to CLARIFY if new uncertainty appears.

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
    skills/                          # Tier 2: rich, token-efficient skills (VS Code host)
      apia-ingest/SKILL.md           # context: fork
      apia-feature-loop/SKILL.md
      apia-test-authoring/SKILL.md   # context: fork
    agents/                          # Tier 2: per-stage custom agents + handoffs (approval gates)
      apia-planner.agent.md          # read-only tools
      apia-implementer.agent.md      # edit tools
      apia-tester.agent.md           # test/terminal tools
  .apia/
    knowledge/                       # cached, committed project knowledge base
    config.yml                       # token budget, stack adapters, thresholds
    scripts/                         # off-model preprocessing (indexing, test discovery)
    adapters/                        # per-stack build/test command maps (py, cpp, csharp, groovy)
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

- Flesh out C++, C#, Groovy adapters (build/test/characterization) to parity with Python.
- **Continuous risk/vulnerability/quality scanning**: dependency CVE checks, static analysis,
  complexity/duplication/debt signals, surfaced as prioritized findings - all via Copilot tool-use
  and off-model scanners, respecting the token budget.
- Improvement-opportunity suggestions grounded in the knowledge base.

### Phase 3 - Validation gates (high level)

- Formalize **security**, **quality**, and **testing** validation gates that a change must pass
  before APPLY: SAST/secret-scan, coverage/quality thresholds, test-adequacy checks. Each gate is a
  skill/agent with scoped tools; failures route back to CLARIFY/MODIFY.

### Phase 4 - Knowledge & capability sharing (high level)

- **Securely share knowledge/capabilities across teams, repos, orgs, and external systems** via
  APIs, plugins, agents, or collaborative workspaces: org-level custom agents/instructions, a shared
  knowledge-base service, an MCP server exposing APIA capabilities to other agents, and plugin
  packaging for marketplace distribution. Security/permissioning is a core concern here.

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
