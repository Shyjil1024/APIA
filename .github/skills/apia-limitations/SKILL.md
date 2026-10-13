---
name: apia-limitations
description: "APIA reviewer (LIMITATIONS). Model-facing synthesis of architectural limits, tech-debt, and scalability risks grounded in the ingest knowledge base (.apia/knowledge/) PLUS the aggregated scan.py findings across security/quality/perf. This stage reasons over real evidence - the scanners are mechanical and make no LLM/network-to-model calls, and the synthesis still NEVER fabricates: every risk traces to a knowledge-base fact, a scanner finding, or a real file read. A missing scanner is reported (doctor), never faked. Use when the user asks for architectural limitations, tech-debt, scalability risks, or an overall risk summary of the project. Runs in a forked context for token isolation; STOPS at the human-approval gate."
context: fork
---

# apia-limitations

Synthesize a project's **architectural limits, tech-debt, and scalability risks** - the
model-facing stage of the reviewer. It reasons over real evidence and **still never
fabricates**. This stage runs in a **forked context** for token isolation.

## Steps

1. **Aggregate the grounded scan findings** across all categories from the repo root:

   ```sh
   python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml \
       --category all --json
   ```

   The scanners are **mechanical and make no LLM and no network-to-model calls**; the
   report normalizes every finding to `{severity, category, file, line, rule, message,
   source_tool}` with `counts_by_category`, the `gate` decision, and `unavailable_tools`.

2. **Read the ingest knowledge base** under `.apia/knowledge/` (produced off-model by
   `ingest.py`): `architecture.md`, `project-map.md`, `dependencies.md`, `pipelines.md`,
   `workflows.md`, `stack.md`. Do **not** re-read the whole repo; retrieve the relevant
   slice.

3. **Synthesize limitations grounded in that evidence.** Cluster the aggregated findings
   plus knowledge-base facts into:
   - **architectural limits** (coupling, missing boundaries, duplicated subsystems),
   - **tech-debt** (dense complexity hotspots from radon/cppcheck, dead code, repeated
     lint classes),
   - **scalability risks** (hotspots on hot paths, dependency/CVE exposure).

   Every risk must trace to a knowledge-base fact, a scanner finding (`file:line` +
   `rule`/`source_tool`), or a real file read. **Never fabricate a limitation**; if the
   evidence is thin, say the evidence is thin and ask ONE specific question.

4. **Report missing scanners.** A `tool_unavailable` marker narrows the evidence - name
   the gap plainly rather than inferring around it or faking a finding.

5. **STOP at the gate.** Present the prioritized limitations summary and wait; write no
   code changes. Hand off to the planner only on explicit approval.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- The scanner engine is deterministic tooling; the synthesis only reasons over its
  grounded output plus the knowledge base - it never invents evidence.
- Stay within the `review` token budget in `.apia/config.yml`.
