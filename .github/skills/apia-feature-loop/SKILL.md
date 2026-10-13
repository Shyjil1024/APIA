---
name: apia-feature-loop
description: "APIA stages 2-3 (CLARIFY -> MODIFY). Orchestrates a feature/change request into an agreed change spec and then a proposed diff grounded in .apia/knowledge/. Restate the request, list assumptions, and ask a numbered list of clarifying questions until confidence meets the confidence_threshold in .apia/config.yml (anti-hallucination gate - never guess); fetch external data only via MCP/tool-use. Then produce a plan + proposed diff (write nothing yet) and STOP at each human-approval gate. Use when the user asks to implement, add, change, fix, refactor, or build a feature on a project that has already been ingested."
---

# apia-feature-loop

Drive a change request through the **CLARIFY** and **MODIFY** stages, stopping at each
human-approval gate.

## CLARIFY (anti-hallucination gate)

1. **Restate** the request in your own words.
2. **List assumptions** explicitly so the user can correct them.
3. **Ask a numbered list of clarifying questions** for every point of uncertainty:
   ambiguous scope, missing acceptance criteria, unknown external contracts,
   conflicting conventions.
4. **Hard rule:** if your confidence is below the `confidence_threshold` in
   `.apia/config.yml`, keep asking - **never guess**.
5. **Fetch external data via MCP/tool-use** (API schemas, library docs), never invent it.
6. Output an agreed **change spec**: what, where, acceptance criteria, out-of-scope.
7. **Acceptance-criteria confirmation gate (anti-circularity).** For acceptance criteria
   that describe **NEW** behavior the code does not yet exhibit, get **ONE human
   confirmation** before the loop treats them as truth
   (`eval_data.acceptance_criteria_gate` in `.apia/config.yml`). The same model must not
   both write the code and silently invent the test it grades itself against.
8. **STOP** until the user answers / approves the change spec and any new-behavior criteria.

## MODIFY (propose, do not write)

1. Produce a **plan + proposed diff** grounded in `.apia/knowledge/` and the specific
   files involved, following the project's detected conventions.
2. Write **nothing** to disk yet; present the diff for review.
3. Note which **Layer-1 eval-data** signal will verify the change
   (characterization > golden > invariant > differential) so verification is grounded
   in observable behavior, not the model's restatement of the request.
4. **STOP** for the user to approve the diff (or request changes -> loop back to CLARIFY).

## Next

Once the diff is approved, hand off to the TEST stage (`apia-test-authoring`) and then
APPLY. The change may **iterate to green** under the hard `max_iterations` cap via the
Layer-2 loop (`/apia-iterate`): the verifier is **deterministic tooling, not a second
model**, and the fix step is this live session only. Never call an external/separate LLM API.
