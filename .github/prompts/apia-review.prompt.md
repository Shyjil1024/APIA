---
description: "APIA reviewer: run the off-model scanner engine (scan.py) for Python AND C++, read its structured prioritized findings, then summarize and prioritize the REAL findings across security/quality/perf/limitations - never inventing one - report any missing scanner, and STOP at the human-approval gate. Works on Visual Studio 2022 as a single Tier-0/Tier-1 agent."
agent: apia-reviewer
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **REVIEW** stage. This prompt drives the **read-only** `apia-reviewer`
agent, so its tools match the agent's own declaration: no `edit/files`, no
`run/terminal`. It runs on **this** Copilot session as a **single Tier-0/Tier-1 agent**,
so it works on **Visual Studio 2022** too (VS 2022 honors only Tier-0 instructions +
Tier-1 prompts, not Tier-2 skills/agents - this one prompt carries the whole flow).

The scanner engine is **deterministic tooling**, never a second model; the off-model
`scan.py` makes **no LLM and no network-to-model calls**. The model's only job is to
**summarize, prioritize, and explain** the scanner's grounded output - it **never
invents a finding**.

1. **Run the off-model scanner engine** (a mechanical, pre-review step the host/user
   runs from the repo root). It works for **Python and C++** off the same per-stack
   adapter, so VS 2022 users get one flow for both stacks:

   ```sh
   python3 .apia/scripts/scan.py <repo> --adapter .apia/adapters/<stack>.yml \
       --category all --json
   ```

   The only subprocesses are the local scanners declared in the adapter's `scanners:`
   section:
   - **Python:** `python -m pip_audit -f json` (dependency CVE audit),
     `python -m bandit -r . -f json` (SAST), `python -m ruff check --output-format json .`
     / `python -m flake8` (lint/dead-code), `python -m radon cc -j .` (complexity/perf).
   - **C++:** the `cppcheck` executable (`cppcheck --enable=all --xml --xml-version=2`)
     and `clang-tidy` (not `python -m`; they are native executables).

   `python3 .apia/scripts/scan.py --doctor` previews scanner-tool presence offline
   (reusing `ingest.py`'s doctor), and `--category security|quality|perf|all` scopes the
   run. `--fail-on-severity LEVEL` overrides the config gate for a one-off.

2. **Read the structured, prioritized report.** Every finding is normalized to the
   canonical shape `{severity, category, file, line, rule, message, source_tool}`. The
   report also carries `counts_by_severity`, `counts_by_category`, the `gate` decision
   (`apply_thresholds` against `review.thresholds` in `.apia/config.yml`), and any
   `unavailable_tools`.

3. **Summarize and PRIORITIZE the REAL findings.** Group by category (security /
   quality / perf) and order by severity (critical > high > medium > low > info). For
   each finding cite its `file:line`, the `rule`/`source_tool`, and the scanner
   `message`. Then synthesize **limitations** (architectural limits, tech-debt,
   scalability risks) grounded in the `.apia/knowledge/` base plus the aggregated
   findings. **Never invent a finding**; every claim traces to the report or a real
   file read. Fetch external facts (e.g. CVE detail) via `web/fetch`, never invent them.

4. **Report missing scanners instead of guessing.** A `tool_unavailable` marker means
   the tool is not installed - say so plainly as a warning, never a hard failure and
   never a fabricated finding. If confidence in a finding or in prioritization is below
   the `confidence_threshold` in `.apia/config.yml`, ask **ONE specific, informed**
   question (what the scanner reported, what is uncertain, a best guess).

5. **STOP at the human-approval gate.** Present the prioritized findings report and
   wait; write **no code changes**. The `gate` block is a **composable** decision: this
   standalone reviewer reads it, and a future generation phase can call the reviewer as
   a GATE before accepting generated code - both read the SAME `review.thresholds`
   block. Only on explicit approval hand off to `apia-planner` to plan a fix.

Do not dump the whole repo; retrieve only the relevant slice. Do not call any external
LLM. Fetch external **data** only via MCP/tool-use.
