"""End-to-end proof of the APIA Phase-1 loop (FEAT-004).

This test IS the objective Phase-1 proof that the ingest -> ... -> test stages
work end-to-end on the sample repos WITHOUT a live Copilot session. It exercises
both the green-baseline path (samples/pyapp) and the legacy no-tests
characterization path (samples/legacy_pyapp):

  samples/pyapp:
    ingest -> knowledge artifacts exist -> verify.py baseline green -> the worked
    example change is present -> verify.py reports no regression + new test green.

  samples/legacy_pyapp:
    ingest -> characterization tests present and green (they pin current behavior)
    -> a safe change is present -> characterization tests still pass + a new test
    for the added behavior passes.

Design notes:
  - ingest.py and verify.py are pure off-model modules (no LLM/network calls).
  - verify.py drives the stack adapter's LOCAL test command via subprocess with
    the sample repo as cwd, so pyapp/legacy_pyapp's own pyproject pythonpath=["."]
    applies regardless of the OUTER pytest rootdir running this file.
  - Ingestion writes to tmp_path so the committed .apia/knowledge/ is untouched.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

# Make ingest.py and verify.py importable regardless of pytest's rootdir / cwd.
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest  # noqa: E402
import verify  # noqa: E402
import evaldata  # noqa: E402
import evalloop  # noqa: E402

# Repo root is three levels up: .apia/scripts/tests/ -> repo.
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
LEGACY = os.path.join(REPO_ROOT, "samples", "legacy_pyapp")
ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")


# --- shared assertions ------------------------------------------------------


def _ingest_and_check_artifacts(repo_path, tmp_path):
    """Run ingest into tmp_path and assert all knowledge artifacts exist."""
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(repo_path, kd)
    assert result["reprocessed"] is True
    assert result["stacks"] == ["python"]
    for artifact in ingest.ARTIFACTS:
        path = os.path.join(kd, artifact)
        assert os.path.isfile(path) and os.path.getsize(path) > 0, (
            "missing/empty artifact: {}".format(artifact)
        )
    return kd, result


def _phase(summary, name):
    for p in summary["phases"]:
        if p["phase"] == name:
            return p
    raise AssertionError("phase {!r} not in summary".format(name))


# --- samples/pyapp: ingest -> verify baseline -> no regression + new green ---


def test_e2e_pyapp_ingest_then_verify_regression_and_new():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        # Stage 1 INGEST: knowledge artifacts are produced off-model.
        tmp = _TmpPath(td)
        kd, _ = _ingest_and_check_artifacts(PYAPP, tmp)
        # The worked example change is present on disk: multiply() is a new fn.
        test_inv = os.path.join(kd, "test-inventory.md")
        with open(test_inv, "r", encoding="utf-8") as fh:
            inventory_text = fh.read()
        assert "test_multiply.py" in inventory_text, (
            "ingest did not see the worked-example new test file"
        )

    # Stage 4 TEST via the harness: baseline + regression + new behavior.
    summary = verify.verify(
        PYAPP,
        adapter_path=ADAPTER,
        new_tests=["tests/test_multiply.py"],
    )
    assert summary["ok"] is True, summary
    assert summary["had_no_tests_before"] is False  # pyapp ships with tests

    baseline = _phase(summary, "baseline")
    regression = _phase(summary, "regression")
    new = _phase(summary, "new")

    assert baseline["passed"] and baseline["counts"]["passed"] >= 4
    # Regression: existing suite still green with the change present.
    assert regression["passed"] and regression["counts"]["failed"] == 0
    # New behavior for multiply() is verified.
    assert new["passed"] and new["counts"]["passed"] >= 1
    assert new["counts"]["failed"] == 0


# --- samples/legacy_pyapp: ingest -> characterization green -> safe change ---


def test_e2e_legacy_characterization_then_safe_change():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = _TmpPath(td)
        kd, result = _ingest_and_check_artifacts(LEGACY, tmp)
        # Characterization tests were generated under tests/ and are discoverable.
        assert result["test_count"] >= 1, "ingest should now see generated tests"
        test_inv = os.path.join(kd, "test-inventory.md")
        with open(test_inv, "r", encoding="utf-8") as fh:
            inventory_text = fh.read()
        assert "test_inventory_characterization.py" in inventory_text
        assert "test_reorder.py" in inventory_text

    # Characterization tests pin current behavior and must be green BEFORE/AFTER
    # the safe change; the new test proves the added behavior (reorder_quantity).
    summary = verify.verify(
        LEGACY,
        adapter_path=ADAPTER,
        new_tests=["tests/test_reorder.py"],
    )
    assert summary["ok"] is True, summary

    baseline = _phase(summary, "baseline")
    regression = _phase(summary, "regression")
    new = _phase(summary, "new")

    # The baseline here is the characterization suite + the new test file.
    assert baseline["passed"] and baseline["counts"]["failed"] == 0
    assert regression["passed"] and regression["counts"]["failed"] == 0
    assert new["passed"] and new["counts"]["passed"] >= 1
    assert new["counts"]["failed"] == 0


# --- the harness itself issues no network/LLM calls -------------------------


def test_verify_harness_has_no_network_or_llm_imports():
    verify_src = os.path.join(SCRIPTS_DIR, "verify.py")
    with open(verify_src, "r", encoding="utf-8") as fh:
        text = fh.read()
    lowered = text.lower()
    for forbidden in ("import requests", "import openai", "import anthropic",
                      "chat/completions", "api.openai.com"):
        # Allow mentions only inside clearly-prohibiting prose, never as imports.
        for i, line in enumerate(text.splitlines(), 1):
            if forbidden in line.lower():
                ll = line.lower()
                assert ("never" in ll or "no " in ll or "not " in ll
                        or "zero" in ll or "forbidden" in ll), (
                    "possible network/LLM usage at verify.py:{}: {}".format(i, line)
                )


class _TmpPath:
    """Tiny os.path.join-friendly wrapper so helpers can accept tmp dirs.

    Mirrors the subset of pathlib used here (``tmp / "name"``) without importing
    pathlib semantics into the shared helper signature.
    """

    def __init__(self, base):
        self._base = str(base)

    def __truediv__(self, other):
        return os.path.join(self._base, str(other))


# ===========================================================================
# FEAT-004 BOTH-STACKS PROOF (Python side): golden + invariant +
# characterization + differential + full iterate-to-green, executed for REAL
# against the samples/pyapp producer and its COMMITTED golden artifacts.
#
# samples/pyapp ships:
#   * calc/report.py            -- a deterministic Layer-1 artifact producer.
#   * tests/golden/report.json  -- the COMMITTED golden JSON (the oracle).
#   * tests/golden/table.csv    -- the COMMITTED golden CSV (text-mode oracle).
# Produced (non-golden) artifacts are written to samples/pyapp/out/ which is
# gitignored, so only the sources + committed goldens are tracked.
# ===========================================================================

# The sample's own golden layout, mirrored by the committed python.yml adapter
# (golden_dir: tests/golden). We DEFINE the eval adapter inline so the demo pins
# the glob to the producer's out/ dir (keeping the committed tree the oracle)
# while reusing the real golden_dir + invariant check ids.
_PY_GOLDEN_JSON = os.path.join(PYAPP, "tests", "golden", "report.json")
_PY_GOLDEN_CSV = os.path.join(PYAPP, "tests", "golden", "table.csv")


def _produce_pyapp_artifacts(out_dir):
    """Run the REAL samples/pyapp producer to emit report.json + table.csv.

    Runs ``python -m calc.report`` with the sample as cwd so its own pyproject
    pythonpath=["."] resolves ``calc`` (consistent with the existing e2e
    pattern). Returns (json_path, csv_path, rows)."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "report.json")
    csv_path = os.path.join(out_dir, "table.csv")
    proc = subprocess.run(
        [sys.executable, "-m", "calc.report", json_path, csv_path],
        cwd=PYAPP, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    assert proc.returncode == 0, proc.stderr
    with open(json_path, "r", encoding="utf-8") as fh:
        rows = json.loads(fh.read())
    return json_path, csv_path, rows


def test_e2e_py_committed_golden_matches_fresh_production():
    """The COMMITTED golden under samples/pyapp/tests/golden/ byte-matches a
    freshly produced artifact, proving the producer is deterministic and the
    committed-golden path resolves produced-vs-golden in the real sample tree."""
    assert os.path.isfile(_PY_GOLDEN_JSON), "committed Python golden must exist"
    assert os.path.isfile(_PY_GOLDEN_CSV), "committed Python CSV golden must exist"

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out_dir = os.path.join(td, "out")
        produced_json, produced_csv, _rows = _produce_pyapp_artifacts(out_dir)

        # bytes mode (the language-neutral trust default): the freshly produced
        # JSON is byte-identical to the committed golden.
        bytes_cmp = evaldata.compare_artifact(produced_json, _PY_GOLDEN_JSON,
                                              "bytes")
        assert bytes_cmp["match"] is True, bytes_cmp
        assert bytes_cmp["produced_hash"] == bytes_cmp["golden_hash"]

        # json mode (natural for the report): structure matches too.
        json_cmp = evaldata.compare_artifact(produced_json, _PY_GOLDEN_JSON,
                                            "json")
        assert json_cmp["match"] is True, json_cmp

        # text mode (natural for the CSV table): newline-insensitive match.
        text_cmp = evaldata.compare_artifact(produced_csv, _PY_GOLDEN_CSV,
                                            "text")
        assert text_cmp["match"] is True, text_cmp


def test_e2e_py_mine_golden_resolves_committed_tree():
    """mine_golden reports match=True resolving the producer output against the
    committed golden tree, via a sample-local eval adapter (golden_dir points at
    the real committed tests/golden)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "pyapp")
        # Stage a repo copy that has the committed golden tree + a fresh out/.
        os.makedirs(os.path.join(repo, "tests"))
        shutil.copytree(os.path.join(PYAPP, "tests", "golden"),
                        os.path.join(repo, "tests", "golden"))
        _produce_pyapp_artifacts(os.path.join(repo, "out"))
        adapter = {
            "name": "python",
            "golden": {
                "artifact_glob": ["out/report.json"],
                "golden_dir": "tests/golden",
                "compare": "bytes",
            },
        }
        results = evaldata.mine_golden(repo, adapter)
        assert len(results) == 1 and results[0]["match"] is True, results
        assert results[0]["artifact"] == os.path.join("out", "report.json")


def test_e2e_py_shipped_adapter_golden_block_matches_all_artifacts():
    """The SHIPPED python.yml golden block (loaded via verify.load_adapter, NOT an
    inline fixture) mines BOTH committed goldens green.

    This is the regression guard for the per-glob compare fix: the shipped block
    globs out/**/*.json AND out/**/*.csv under one artifact_glob. With the OLD
    single global `compare: json` the CSV was run through the JSON parser and
    always reported match=False ("invalid JSON"), a spurious permanent golden
    failure. The shipped block now resolves the compare mode PER GLOB (json for
    the report, text for the CSV table), so mine_golden must report match=True
    for EVERY globbed artifact. This test FAILS against the old config and PASSES
    after the fix, so the shipped adapter's golden path is finally covered."""
    import tempfile

    adapter = verify.load_adapter(ADAPTER)  # the SHIPPED python.yml, not inline
    golden = adapter["golden"]
    # Guard the exact shipped shape so this stays a real shipped-adapter test.
    assert set(golden["artifact_glob"]) == {"out/**/*.json", "out/**/*.csv"}
    assert golden["golden_dir"] == "tests/golden"

    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "pyapp")
        # Stage the committed golden tree (the oracle) under golden_dir and emit
        # fresh produced artifacts into the globbed out/ dir from the REAL
        # producer, mirroring the shipped sample layout.
        os.makedirs(os.path.join(repo, "tests"))
        shutil.copytree(os.path.join(PYAPP, "tests", "golden"),
                        os.path.join(repo, "tests", "golden"))
        _produce_pyapp_artifacts(os.path.join(repo, "out"))

        results = evaldata.mine_golden(repo, adapter)
        by_artifact = {r["artifact"]: r for r in results}
        # Both globbed artifacts resolved and matched.
        assert set(by_artifact) == {
            os.path.join("out", "report.json"),
            os.path.join("out", "table.csv"),
        }, by_artifact
        assert all(r["match"] is True for r in results), results
        # The modes resolved per glob: json report, text CSV (the fix).
        assert by_artifact[os.path.join("out", "report.json")]["mode"] == "json"
        assert by_artifact[os.path.join("out", "table.csv")]["mode"] == "text"


def test_e2e_py_invariant_row_count_matches_input():
    """run_invariants proves a property on the REAL produced artifact: the row
    count equals the producer's declared input row count, with no NaNs and a
    JSON roundtrip identity."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        produced_json, _csv, rows = _produce_pyapp_artifacts(
            os.path.join(td, "out"))
        adapter = {
            "name": "python",
            "invariants": {"checks": ["row_count_matches_input", "no_nans",
                                      "roundtrip_identity"]},
        }
        results = evaldata.run_invariants(
            td, adapter,
            {"produced_path": produced_json, "input_row_count": len(rows)},
        )
        held = {c["check_id"]: c["held"] for c in results}
        assert held == {"row_count_matches_input": True, "no_nans": True,
                        "roundtrip_identity": True}, results


def test_e2e_py_characterization_freezes_and_stays_stable_across_safe_change():
    """Characterization harvest records the producer's current stdout and freezes
    it with a stable hash; a SAFE change (adding an unrelated pure function to
    calc.operations) leaves the frozen behavior byte-identical."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # Copy the sample sources so we can apply a safe change without touching
        # the committed tree.
        repo = os.path.join(td, "pyapp")
        shutil.copytree(PYAPP, repo,
                        ignore=shutil.ignore_patterns("__pycache__", "out",
                                                       ".pytest_cache"))
        adapter = {
            "name": "python",
            # capture_command runs the producer with no args -> prints the JSON.
            "characterization": {"capture_command": "python -m calc.report"},
        }
        first = evaldata.harvest_characterization(repo, adapter)
        assert first is not None and first["returncode"] == 0
        assert first["output_hash"]

        # SAFE change: append a NEW pure function; existing behavior untouched.
        ops = os.path.join(repo, "calc", "operations.py")
        with open(ops, "a", encoding="utf-8") as fh:
            fh.write("\n\ndef negate(a):\n    \"\"\"Safe added helper.\"\"\"\n"
                     "    return -a\n")

        after = evaldata.harvest_characterization(repo, adapter)
        assert after is not None
        # The frozen characterization is STABLE across the safe change.
        assert after["output_hash"] == first["output_hash"], (
            "a safe change must not alter the characterized behavior")


def test_e2e_py_differential_only_intended_artifact_changes():
    """Differential OLD-vs-NEW: produce two artifacts from the SAME inputs, then
    intentionally change ONE. The unchanged artifact stays byte-identical and
    only the intended one is flagged as changed."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        old_dir = os.path.join(td, "old")
        new_dir = os.path.join(td, "new")
        _produce_pyapp_artifacts(old_dir)
        _produce_pyapp_artifacts(new_dir)

        # Deterministic scripted change to ONLY the CSV in the NEW dir.
        new_csv = os.path.join(new_dir, "table.csv")
        with open(new_csv, "r", encoding="utf-8") as fh:
            text = fh.read()
        with open(new_csv, "w", encoding="utf-8") as fh:
            fh.write(text.replace("2,3,5,-1,6", "2,3,5,-1,7"))

        diff = evaldata.differential_artifacts(
            old_dir, new_dir, ["*.json", "*.csv"], "bytes")
        assert diff["unchanged"] == ["report.json"], diff
        assert [c["artifact"] for c in diff["changed"]] == ["table.csv"], diff


def test_e2e_py_full_iterate_to_green_with_committed_golden(tmp_path):
    """FULL Python iterate-to-green driven by the COMMITTED golden: start RED (a
    producer that emits a wrong artifact so the golden comparison fails), get the
    structured failure report, apply a DETERMINISTIC scripted fix, re-run GREEN,
    and prove the cap stops a persistently-red variant.

    The 'fix' is a scripted edit standing in for the LIVE model fix (exactly as
    PR #2 materialized worked-example changes); evalloop makes no model call."""
    # Stage a repo copy with the committed golden tree.
    repo = str(tmp_path / "pyapp")
    shutil.copytree(PYAPP, repo,
                    ignore=shutil.ignore_patterns("__pycache__", "out",
                                                   ".pytest_cache"))
    # Produce the artifact the golden mining will grade.
    out_dir = os.path.join(repo, "out")

    # verify.verify loads the real python.yml from adapter_path and runs the
    # sample's own (green) pytest suite; the eval adapter dict below drives the
    # Layer-1 golden mining, so the COMMITTED-golden comparison is the ONLY
    # thing that flips red/green across the cycle.
    adapter = {
        "name": "python",
        "golden": {
            "artifact_glob": ["out/report.json"],
            "golden_dir": "tests/golden",
            "compare": "bytes",
        },
    }

    def _produce(mangle=False):
        os.makedirs(out_dir, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, "-m", "calc.report",
             os.path.join(out_dir, "report.json")],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        assert proc.returncode == 0, proc.stderr
        if mangle:
            # Deliberately corrupt the produced artifact so it diverges from the
            # committed golden (the RED state).
            p = os.path.join(out_dir, "report.json")
            with open(p, "r", encoding="utf-8") as fh:
                data = fh.read()
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(data.replace('"sum":5', '"sum":999'))

    eval_options = {
        "golden": True,
        "characterization": False,
        "invariants": False,
    }
    opts = {"adapter_path": ADAPTER, "eval_options": eval_options,
            "new_tests": ["tests/test_multiply.py"]}

    # --- START RED: produce a WRONG artifact; golden comparison must fail. ---
    _produce(mangle=True)
    state = evalloop.LoopState()
    red = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert red["status"] == "needs_fix", red
    report = red["failure_report"]
    assert report["golden_failures"], report
    assert report["golden_failures"][0]["artifact"] == os.path.join(
        "out", "report.json")
    assert json.loads(json.dumps(report, default=str))
    assert state.iteration == 1

    # --- SCRIPTED FIX: re-run the real producer so the artifact matches. ---
    _produce(mangle=False)
    green = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=3), state)
    assert green["status"] == "green", green
    assert state.iteration == 2

    # --- Prove the cap stops a persistently-red golden mismatch. ---
    _produce(mangle=True)
    cap_state = evalloop.LoopState()
    decisions = []
    for _ in range(10):
        d = evalloop.evaluate(repo, adapter, dict(opts, max_iterations=2),
                              cap_state)
        decisions.append(d)
        if d["status"] == "stop":
            break
    assert decisions[-1]["status"] == "stop", decisions[-1]
    assert decisions[-1]["reason"] == "max_iterations_reached"
    assert cap_state.iteration == 2  # never exceeded the cap
