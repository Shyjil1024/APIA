---
name: apia-perf-scan
description: "APIA reviewer (PERFORMANCE). Runs the off-model scanner engine .apia/scripts/scan.py for the perf category - performance smells, hotspots, and complexity (radon for Python; cppcheck performance checks for C++) - then SUMMARIZES and PRIORITIZES the REAL findings grounded in scanner output + cited file:line evidence. The scanners are mechanical and make no LLM/network-to-model calls; the model only summarizes and prioritizes, and NEVER invents a finding. A missing scanner is reported (doctor), never faked. Use when the user asks for a performance review, hotspot analysis, complexity/scalability pass, or to find perf smells. Runs in a forked context for token isolation; STOPS at the human-approval gate."
context: fork
---

# apia-perf-scan

Review a repo's **performance** smells and hotspots from grounded scanner evidence,
not guesswork. This stage runs in a **forked context** for token isolation.

## Steps

1. **Run the off-model scanner engine** for the perf category from the repo root:

   ```sh
   python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml \
       --category perf --json
   ```

   The scanners are **mechanical and make no LLM and no network-to-model calls** - the
   only subprocesses are the local tools declared in the adapter's `scanners:` section:
   - **Python:** `python -m radon cc -j .` surfaces high cyclomatic-complexity
     functions as hotspot/perf candidates.
   - **C++:** the `cppcheck` executable's performance-class checks.

2. **Consume the structured report.** Each finding is normalized to
   `{severity, category, file, line, rule, message, source_tool}`; the report carries
   `counts_by_severity`, the `gate` decision from `review.thresholds` in
   `.apia/config.yml`, and any `unavailable_tools`.

3. **Summarize and PRIORITIZE the REAL findings** by severity
   (critical > high > medium > low > info), highlighting the highest-complexity
   hotspots first. Cite `file:line`, the `rule`/`source_tool`, and the scanner
   `message`. **Never invent a finding** - complexity is a *candidate* hotspot, not a
   proven bottleneck; say so and do not overstate beyond what the scanner reports.

4. **Report missing scanners.** A `tool_unavailable` marker is surfaced as a warning;
   never a hard failure and never a fabricated finding.

5. **STOP at the gate.** Present the prioritized perf findings and wait; write no code
   changes. Hand off to the planner only on explicit approval.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- The scanner engine is deterministic tooling; the model only summarizes/prioritizes.
- Stay within the `review` token budget in `.apia/config.yml`.
