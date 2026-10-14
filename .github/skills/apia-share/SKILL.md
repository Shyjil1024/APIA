---
name: apia-share
description: "APIA LOCAL SHARE stage. Drive the four Phase-6a sharing capabilities with the off-model .apia/scripts/share.py (no LLM/network): export/import a versioned KNOWLEDGE PACK with a SAFE no-clobber merge that keeps a locally-corrected artifact; apply an ORG/TEAM PROFILE deep-merged UNDER local config so LOCAL overrides win; discover LOCAL PLUGINS (adapters/scanners/shapes) committed under .apia/plugins/; and build a CAPABILITY MANIFEST reusing ingest.doctor + adapter/scanner/shape detection. share.py does ALL the deterministic export/import/merge/profile-layer/plugin-discovery/manifest work; Copilot only summarizes and decides at the human gate. Sharing happens VIA THE GIT REPO (committed files) exactly like .apia/knowledge/ - it is NOT a hosted platform, NOT cross-org networking, NOT a marketplace (intentionally out of scope). Human-gated; strictly local; no external LLM. Use to share APIA knowledge/capabilities across a team through git."
---

# apia-share

Share APIA knowledge and capabilities **through the git repo** - the committed files under
`.apia/` - exactly like `.apia/knowledge/` is already shared. Four capabilities, all driven
by the off-model `.apia/scripts/share.py`.

## Model vs off-model split (state it plainly)

- **Off-model (`share.py`, free, deterministic):** export/import a versioned knowledge pack
  and perform the **SAFE no-clobber merge** (a divergent local artifact is a local
  correction - kept and reported, never silently overwritten); load an org/team profile
  and **deep-merge it UNDER local config** (local wins); discover + validate **LOCAL
  plugins** (adapters/scanners/shapes) and expose them to the engines; and build the
  **capability manifest** reusing `ingest.doctor()` + adapter/scanner/shape detection. It
  calls **no LLM and no network-to-model**, downloads nothing, and spawns no subprocess of
  its own.
- **On-model (this Copilot session):** only **summarizing** the deterministic result and
  **deciding at the human gate** (import this pack? adopt this profile? use this plugin?).
  This stage **drives** share.py; it never reimplements the merge/precedence/discovery.

## Steps

1. **Export a knowledge pack** (the 8 `ingest.ARTIFACTS` incl. `project-facts.md` + the
   `.index.json`, `schema_version "1"`):

   ```sh
   python3 .apia/scripts/share.py --export-pack --repo . --out pack.json --json
   ```

2. **Import a pack with the SAFE merge.** A byte-identical or missing target is written
   freely; a **divergent** target is a local correction - KEPT, reported in `conflicts` +
   `skipped_local`, never silently clobbered. An **unknown/newer** `schema_version` is
   reported (`unknown_version: true`) and NOT applied. `--force` (opt-in only) writes the
   pack copy over a divergent local file (recorded in `forced`). **STOP at the gate** before
   any `--force`.

   ```sh
   python3 .apia/scripts/share.py --import-pack --pack pack.json --json
   python3 .apia/scripts/share.py --import-pack --pack pack.json --force --json
   ```

3. **Apply an org/team profile (LOCAL overrides win).** The profile at `.apia/profile.yml`
   (example: `.apia/profiles/example-team.yml`) uses the SAME keys as `.apia/config.yml`
   and is deep-merged UNDER local config. Report the effective config + the `provenance`
   map (`local` vs `profile`) so precedence is visible.

   ```sh
   python3 .apia/scripts/share.py --apply-profile --repo . --json
   ```

4. **Discover LOCAL plugins** under `.apia/plugins/{adapters,scanners,shapes}/` - committed
   files, strictly additive, no download/marketplace. A malformed plugin is captured in
   `errors` and skipped (the loader never raises). The plugin contract (locations, required
   keys per kind, wiring, malformed behavior) lives in `.apia/adapters/README.md` (and
   `.apia/plugins/README.md`).

   ```sh
   python3 .apia/scripts/share.py --plugins --repo . --json
   ```

5. **Build the capability manifest** to answer "what can APIA do here?" (adapters
   complete-vs-stub, scanners, shapes, loaded plugins incl. errors, versions):

   ```sh
   python3 .apia/scripts/share.py --manifest --repo . --json
   python3 .apia/scripts/share.py --doctor   --repo .
   ```

## Honest framing

- This is **LOCAL, git-based sharing**: teams share by **committing and pulling files**,
  exactly like `.apia/knowledge/`. It is **NOT a hosted platform, NOT cross-org
  networking, NOT a marketplace** - those are intentionally **out of scope**. No server,
  no network service, no auth/multi-tenancy; nothing leaves the machine.

## Constraints

- **Human-gated.** STOP at the human gate before importing a pack (always before
  `--force`), adopting a profile, or acting on a manifest.
- Never call an external/separate LLM API. External **data** only via MCP/tool-use.
- Strictly **local**: never auto-push, open a PR/MR, or run CI/packaging/signing. The user
  commits and pushes.
