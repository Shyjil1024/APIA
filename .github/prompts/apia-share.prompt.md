---
description: "APIA SHARE (local, git-based): drive the four Phase-6a sharing capabilities with the off-model share.py - export/import a versioned KNOWLEDGE PACK (safe no-clobber merge that keeps a locally-corrected artifact), apply an ORG/TEAM PROFILE (deep-merged UNDER local config so LOCAL overrides win), discover LOCAL PLUGINS (adapters/scanners/shapes committed under .apia/plugins/), and build a CAPABILITY MANIFEST. Sharing happens VIA THE GIT REPO (committed files) exactly like .apia/knowledge/ - it is NOT a hosted platform, NOT cross-org networking, NOT a marketplace. Human-gated; strictly local; no external LLM. Works on Visual Studio 2022 as a single Tier-0/Tier-1 agent."
agent: apia-orchestrator
tools: ["search/codebase", "search/usages", "web/fetch"]
---

Run the APIA **SHARE** stage: let a team share APIA knowledge and capabilities **through
the git repo** - the committed files under `.apia/` - exactly like `.apia/knowledge/` is
already shared. This prompt carries the WHOLE sharing flow as a **single Tier-0/Tier-1
agent**, so it works on **Visual Studio 2022** and **JetBrains** too (those hosts honor
only Tier-0 instructions + Tier-1 prompts, not Tier-2 skills/agents - this one prompt
carries the whole flow). It drives the **read-only** `apia-orchestrator` surface, so its
tools match that declaration: no `edit/files`, no `run/terminal`.

The off-model `.apia/scripts/share.py` is **deterministic tooling**, never a second model;
it makes **no LLM and no network-to-model calls**, downloads nothing, and reaches no
remote endpoint. It does ALL the mechanical work (export/import/merge, profile layering,
plugin discovery, manifest). Copilot only **summarizes** the result and **decides at the
human gate**. We **cannot set model temperature.**

## 1. KNOWLEDGE PACK - export / import (versioned, SAFE no-clobber merge)

Export the committed knowledge artifacts (the 8 `ingest.ARTIFACTS` incl.
`project-facts.md` + the `.index.json`) into one portable, versioned pack
(`schema_version "1"`):

```sh
python3 .apia/scripts/share.py --export-pack --repo . --out pack.json --json
```

Import a pack into a target knowledge dir with a **SAFE merge**:

```sh
python3 .apia/scripts/share.py --import-pack --pack pack.json --json
# opt-in override of a divergent local copy (only when the human asks):
python3 .apia/scripts/share.py --import-pack --pack pack.json --force --json
```

Behavior to state plainly: a byte-identical or missing target is written freely; a target
that **diverges** from the pack is treated as a **local correction** - it is **KEPT**,
reported in `conflicts` + `skipped_local`, and **never silently clobbered**. `--force`
writes the pack copy over a divergent local file (recorded in `forced`). An
**unknown/newer** `schema_version` is **reported** (`unknown_version: true`) and **NOT
applied** - never forced. Summarize `imported` / `skipped_local` / `conflicts` and **STOP
at the human gate** before any `--force`.

## 2. ORG/TEAM PROFILE - apply (LOCAL overrides win)

A committable profile at `.apia/profile.yml` (example: `.apia/profiles/example-team.yml`)
uses the SAME keys as `.apia/config.yml` (no new top-level concepts). It is **deep-merged
UNDER** the local config: a key present locally **WINS**; a key absent locally is **filled
in** from the profile.

```sh
python3 .apia/scripts/share.py --apply-profile --repo . --json
python3 .apia/scripts/share.py --apply-profile --repo . --profile .apia/profiles/example-team.yml --json
```

Report the EFFECTIVE config + the `provenance` map (which keys came from `local` vs
`profile`) so the precedence is visible. STOP at the gate before adopting it.

## 3. LOCAL PLUGINS - adapters / scanners / shapes (committed, additive)

Team-provided LOCAL plugins live under a committable `.apia/plugins/{adapters,scanners,
shapes}/` dir (REPO-FIRST, then the APIA package). They are **discovered as committed
files** - no download, no fetch, no marketplace - and are strictly **additive** (an absent
dir changes nothing). The engines (`ingest`/`scan`/`scaffold`/`verify`) consult the loader
to pick up team adapters/scanners/shapes.

```sh
python3 .apia/scripts/share.py --plugins --repo . --json
```

A **malformed** plugin (bad YAML / missing required keys) is captured in `errors` and
**skipped** - the loader **never raises**. The plugin contract (where plugins live, the
required keys per kind, how each is wired in, malformed-plugin behavior) is documented in
`.apia/adapters/README.md` (and `.apia/plugins/README.md`); point the user there to add
one. Summarize the loaded plugins and any `errors`.

## 4. CAPABILITY MANIFEST - what this install can do

```sh
python3 .apia/scripts/share.py --manifest --repo . --json
```

Reuses `ingest.doctor()` + adapter/scanner/shape detection to report adapters
(complete vs stub: python/cpp complete, csharp/groovy stub), available scanners, supported
shapes, loaded plugins (incl. errors), and versions. Use it to answer "what can APIA do
here?" without guessing.

A cross-capability toolchain preflight is available too:

```sh
python3 .apia/scripts/share.py --doctor --repo .
```

## Honest framing

This is **LOCAL, git-based sharing**: teams share by **committing and pulling files**,
exactly like `.apia/knowledge/` is already shared. It is **NOT a hosted platform, NOT
cross-org networking, NOT a marketplace** - those are intentionally **out of scope**.
There is no server, no network service, no auth/multi-tenancy; nothing leaves the machine.

**Human-gated; strictly local.** STOP at the human gate before importing a pack (and
always before `--force`), adopting a profile, or acting on a manifest. APIA never
auto-pushes, opens a PR/MR, or runs CI/packaging/signing - the user commits and pushes.
**Never call an external LLM**; fetch external **data** only via MCP/tool-use. Do not dump
the whole repo; retrieve only the relevant slice.
