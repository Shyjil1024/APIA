---
name: apia-quality-scan
description: "APIA reviewer (QUALITY). Runs the off-model scanner engine .apia/scripts/scan.py for the quality category - coding-standard/lint, complexity, duplication, and dead-code checks (ruff/flake8/radon for Python; clang-tidy/cppcheck for C++) - then SUMMARIZES and PRIORITIZES the REAL findings grounded in scanner output + cited file:line evidence. The scanners are mechanical and make no LLM/network-to-model calls; the model only summarizes and prioritizes, and NEVER invents a finding. A missing scanner is reported (doctor), never faked. Use when the user asks for a code-quality review, lint pass, complexity/maintainability check, or dead-code audit. Runs in a forked context for token isolation; STOPS at the human-approval gate."
context: fork
---

# apia-quality-scan

Review a repo's **code quality** from grounded scanner evidence, not guesswork. This
stage runs in a **forked context** for token isolation.

## Steps

1. **Run the off-model scanner engine** for the quality category from the repo root:

   ```sh
   python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml \
       --category quality --json
   ```

   The scanners are **mechanical and make no LLM and no network-to-model calls** - the
   only subprocesses are the local tools declared in the adapter's `scanners:` section:
   - **Python:** `python -m ruff check --output-format json .` (and/or flake8) for
     coding-standard/lint and dead-code, plus `python -m radon cc -j .` for complexity.
   - **C++:** `clang-tidy` and the `cppcheck` executable for lint/complexity/dead-code.

2. **Consume the structured report.** Each finding is normalized to
   `{severity, category, file, line, rule, message, source_tool}`; the report carries
   `counts_by_category`, the `gate` decision from `review.thresholds` in
   `.apia/config.yml`, and any `unavailable_tools`.

3. **Summarize and PRIORITIZE the REAL findings** by severity
   (critical > high > medium > low > info), grouping lint vs. complexity vs. dead-code.
   Cite `file:line`, the `rule`/`source_tool`, and the scanner `message`. **Never invent
   a finding** - every claim traces to the report or a real file read.

4. **Report missing scanners.** A `tool_unavailable` marker is surfaced as a warning;
   never a hard failure and never a fabricated finding.

5. **STOP at the gate.** Present the prioritized quality findings and wait; write no
   code changes. Hand off to the planner only on explicit approval.

## Constraints

- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- The scanner engine is deterministic tooling; the model only summarizes/prioritizes.
- Stay within the `review` token budget in `.apia/config.yml`.
