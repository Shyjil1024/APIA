# APIA LOCAL plugins (Phase-6a)

This directory holds **team-provided LOCAL plugins** that extend APIA's
capabilities. Plugins are **committable files shared via the git repo** -- there
is **no download, no fetch, no marketplace, no network**. The off-model plugin
loader in `.apia/scripts/share.py` (`discover_plugins()` / `load_plugins()`)
scans this directory and the existing engines (`ingest` / `scan` / `scaffold` /
`verify`) consult it. Discovery is **REPO-FIRST**: a consuming repo's own
`.apia/plugins/` is scanned first, then the APIA package's `.apia/plugins/`.

A **malformed** plugin (bad YAML / missing required keys) is captured in the
loader's `errors` list and **skipped** -- the loader never raises and the engines
keep working. When this directory is absent or empty, every engine behaves
**exactly as before** (plugins are strictly additive).

## Layout

```
.apia/plugins/
  adapters/<stack>.yml    # a new/overriding stack adapter (same schema as
                          #   .apia/adapters/*.yml; see adapters/README.md)
  scanners/<name>.yml     # a scanner that EXTENDS an adapter's scanners: contract
  shapes/<name>.yml       # a shape plugin naming a new scaffold shape id
```

### Adapter plugin (`adapters/<stack>.yml`)

A YAML consistent with the `.apia/adapters/README.md` adapter schema. Required
key: `name` (the stack id). `ingest` folds the stack into detection when its
declared `extensions`/`manifests` have evidence in the repo; `scan` and `verify`
resolve `name` to this file.

### Scanner plugin (`scanners/<name>.yml`)

Extends an adapter's `scanners:` contract. Required keys:

| Key | Meaning |
|---|---|
| `stack` | The adapter/stack name this scanner extends (e.g. `python`). |
| `category` | `security` \| `quality` \| `perf`. |
| `scanner` | A scanner def `{tool, command, format}` (same contract as an adapter `scanners:` entry). |

`scan` folds the plugin scanner into the stack's scanner enumeration.

### Shape plugin (`shapes/<name>.yml`)

Names a new scaffold shape id. Required key: `shape` (the id). `scaffold`'s
`resolve_supported_shapes()` includes it in the supported-shapes list.

## Examples

The files shipped here (`example-*.yml`) are clearly-labeled EXAMPLES that make
the mechanism demonstrable and testable. Remove or replace them with your own.
