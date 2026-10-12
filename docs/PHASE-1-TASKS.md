# Phase 1 - Task breakdown

Phase 1 delivers a minimal working APIA skill package for **VS Code** that proves the
`ingest -> clarify -> modify -> test -> apply` loop on **one sample Python repository**, with
portable Tier-0/Tier-1 fallbacks and stubs for C++/C#/Groovy.

Machine-readable task state lives in `.tasks/` (`task.json` + `features/FEAT-00N.json`). This
document is the human-readable companion.

## Toolchain (verified 2026-10-12)

Phase-1 scripts and sample repos use a local virtualenv. No external LLM SDK is installed or
allowed; model inference is the user's Copilot session only.

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install pytest
```

| Tool | Version |
|------|---------|
| Python | 3.9.25 |
| pytest | 8.4.2 |
| PyYAML | 6.0.3 (used only for validating/parsing `.apia/config.yml`) |

| ID | Type | Title | Depends on |
|----|------|-------|-----------|
| FEAT-001 | chore | Environment setup + portability verification + package scaffold + sample repo | - |
| FEAT-002 | feat | Off-model ingestion engine, cached knowledge base, Python stack adapter | FEAT-001 |
| FEAT-003 | feat | Tier-2 skills + custom agents implementing the gated loop; Tier-1 prompt entrypoints; Tier-0 instructions | FEAT-002 |
| FEAT-004 | feat | Test/verify loop: regression guard + new tests + legacy characterization path, proven on the sample | FEAT-002, FEAT-003 |

Key constraints every task must honor:

- **No external LLM API** anywhere in the package or scripts. Model inference is the user's Copilot
  session only. External *data* via MCP/Copilot tool-use only.
- **Portability**: Tier-0 `.github/copilot-instructions.md` must carry the full protocol so the loop
  works even in JetBrains (which supports only that single instructions file + prompt files).
- **SKILL.md `name` must equal its directory name**, lowercase/numbers/hyphens only, or it silently
  fails to load.
- **Token efficiency** and **anti-hallucination clarify gate** are first-class; see
  `docs/ARCHITECTURE.md` sections 5 and 6.
