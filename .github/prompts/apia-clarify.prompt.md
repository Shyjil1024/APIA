---
description: "APIA stage 2 (CLARIFY): anti-hallucination gate. Restate the request, list assumptions, and ask numbered clarifying questions until confidence is met; never guess."
agent: apia-planner
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **CLARIFY** stage (the anti-hallucination gate).

1. Restate the request and list every **assumption** you are making.
2. Ask a **numbered** list of clarifying questions for all ambiguous scope, missing
   acceptance criteria, unknown external contracts, or conflicting conventions.
3. **Hard rule:** if your confidence in understanding the request is below the
   `confidence_threshold` in `.apia/config.yml`, ask questions - **never guess**.
4. Fetch any external data needed (API schema, library docs) via **MCP/tool-use**,
   not from memory.
5. Output an agreed **change spec** (what, where, acceptance criteria, out-of-scope)
   and **STOP** at the gate for user approval.
