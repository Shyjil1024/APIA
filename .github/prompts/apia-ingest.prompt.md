---
description: "APIA stage 1 (INGEST): learn this project cheaply via the off-model preprocessor and summarize the cached knowledge base, stopping at the approval gate."
agent: apia-planner
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **INGEST** stage. This prompt drives the **read-only** `apia-planner`
agent, so its tools match the agent's own declaration: no `edit/files`, no
`run/terminal`. (Earlier drafts granted edit/terminal here; that loosened the
read-only planner for no benefit and has been removed.)

1. The off-model preprocessor is a **mechanical, pre-ingest step the host/user
   runs** from the repo root before this stage:
   `python3 .apia/scripts/ingest.py <repo>` (add `--full` to force a complete
   re-ingest; it emits Markdown artifacts under `.apia/knowledge/`). It does the
   tree walk, manifest parse, and test discovery with **no LLM/network calls**, so
   Copilot tokens are only spent summarizing. The planner does not need terminal
   access to produce the summary; it reads the artifacts the script already wrote.
2. Read the generated `.apia/knowledge/` artifacts (`project-map.md`,
   `architecture.md`, `dependencies.md`, `pipelines.md`, `workflows.md`,
   `test-inventory.md`, `stack.md`) and present a **compact** summary of what the
   project is, its stack, and its test coverage.
3. **STOP at the approval gate.** Ask the user to confirm or correct the summary.
   Corrections to the cached knowledge base are a **write**, so they are applied by
   the user (or by re-running the off-model ingest), not by this read-only agent -
   keeping the planner's no-accidental-writes guarantee intact.

Do not dump the whole repo; retrieve only what you need. Do not call any external LLM.
