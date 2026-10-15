# apia-eval baseline interpretation

This file interprets the committed snapshot `baseline.json` in this directory. It
is written by reading that JSON, not from memory: the tool set below is
transcribed **verbatim** from the snapshot's `tool_env` block, and every number
is explained against the snapshot's own `aggregate`/`scenarios`.

> **Schema contract.** `baseline.json` carries `"schema_version": 1`. A reader
> (human or tool) must check that value and **fail loud** on any unrecognized
> version rather than guessing at the layout — the fields described here apply to
> schema version 1 only.

## Reference environment (`tool_env`, verbatim from `baseline.json`)

```json
"tool_env": {
  "python": "3.11.15",
  "scanners_present": [
    "bandit",
    "pip-audit",
    "ruff",
    "flake8",
    "radon",
    "clang-tidy"
  ],
  "scanners_absent": [
    "cppcheck"
  ]
}
```

- **Present:** `bandit`, `pip-audit`, `ruff`, `flake8`, `radon` — the five Python
  scanners the `python.yml` adapter requires. Their presence is why the Python
  gate/defect scenarios (S1, S2, S4) are **scored** rather than skipped.
- **`clang-tidy` present is immaterial to every verdict.** `cpp.yml` declares
  `clang-tidy` as `required: false`, so its presence or absence changes no gate
  outcome. It is listed here only because `tool_env` records what was actually
  probed; it is **not** evidence for or against any scored result. (We record it
  honestly as present rather than mis-stating it as absent.)
- **`cppcheck` absent — on purpose.** `cppcheck` is `required: true` in `cpp.yml`.
  Its deliberate absence is what makes scenario S3's `INCOMPLETE` verdict honest:
  the coverage-aware gate cannot complete a C++ review without its required
  scanner, so it returns `INCOMPLETE` (not a hollow `PASSED`). This is the
  behavior S3 is designed to measure. Installing `cppcheck` would instead make S3
  **skipped** (its negative precondition `!cppcheck` would be violated), not a
  false pass.

## Headline and what each number means

From `baseline.json` → `aggregate`:

- **Headline — gate-correctness: `1.00` (`status: "full"`), 3/3 scored.** Among
  the three gate scenarios (S1 `gate_fail_pyapp`, S2 `gate_pass_clean`,
  S3 `gate_incomplete_missing_scanner`), all three were **scored** and the gate
  returned the declared `FAILED` / `PASSED` / `INCOMPLETE` verdict. The headline
  is reportable as a number **only because all three gate scenarios were scored**;
  had any been skipped, the headline would read the literal `degraded`.
- **corpus_pass_rate: `1.00`** — every scored scenario's actual matched its
  declared `expected`. This drives the process exit code (exit 0).
- **defect_detection: `1.00` (3/3)** — every expected rule id across the
  defect-declaring scenarios appeared in the engine's findings (S1 `B602`;
  S4 `B602` + `B105`).
- **false_positive: `0`, false_negative: `0`** — the clean oracle (S2) did not
  spuriously fail, and the defect oracle (S1) was not missed. Reported as integer
  counts, never averaged.
- **verification_correctness: PASS (2/2)** — `accept: true` **and**
  `reject: true`: S5 accepts the matching golden artifacts and S6 rejects the
  perturbed one (`mismatch_count: 1`). Both sides must hold.
- **ingest_accuracy: `1.00` (1/1 scenario, 3/3 assertions)** — S7's three
  assertions all held: default ingest excludes `.apia/` paths, `--include-apia`
  includes the declared `.apia/scripts/**` paths, and the include-apia
  `test_count` exceeds the default by the declared delta of 1.
- **scenarios_total: 7, scenarios_scored: 7, scenarios_passed: 7,
  scenarios_failed: 0, scenarios_skipped: 0** (`scenarios_skipped_detail: []`).

## Per-scenario outcome

| id | engine | metric | result |
|----|--------|--------|--------|
| `defect_detection_bandit` | scan | defect_detection | **scored, pass** — findings include `B602`, `B105` |
| `gate_fail_pyapp` | scan | gate_correctness | **scored, pass** — gate `FAILED` as declared; `B602` present |
| `gate_incomplete_missing_scanner` | scan | gate_correctness | **scored, pass** — gate `INCOMPLETE`; `incomplete_reason` includes `cppcheck (unavailable)` |
| `gate_pass_clean` | scan | gate_correctness | **scored, pass** — gate `PASSED`, no findings |
| `ingest_include_apia` | ingest | ingest_accuracy | **scored, pass** — all three ingest assertions hold |
| `verify_golden_accept` | evaldata | verification_correctness | **scored, pass** — golden all-match |
| `verify_golden_reject` | evaldata | verification_correctness | **scored, pass** — one declared mismatch detected |

Nothing was skipped or failed in this reference run.

## What this baseline does NOT prove (scope boundary)

- It measures only APIA's **deterministic tooling** (gate / defect-detection /
  verify / ingest) on **fixed inputs** with known ground truth. It makes **NO**
  claim about the quality of any **AI/LLM-generated code** or about Copilot/model
  behavior.
- The run is fully **local, offline, and deterministic**: no network and no LLM
  calls anywhere in the harness or corpus.
- The corpus is **deliberately small** (7 scenarios), chosen to cover each
  finalized metric at least once. It is honest, not statistically powered, and it
  is **not** a before/after study on real external projects.
- S3's `INCOMPLETE` reflects a **deliberately absent required scanner**
  (`cppcheck`). It is an honest coverage verdict, not a hollow pass.
