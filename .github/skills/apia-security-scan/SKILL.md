---
name: apia-security-scan
description: "APIA reviewer (SECURITY). Runs the off-model scanner engine .apia/scripts/scan.py for the security category - dependency CVE/vulnerable-dependency audit (pip-audit) plus SAST-style vulnerability patterns (bandit for Python; cppcheck security checks for C++) - then SUMMARIZES and PRIORITIZES the REAL findings grounded in scanner output + cited file:line evidence. The scanners are mechanical and make no LLM/network-to-model calls; the model only summarizes and prioritizes, and NEVER invents a finding. A missing scanner is reported (doctor), never faked. Use when the user asks for a security review, vulnerability audit, CVE/dependency check, or SAST pass. Runs in a forked context for token isolation; STOPS at the human-approval gate."
context: fork
---

# apia-security-scan

Review a repo's **security** posture from grounded scanner evidence, not guesswork.
This stage runs in a **forked context** for token isolation.

## Steps

1. **Run the off-model scanner engine** for the security category from the repo root:

   ```sh
   python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml \
       --category security --json
   ```

   The scanners are **mechanical and make no LLM and no network-to-model calls** - the
   only subprocesses are the local tools declared in the adapter's `scanners:` section:
   - **Python:** `python -m pip_audit -f json` (dependency CVE / vulnerable-dependency
     audit) and `python -m bandit -r . -f json` (SAST-style vulnerability patterns).
   - **C++:** the `cppcheck` executable (`cppcheck --enable=all --xml --xml-version=2`),
     whose security-class findings self-categorize.

   > A dependency auditor like pip-audit fetches its advisory DB over the network; that
   > is the SCANNER tool's behavior. `scan.py` itself makes zero model/network-to-model
   > calls.

2. **Consume the structured report.** Each finding is normalized to the canonical shape
   `{severity, category, file, line, rule, message, source_tool}`; the report also
   carries `counts_by_severity`, the `gate` decision from `review.thresholds` in
   `.apia/config.yml` (security is capped at 0 by default - any known vulnerability
   fails the gate), and any `unavailable_tools`.

3. **Summarize and PRIORITIZE the REAL findings** by severity
   (critical > high > medium > low > info). For each, cite `file:line`, the
   `rule`/`source_tool`, and the scanner `message`. **Never invent a finding**; every
   claim traces to the report or a real file read. Fetch CVE detail via tool-use, never
   invent it.

4. **Report missing scanners.** A `tool_unavailable` marker means the tool is not
   installed - say so plainly (warning, not a hard failure); do not fabricate findings
   to fill the gap.

5. **STOP at the gate.** Present the prioritized security findings and wait; write no
   code changes. Hand off to the planner only on explicit approval.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- The scanner engine is deterministic tooling; the model only summarizes/prioritizes.
- Stay within the `review` token budget in `.apia/config.yml`.
