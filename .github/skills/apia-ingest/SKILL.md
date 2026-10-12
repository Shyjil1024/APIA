---
name: apia-ingest
description: "APIA stage 1 (INGEST). Learns this project cheaply and builds the cached knowledge base. Run the off-model preprocessor .apia/scripts/ingest.py (mechanical tree walk, manifest parse, test discovery, hashing - no LLM/network calls), then summarize the emitted .apia/knowledge/ artifacts and STOP at the approval gate for the user to confirm or correct. Use when the user asks to ingest, learn, index, understand, onboard, or analyze a repository/project, or before any change request on an unlearned project. Runs in a forked context so heavy ingestion reasoning never pollutes the main conversation; only the distilled knowledge summary returns. Token efficiency lives here: retrieval over re-reading, incremental re-ingest on changed paths only, never dump the whole repo."
context: fork
---

# apia-ingest

Build (or refresh) APIA's persistent, cached knowledge base so Copilot never has to
re-read the whole repo. This stage runs in a **forked context** for token isolation.

## Steps

1. **Run the off-model preprocessor** from the repo root:

   ```
   python3 .apia/scripts/ingest.py <repo> [--full] [--knowledge-dir DIR]
   ```

   It walks the tree, parses manifests, discovers tests, and hashes sources with **no
   LLM and no network calls**, then writes compact Markdown artifacts to
   `.apia/knowledge/` plus an incremental `.index.json`. Use `--full` to force a
   complete re-ingest; otherwise it reprocesses only changed paths (git-diff style on
   source hashes).

2. **Read and summarize the artifacts** - do not re-read the raw repo:
   - `project-map.md` - tree + module responsibilities
   - `architecture.md` - detected structure
   - `dependencies.md` - manifests and declared dependencies
   - `pipelines.md` - CI/CD and build
   - `workflows.md` - business flows
   - `test-inventory.md` - discovered tests (or "no tests" signal)
   - `stack.md` - languages/frameworks detected

3. **Present a compact summary** (within the `ingest` token budget in
   `.apia/config.yml`): what the project is, its stack, and its test coverage.

4. **STOP at the approval gate.** Ask the user to confirm the summary is accurate.
   Fold any corrections back into the relevant `.apia/knowledge/` file - persisted
   corrections are themselves an anti-hallucination mechanism.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Never dump the whole repo; retrieve only the relevant slice.
- Do not proceed to CLARIFY/MODIFY until the user approves this summary.
