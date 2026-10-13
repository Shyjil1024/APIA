---
description: "APIA Layer-2 iterate-to-green loop: assemble Layer-1 eval data, generate a change, run the deterministic verifier, feed the structured failure back and fix, and repeat until green OR the hard max_iterations cap, then STOP and ask. Honors the acceptance-criteria confirmation gate for new behavior and never auto-applies a broken change."
agent: apia-implementer
tools: ["edit/files", "run/terminal", "run/tests", "search/codebase"]
---

Run the APIA **iterate-to-green** self-correction loop. This is the Layer-2 loop that
sits inside TEST/APPLY. It runs on **this** Copilot session as a **single agent** (no
Tier-2 skills/agents required), so it works on **Visual Studio 2022** too.

The verifier is **deterministic tooling** (tests/build/diff/golden), never a second
model. The off-model scripts call no model; **the fix step is this live session only.**

1. **Assemble Layer-1 eval data (repo-derived ground truth).** Prefer the highest-trust
   signal available, in order **characterization > golden > invariant > differential**:
   - **characterization** - a test that pins the code's CURRENT observed behavior;
   - **golden** - freshly produced artifacts compared against a committed golden tree
     (per the adapter's `golden.artifact_glob` / `golden_dir` / `compare`);
   - **invariant** - a built-in structural property (`row_count_matches_input`,
     `no_nans`, `roundtrip_identity`) from the adapter's `invariants.checks`;
   - **differential** - the delta against the saved baseline (regressions vs. new).
2. **Acceptance-criteria confirmation GATE (anti-circularity).** For acceptance criteria
   that describe **NEW** behavior the code does not yet exhibit, propose them and get
   **ONE human confirmation** before the loop treats them as truth. The same model must
   not both write the code and silently invent the test it grades itself against. If the
   gate is unresolved, the verifier returns `needs_confirmation` with a specific ask -
   surface it and STOP.
3. **Generate the change** grounded in `.apia/knowledge/` and the agreed change spec.
4. **Run the deterministic verifier - one cycle at a time:**

   ```sh
   python .apia/scripts/verify.py <repo> --adapter .apia/adapters/<stack>.yml \
       --iterate --max-iterations N --emit-failure-report --json
   ```

   `--iterate` runs ONE deterministic cycle (assemble eval data -> run the verify
   phases -> decide). It honors the **hard `max_iterations` cap** and the advisory
   `token_budget.iterate` from `.apia/config.yml`.
5. **Read the decision:**
   - `green` - the cycle passed; the loop STOPS immediately. Proceed to APPLY (still
     human-gated).
   - `needs_fix` - take the **structured failure report** (the JSON `--emit-failure-report`
     prints), fix the code in this session, then re-run step 4 for the next cycle.
   - `needs_confirmation` - the acceptance-criteria gate is open; surface the `ask` and
     STOP for the human.
   - `stop` (`reason: max_iterations_reached`) - the hard cap was hit without going
     green. **STOP and ask the specific question** in the decision's `ask`. Do **not**
     auto-apply the broken change.
6. **Never silently apply a broken change; never loop forever.** The cap is a hard stop,
   not a suggestion. APPLY always gates on the human regardless of how few iterations it
   took.

Do not call any external/separate LLM API. Fetch external **data** only via MCP/tool-use.
