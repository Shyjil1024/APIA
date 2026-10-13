---
description: "APIA Layer-2 iterate-to-BEST loop: assemble Layer-1 eval data, generate a change, run the deterministic verifier AND the reviewer gate, feed the structured failure (incl. the exact scanner findings) back and fix, and repeat until tests/build are green AND the reviewer gate passes, OR the hard max_iterations cap, then STOP and ask carrying the best-so-far attempt. Honors the acceptance-criteria confirmation gate for new behavior and never auto-applies a broken change."
agent: apia-implementer
tools: ["edit/files", "run/terminal", "run/tests", "search/codebase"]
---

Run the APIA **iterate-to-best** self-correction loop. This is the Layer-2 loop that
sits inside TEST/APPLY. It runs on **this** Copilot session as a **single agent** (no
Tier-2 skills/agents required), so it works on **Visual Studio 2022** too.

The acceptance criterion is **not merely green**: an iteration is accepted only when the
deterministic test/build verification is green **AND** the reviewer gate passes the
configured `review.thresholds` (security/quality/perf severities). You iterate toward the
BEST solution those thresholds define, bounded and fail-loud.

The verifier and the scanners are **deterministic tooling** (tests/build/diff/golden +
`scan.py`), never a second model. The off-model scripts call no model and set no
temperature; **the fix step is this live session only.** "best" is enforced by the
deterministic thresholds + grounded findings, not by a temperature setting.

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
4. **Run the deterministic verifier + the reviewer gate - one cycle at a time.** First
   scan to produce the grounded findings report, then feed it to `--iterate` with
   `--findings` so the cycle targets green AND the reviewer gate:

   ```sh
   python .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml --json > scan.json
   python .apia/scripts/verify.py <repo> --adapter .apia/adapters/<stack>.yml \
       --iterate --max-iterations N --findings scan.json --emit-failure-report --json
   ```

   `--iterate` runs ONE deterministic cycle (assemble eval data -> run the verify
   phases -> apply `review.thresholds` to the findings -> decide). It honors the **hard
   `max_iterations` cap** and the advisory `token_budget.iterate` from `.apia/config.yml`.
   (Omitting `--findings` keeps the unchanged tests-green behavior.)
5. **Read the decision:**
   - `green` - tests/build green AND the reviewer gate passed; the loop STOPS immediately
     (best reached). Proceed to APPLY (still human-gated).
   - `needs_fix` - a verify phase failed OR the reviewer gate failed. Take the **structured
     failure report** (the JSON `--emit-failure-report` prints); on a gate failure it
     carries the EXACT scanner findings under `scanner_findings` and the
     `apply_thresholds` block under `reviewer_gate`. Fix the code in this session, re-scan,
     then re-run step 4 for the next cycle.
   - `needs_confirmation` - the acceptance-criteria gate is open; surface the `ask` and
     STOP for the human.
   - `stop` (`reason: max_iterations_reached`) - the hard cap was hit without fully
     converging (green + the gate). **STOP and ask the specific question** in the
     decision's `ask`, which carries the **best-so-far** attempt (the iteration with the
     fewest/lowest-severity residual findings, **not** necessarily the last) and a residual
     summary grouped by severity. Present that best attempt; do **not** auto-apply the
     broken/sub-threshold change, and do **not** silently accept it as "best".
6. **Never silently apply a broken or sub-threshold change; never loop forever.** The cap
   is a hard stop, not a suggestion. "best" means the configured thresholds are met, or the
   closest bounded attempt with an honest residual report - not provably optimal code.
   APPLY always gates on the human regardless of how few iterations it took.

Do not call any external/separate LLM API. Fetch external **data** only via MCP/tool-use.
