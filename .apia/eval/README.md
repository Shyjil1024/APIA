# apia-eval benchmark corpus

This directory holds the APIA effectiveness-measurement corpus: seven declared,
offline, deterministic scenarios (under `scenarios/<id>/scenario.yml`) that
exercise APIA's deterministic engines (`scan` / `evaldata` / `ingest`) against
fixed ground truth, plus the committed `baseline/` snapshot the harness writes.
This is the authoritative "metrics defined **before** the run" document: the
metric set and thresholds below were fixed ahead of the first scored run.

## What this measures (and what it does not)

- Measures the **deterministic tooling's** detect / verify / ingest / gate
  behavior on **fixed inputs only**, comparing each engine's structured output to
  a pre-declared `expected` block.
- Makes **NO** claim about the **AI/LLM generation step's** quality or about
  Copilot/model behavior.
- Fully **local / offline / deterministic**: **no network, no LLM** calls
  anywhere in the harness or corpus.
- Deliberately **small corpus** (7 scenarios) — honest, not statistically
  powered, and **not** a before/after study on real external projects.

## Metric set and "good" thresholds

Every metric is derived solely from an engine's structured output vs. the
declared `expected`; denominators count **scored** scenarios only (skipped
scenarios are excluded and counted separately).

| metric | computation | good |
|--------|-------------|------|
| **gate-correctness** (headline) | fraction of **scored** `scan` gate scenarios whose `gate.status` matches the declared `PASSED`/`FAILED`/`INCOMPLETE` (for `INCOMPLETE`, each declared name is a substring of a `gate.incomplete_reason` entry) | **3/3 = 100%**, reportable only when S1, S2 and S3 are all scored; otherwise the headline is the literal `degraded` |
| **defect-detection rate** | fraction of declared `rules_present` ids found in `scan` findings, aggregated detected/expected | 100% of seeded defects |
| **false-positive / false-negative counts** | FP = clean oracle that FAILED or surfaced a `rules_absent` id; FN = defect oracle that did not FAIL or missed a `rules_present` id | FP = 0 and FN = 0 (integer counts) |
| **verification-correctness** | S5 `golden_all_match == true` (accept) **and** S6 `golden_all_match == false` with `mismatch_count == 1` (reject); passes only if **both** hold | both pass (2/2) |
| **ingest-accuracy** | S7's three assertions: default ingest excludes `.apia/`; `--include-apia` includes the declared `.apia/scripts/**` paths; include-apia `test_count` exceeds default by the declared delta | 3/3 |

**Headline-degraded rule.** The `3/3` gate-correctness headline is reportable
**only** when all three gate scenarios were scored. If any gate scenario is
skipped (e.g. a required Python scanner is absent), `aggregate.headline.status`
is `"degraded"`, the headline is **not** a bare number, and the summary +
`baseline/BASELINE.md` list which scenarios were skipped and why. A `degraded`
headline is an honest "the full gate-correctness claim could not be measured in
this environment," never a pass.

## Running the harness

The run command uses the project's `.venv` interpreter, which installs the
scanners the scored scenarios need. Bootstrap it **once** from the repo root:

```sh
# one-time environment bootstrap (creates the interpreter the run uses + scanners)
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # pinned PyYAML + bandit/pip-audit/ruff/flake8/radon
```

Then the single documented run command:

```sh
.venv/bin/python .apia/scripts/evalbench.py --out .apia/eval/baseline/latest.json
```

This discovers the corpus, runs the engines in-process, scores each scenario,
prints a human summary, and writes the JSON results to `--out`. `--out` defaults
to `.apia/eval/baseline/latest.json` (a working, non-committed output); only
`baseline/baseline.json` is the committed snapshot. Flags: `--json-only`
suppresses the human summary; `--summary-only` suppresses the JSON file (and may
not be combined with `--out`).

**System scanners (not pip).** `cppcheck` and `clang-tidy` are native/system
tools, not pip packages. The reference baseline is produced with `cppcheck`
**absent** so scenario S3's `INCOMPLETE` is honest; `clang-tidy` is
`required: false` in `cpp.yml`, so its presence or absence is immaterial.

### Exit codes

- **0** — every scored scenario passed.
- **1** — some scored scenario failed.
- **2** — harness/usage error (bad manifest, missing corpus, unwritable `--out`,
  argparse error).

Skipped scenarios do not by themselves cause a non-zero exit, but a skipped gate
scenario forces the `degraded` headline described above.

## Reading the results

The JSON (`schema_version: 1`) has these top-level keys:

- `schema_version` — **check this and fail loud on an unrecognized value.**
- `tool_env` — `python` version plus `scanners_present` / `scanners_absent`,
  built from real runtime probes (never hardcoded).
- `scope_note` — the scope boundary string (identical to the summary footer).
- `scenarios` — one object per scenario: `id`, `engine`, `metric`, `status`
  (`scored` / `skipped`), `passed`, `skip_reason`, the declared `expected`, the
  observed `actual`, and the per-key `checks`.
- `aggregate` — the metrics above plus `scenarios_total` / `_scored` / `_passed`
  / `_failed` / `_skipped`, `scenarios_skipped_detail`, and the `headline`
  (`status`, `gate_correctness`, `corpus_pass_rate`).
- `generated_at` and any absolute-path fields are present but **non-stable** and
  excluded from the byte-stability guarantee.

The human summary mirrors `scan.format_human`: one line per scenario, then an
aggregate block printing each metric as `value (scored/declared)` against its
"good" threshold, the headline (a number when `full`, or the literal `degraded`
with the skipped scenarios listed), the present/absent tool sets, and the scope
note.

## The seven scenarios

| id | engine | metric | seeded condition |
|----|--------|--------|------------------|
| `gate_fail_pyapp` | scan | gate-correctness, defect-detection, FN | `samples/pyapp` with a `B602`/`B105` defect + a vulnerable requirement → gate `FAILED` |
| `gate_pass_clean` | scan | gate-correctness, FP | a scanner-clean fixture → gate `PASSED`, no findings (the clean oracle) |
| `gate_incomplete_missing_scanner` | scan | gate-correctness | C++ fixture with the required `cppcheck` absent → gate `INCOMPLETE` (`cppcheck (unavailable)`) |
| `defect_detection_bandit` | scan | defect-detection | seeded `B602` + `B105` must appear in findings |
| `verify_golden_accept` | evaldata | verification-correctness | known-good artifacts match the golden → accept |
| `verify_golden_reject` | evaldata | verification-correctness | one perturbed artifact → exactly one golden mismatch → reject |
| `ingest_include_apia` | ingest | ingest-accuracy | default ingest excludes `.apia/`; `--include-apia` adds the declared `.apia/scripts/**` paths and raises `test_count` by the declared delta |

## Baseline

The committed reference snapshot is `baseline/baseline.json`, interpreted in
`baseline/BASELINE.md`. Under the reference environment (the five Python scanners
present, `cppcheck` absent) the headline reads `full` with gate-correctness 3/3.
No wording here claims any effectiveness the committed baseline does not
demonstrate.

# Context-efficiency benchmark (apia-tokenbench)

`apia-tokenbench` is a **separate, sibling** benchmark (its own
`.apia/scripts/tokenbench.py` runner and `.apia/eval/tokenbench/` artifact tree). It
substantiates APIA's "minimize token/credit usage" design driver with a repeatable,
honestly scoped **context-size proxy** — it does **not** reuse the `apia-eval`
scenario/scoring machinery and does **not** modify `evalbench.py`.

## What it measures (and what it does not)

- Measures a deterministic **context-size proxy**: for each sample project, the
  repository context a model would consume under two approaches —
  - a **naive full-context baseline**: every tracked, non-binary source file
    concatenated, and
  - APIA's **retrieve-over-re-reading** approach: its distilled
    `.apia/knowledge/*.md` base (produced by an actual `ingest.ingest()` run) plus a
    bounded, representative **retrieved slice**.
- Reports the **reduction** from baseline to APIA in **bytes** and
  **tokenizer-estimated tokens**, each as an absolute figure and a percentage, per
  project and in a pooled aggregate.
- Makes **NO** claim about real **billed Copilot tokens**, credits, or dollars; the
  figures are a proxy for APIA's context-reduction mechanism, not a billing
  measurement.
- Fully **local / offline / deterministic**: **no network, no LLM** anywhere.

## The metric

- **bytes/token reduction**, per project and aggregate. Larger reduction = APIA
  consumes less context.
- **Aggregate = pooled totals**: `Σ baseline` vs `Σ APIA`, one pooled percentage per
  metric (never a mean of per-project percentages — that would weight a tiny project
  the same as a large one and be trivially gamed). Per-project percentages are still
  reported for transparency.

## Shared file universe (anti-rigging invariant)

The baseline and the APIA knowledge-base input are measured over the **same file
universe under the same ignore rules**, obtained from a **single**
`ingest.walk_repo(repo, include_apia=…)` call per project (reusing
`ingest.DEFAULT_IGNORES` and the minimal `.gitignore` handling — the benchmark does
**not** reimplement ignore rules). A benchmark-only text/binary filter
(`is_countable`: an extension blocklist + a NUL-byte sniff) is applied **identically**
to both sides and the slice. For the `apia_self` case (`include_apia=True`) the
benchmark's own `.apia/eval/tokenbench/` artifact tree is carved out of that single
universe before either side counts, so the self figure never measures its own output
and is reproducible from the committed tree.

## The retrieval-slice rule (bounded, documented, non-cherry-picked)

The retrieved slice stands in for the files APIA would load for a *typical* task,
having first consulted the distilled knowledge base. It is selected by a fixed,
project-independent rule over the countable universe, capped at **N = 8**
(`RETRIEVAL_SLICE_MAX`), in ordered tiers:

1. **Primary entry points** — basename in `{main.py, __main__.py, app.py, cli.py,
   main.cpp, report_main.cpp}` (lexicographic).
2. **Public surface** — top-level `__init__.py` and `include/*.h` / `*.hpp`
   (lexicographic).
3. **One representative test per module group** — for each top-level group from
   `ingest.map_modules`, the lexicographically-first member that is also an
   `ingest.discover_tests` path.
4. **Fill** — remaining countable sources, shortest path-depth first then
   lexicographic.

The first N across tiers (in tier order) are kept, then re-sorted lexicographically
for stable output. The rule is applied identically to every project and is **not**
hand-tuned per project. A real task may read **more or fewer** files than this fixed
slice; the slice is a single representative stand-in, not a per-task claim.

## Tokenizer (deterministic, recorded)

Token counts come from `tiktoken` (encoding `cl100k_base`) when it is importable,
otherwise a documented dependency-free heuristic `ceil(utf-8 bytes / 4)`. The output
**records which was used** (`tokenizer` + `tiktoken_present`). `tiktoken` is an
**optional** dev dependency (`requirements-dev.txt`); its absence degrades gracefully
and is **not** required for the suite to pass. Counts are deterministic for a given
tokenizer; two runs are only byte-comparable when the recorded identifier matches.

## Running the harness

```sh
.venv/bin/python .apia/scripts/tokenbench.py --out .apia/eval/tokenbench/latest.json
```

`--out` defaults to `.apia/eval/tokenbench/latest.json` (a working, non-committed
output); only `.apia/eval/tokenbench/baseline.json` is the committed snapshot. Flags:
`--json-only` suppresses the human summary (the JSON file is still written and also
echoed to stdout); `--summary-only` suppresses the JSON file (and may not be combined
with `--out`).

### Exit codes

- **0** — success.
- **2** — harness/usage/config error (bad flag combination, missing corpus path,
  unwritable `--out`, or an ingest/artifact failure — a project is never silently
  dropped).

There is **no exit 1**: the benchmark reports numbers, it does not pass/fail a corpus.

## Reading the results

The JSON (`schema_version: 1`) has these top-level keys:

- `schema_version` — **check this and fail loud on an unrecognized value.**
- `tokenizer` and `tiktoken_present` — the tokenizer identity actually used.
- `retrieval_slice` — the slice `rule` id and `max_files` bound.
- `scope_note` — the scope boundary string (identical to the summary footer).
- `projects` — one object per project: `label`, `repo` (repo-relative),
  `include_apia`, `baseline` (bytes/tokens/files), `apia`
  (bytes/tokens/files + nested `knowledge` and `slice`, where
  `apia.bytes = knowledge.bytes + slice.bytes` — two disjoint parts, no de-dup), and
  `reduction` (bytes/tokens, abs + pct).
- `aggregate` — pooled `baseline` / `apia` totals, the pooled `reduction`, and
  `method: "pooled-totals"`.
- `generated_at` is the only **non-stable** field (excluded from the byte-stability
  guarantee); `repo` paths are stored repo-relative so the rest is stable.

The human summary mirrors `scan.format_human`: one line per project
(baseline → APIA, bytes% and tokens%), a pooled aggregate block, the recorded
tokenizer, and the scope note as a footer.

## Baseline

The committed reference snapshot is `tokenbench/baseline.json`, interpreted in
`tokenbench/BASELINE.md`. It records `"tokenizer": "heuristic:ceil(len_utf8_bytes/4)"`
and `"tiktoken_present": false` (the reference environment has no `tiktoken`). Every
number in `BASELINE.md` is explained against that snapshot's own fields.

**Scope boundary.** apia-tokenbench measures a context-size **proxy** only — the
tokens are estimates, **NOT billed** Copilot tokens, with no token/credit/dollar
savings guaranteed; fully offline, **no network, no LLM**.
