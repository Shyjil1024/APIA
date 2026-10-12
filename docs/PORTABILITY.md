# APIA portability matrix

Which Copilot customization files are honored by which host. This is the basis
for APIA's layered graceful-degradation strategy (see `docs/ARCHITECTURE.md`
section 2).

**Verified on: 2026-10-12.** Copilot's customization surface changes frequently.
**Re-check this matrix on every Copilot release**; treat it as a snapshot, not a
guarantee.

## Verified mechanism matrix

| Mechanism | File / location | VS Code | Visual Studio | JetBrains | GitHub.com / cloud agent / CLI | Notes |
|---|---|---|---|---|---|---|
| Repo-wide custom instructions | `.github/copilot-instructions.md` | Yes | Yes | Yes | Yes | **Universal baseline.** Always-on, sent with every request. |
| Path-specific instructions | `.github/instructions/*.instructions.md` (`applyTo` glob) | Yes | Yes | **No** | Yes | **Not honored by JetBrains.** |
| Agent instructions | `AGENTS.md` (nearest in tree wins) | Yes (Agent Host) | - | - | Yes | Cross-agent format. |
| Prompt files | `*.prompt.md` (`.github/prompts/`) | Yes (Local; **deprecated in Agent Host**) | Yes | Yes | - | Being migrated to Agent Skills in the VS Code Agent Host. |
| **Agent Skills** | `SKILL.md` in `.github/skills/` (also `.claude/skills/`, `.agents/skills/`) | Yes | - | **No** | Copilot CLI, app, cloud agent, Codex | **Open standard** (agentskills.io). **Not honored by JetBrains** (see caveats). Progressive 3-stage disclosure = token-efficient. `context: fork` runs in a subagent. |
| Custom agents | `*.agent.md` in `.github/agents/` | Yes | - | - | Org-level | Per-role tools/model, `handoffs`, `mcp-servers`. |

## What this means for APIA's tiers

- **Tier 0 - Universal (every IDE + GitHub.com):** `.github/copilot-instructions.md`
  carries the full core protocol (the loop, approval gates, clarify discipline,
  token budget, stack preferences). This alone makes the loop run everywhere,
  including JetBrains.
- **Tier 1 - Portable prompt entrypoints (VS Code, Visual Studio, JetBrains):**
  `*.prompt.md` slash-commands give explicit per-stage entrypoints where Agent
  Skills are not yet honored. (Deprecated in the VS Code Agent Host, which prefers
  skills.)
- **Tier 2 - Rich skill package (VS Code Agent Host):** `SKILL.md` skills with
  `context: fork` for heavy, token-isolated stages, plus custom agents
  (`*.agent.md`) whose `handoffs` implement the approval gates as UI buttons.

## Host-specific caveats

- **JetBrains** supports **only** `.github/copilot-instructions.md` and prompt
  files. It does **not** honor `.github/instructions/*.instructions.md`, Agent
  Skills, or custom agents. The Tier-0 file must therefore be self-sufficient.
- **VS Code Agent Host** is the Phase-1 primary host (richest surface) and
  **deprecates prompt files** in favor of Agent Skills.
- **Agent Skills `name` must equal the parent directory name** and contain only
  lowercase letters, numbers, and hyphens, or the skill silently fails to load.
