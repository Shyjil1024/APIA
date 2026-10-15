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
