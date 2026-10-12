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

import os
import sys

import pytest

# Make ingest.py and verify.py importable regardless of pytest's rootdir / cwd.
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest  # noqa: E402
import verify  # noqa: E402

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
