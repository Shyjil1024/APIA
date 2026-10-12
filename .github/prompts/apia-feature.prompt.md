---
description: "APIA stages 2-3 (CLARIFY -> MODIFY): turn a feature/change request into an agreed change spec and a proposed diff grounded in the knowledge base, stopping at each gate."
agent: apia-planner
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **feature loop** (CLARIFY -> MODIFY).

1. Restate the request in your own words and list your **assumptions**.
2. Ask a **numbered** list of clarifying questions for every point of uncertainty.
   If your confidence is below the `confidence_threshold` in `.apia/config.yml`,
   you MUST ask - never guess. Fetch any external data via MCP/tool-use, never invent it.
3. **STOP at the CLARIFY gate** until the user answers and approves a **change spec**
   (what, where, acceptance criteria, out-of-scope).
4. Produce a plan + **proposed diff** grounded in `.apia/knowledge/` and the specific
   files involved. Write nothing to disk yet.
5. **STOP at the MODIFY gate** for the user to approve the diff (or request changes,
   which loops back to CLARIFY).

Hand off to `apia-implementer` only after the diff is approved.
