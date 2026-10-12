"""Tests for the review-fix iteration (addressing 2026-10-12-054254-review.md).

Each test maps to a numbered review issue so the behavior that changed is pinned:

  1. Regression phase now proves a real before/after delta: a baseline captured
     with --baseline-only on the pre-change tree is compared against a post-change
     run, and a genuine regression (a broken existing test, or a removed test) is
     flagged.
  2. The baseline/existing-suite phases EXCLUDE the --new-tests target, so
     "existing tests" is genuinely the pre-change suite.
  3. A stub adapter (null test_command) is refused with a clear error instead of
     silently defaulting to pytest.
  4. The pyproject optional-dependencies parser handles MULTI-LINE arrays.
  5. ARCHITECTURE.md and PORTABILITY.md agree on the JetBrains/Agent-Skills cell.
  7. The docs state explicitly that all MCP wiring is deferred to Phase 2.
  8. discover_tests still finds conftest.py (dead-branch simplification kept the
     documented behavior); the ingest prompt no longer loosens the read-only
     planner (asserted in test_artifacts.py).

All subprocess-driven phases run the sample repos' OWN pytest via the adapter, in
tmp copies where needed, and never touch the committed samples.
"""

import os
import shutil
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest  # noqa: E402
import verify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
LEGACY = os.path.join(REPO_ROOT, "samples", "legacy_pyapp")
ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")
DOCS = os.path.join(REPO_ROOT, "docs")


def _phase(summary, name):
    for p in summary["phases"]:
        if p["phase"] == name:
            return p
    raise AssertionError("phase {!r} not in summary".format(name))


# --- Issue 1: regression delta is a real before/after comparison ------------


def _copy_pyapp(tmp_path):
    repo = tmp_path / "pyapp"
    shutil.copytree(PYAPP, str(repo))
    return str(repo)


def test_baseline_only_persists_counts_and_node_set(tmp_path):
    repo = _copy_pyapp(tmp_path)
    bl_file = str(tmp_path / "baseline.json")
    summary = verify.verify(
        repo, adapter_path=ADAPTER, new_tests=["tests/test_multiply.py"],
        baseline_only=True, baseline_file=bl_file,
    )
    assert summary["baseline_only"] is True
    # Only the baseline phase runs in baseline-only mode.
    assert [p["phase"] for p in summary["phases"]] == ["baseline"]
    assert os.path.isfile(bl_file), "baseline must be persisted to disk"

    stored = verify.load_baseline(bl_file)
    assert stored is not None
    # The new-test target is excluded, so the baseline captures the pre-change
    # suite (4 operations tests), not the committed new tests.
    assert stored["counts"]["passed"] == 4
    assert stored["nodes"], "baseline must record the collected node set"
    assert all("test_multiply" not in n for n in stored["nodes"]), (
        "new-test nodes must be excluded from the pre-change baseline"
    )


def test_regression_detects_broken_existing_test_vs_baseline(tmp_path):
    repo = _copy_pyapp(tmp_path)
    bl_file = str(tmp_path / "baseline.json")

    # Capture the GREEN pre-change baseline.
    verify.verify(repo, adapter_path=ADAPTER, new_tests=["tests/test_multiply.py"],
                  baseline_only=True, baseline_file=bl_file)

    # Introduce a real regression: break add() so an EXISTING test fails.
    ops = os.path.join(repo, "calc", "operations.py")
    with open(ops, "r", encoding="utf-8") as fh:
        src = fh.read()
    with open(ops, "w", encoding="utf-8") as fh:
        fh.write(src.replace("    return a + b", "    return a - b  # BUG"))

    summary = verify.verify(repo, adapter_path=ADAPTER,
                            new_tests=["tests/test_multiply.py"], baseline_file=bl_file)
    assert summary["baseline_compared"] is True
    delta = summary["regression_delta"]
    assert delta is not None and delta["regressed"] is True
    assert delta["passed_delta"] < 0, "fewer existing tests pass than baseline"
    regression = _phase(summary, "regression")
    assert regression["passed"] is False
    assert summary["ok"] is False


def test_regression_detects_removed_test_vs_baseline(tmp_path):
    repo = _copy_pyapp(tmp_path)
    bl_file = str(tmp_path / "baseline.json")

    verify.verify(repo, adapter_path=ADAPTER, new_tests=["tests/test_multiply.py"],
                  baseline_only=True, baseline_file=bl_file)

    # Delete a pre-change test file: coverage silently disappears.
    os.remove(os.path.join(repo, "tests", "test_operations.py"))

    summary = verify.verify(repo, adapter_path=ADAPTER,
                            new_tests=["tests/test_multiply.py"], baseline_file=bl_file)
    delta = summary["regression_delta"]
    assert delta is not None
    assert delta["removed_nodes"], "a removed pre-change test must be flagged"
    assert delta["regressed"] is True
    assert summary["ok"] is False


def test_regression_green_when_change_preserves_behavior(tmp_path):
    repo = _copy_pyapp(tmp_path)
    bl_file = str(tmp_path / "baseline.json")

    verify.verify(repo, adapter_path=ADAPTER, new_tests=["tests/test_multiply.py"],
                  baseline_only=True, baseline_file=bl_file)

    # A behavior-preserving change (add a brand new function) must NOT regress.
    ops = os.path.join(repo, "calc", "operations.py")
    with open(ops, "a", encoding="utf-8") as fh:
        fh.write("\n\ndef negate(a):\n    return -a\n")

    summary = verify.verify(repo, adapter_path=ADAPTER,
                            new_tests=["tests/test_multiply.py"], baseline_file=bl_file)
    delta = summary["regression_delta"]
    assert delta is not None and delta["regressed"] is False
    assert delta["passed_delta"] == 0
    assert summary["ok"] is True


# --- Issue 6: the zero-tests trigger fires on an actually-untested tree -----


def _legacy_copy_without_tests(tmp_path):
    """Copy legacy_pyapp into tmp and REMOVE its committed tests/ dir.

    The shipped legacy_pyapp has characterization tests committed, so
    had_no_tests_before is false for it. To observe the zero-tests signal we must
    exercise an actually-untested tree, which is exactly the pre-snapshot state
    the characterization path is designed to catch.
    """
    repo = tmp_path / "legacy_untested"
    shutil.copytree(LEGACY, str(repo))
    shutil.rmtree(os.path.join(str(repo), "tests"))
    # Drop any cached pyc so collection genuinely finds nothing.
    for dirpath, dirnames, _ in os.walk(str(repo)):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))
    return str(repo)


def test_detect_no_tests_true_on_untested_tree(tmp_path):
    repo = _legacy_copy_without_tests(tmp_path)
    adapter = verify.load_adapter(ADAPTER)
    assert verify.detect_no_tests(adapter, repo) is True, (
        "zero-tests trigger must fire on a tree with no collectable tests"
    )


def test_verify_reports_had_no_tests_before_on_untested_tree(tmp_path):
    repo = _legacy_copy_without_tests(tmp_path)
    summary = verify.verify(repo, adapter_path=ADAPTER)
    assert summary["had_no_tests_before"] is True, (
        "verify must surface the legacy/characterization signal on an untested tree"
    )
    # Fail-closed: an empty baseline is not treated as a pass.
    baseline = _phase(summary, "baseline")
    assert baseline["counts"]["no_tests"] is True
    assert baseline["passed"] is False
    assert summary["ok"] is False


def test_committed_legacy_sample_has_tests(tmp_path):
    """Contrast: the shipped sample DOES have tests (trigger not observable
    there), which is why the untested-tree tests above exist."""
    adapter = verify.load_adapter(ADAPTER)
    assert verify.detect_no_tests(adapter, LEGACY) is False


# --- Issue 2: baseline/regression exclude the new-test target ---------------


def test_baseline_excludes_new_test_target(tmp_path):
    repo = _copy_pyapp(tmp_path)
    summary = verify.verify(repo, adapter_path=ADAPTER,
                            new_tests=["tests/test_multiply.py"])
    baseline = _phase(summary, "baseline")
    # pyapp ships 4 operations tests + 3 multiply tests = 7 total; with the new
    # tests excluded the existing suite must be exactly the 4 pre-change tests.
    assert baseline["counts"]["passed"] == 4, (
        "baseline must exclude the committed new-test file"
    )
    new = _phase(summary, "new")
    assert new["counts"]["passed"] == 3
    assert all("test_multiply" not in n for n in summary["existing_suite_nodes"])


# --- Issue 3: stub adapter is refused, not defaulted to pytest --------------


def test_stub_adapter_refused(tmp_path):
    stub = os.path.join(REPO_ROOT, ".apia", "adapters", "csharp.yml")
    repo = _copy_pyapp(tmp_path)
    with pytest.raises(verify.StubAdapterError):
        verify.verify(repo, adapter_path=stub)


def test_all_phase1_stub_adapters_refused(tmp_path):
    # cpp.yml is completed in Phase 2 (FEAT-001) and is no longer a stub; the
    # remaining Phase-1 stubs (csharp/groovy, null test_command) stay refused.
    repo = _copy_pyapp(tmp_path)
    for name in ("csharp.yml", "groovy.yml"):
        stub = os.path.join(REPO_ROOT, ".apia", "adapters", name)
        with pytest.raises(verify.StubAdapterError):
            verify.verify(repo, adapter_path=stub)


def test_python_adapter_still_accepted(tmp_path):
    repo = _copy_pyapp(tmp_path)
    summary = verify.verify(repo, adapter_path=ADAPTER)
    assert summary["adapter"] == "python"
    assert summary["test_command"] == "python -m pytest"


# --- Issue 4: multi-line pyproject optional-dependencies --------------------


def test_pyproject_multiline_optional_dependencies(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join([
            "[project]",
            'name = "demo"',
            "dependencies = [",
            '    "click>=8.0",',
            '    "rich",',
            "]",
            "",
            "[project.optional-dependencies]",
            "test = [",
            '    "pytest",',
            '    "pytest-cov",',
            "]",
            'dev = ["black", "ruff"]',
            "",
        ]),
        encoding="utf-8",
    )
    deps = ingest._parse_pyproject(str(pyproject))
    # Core multi-line dependencies collected.
    assert "click>=8.0" in deps
    assert "rich" in deps
    # Multi-line optional-dependency extra collected (the previous parser missed
    # these entirely).
    assert "pytest (extra: test)" in deps
    assert "pytest-cov (extra: test)" in deps
    # Single-line extra still works alongside multi-line.
    assert "black (extra: dev)" in deps
    assert "ruff (extra: dev)" in deps
    # Non-dependency keys must never be collected as deps.
    assert "demo" not in deps


def test_pyproject_single_line_still_parses(tmp_path):
    """Regression: the committed sample's single-line form keeps working."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join([
            "[project]",
            'name = "demo"',
            "dependencies = []",
            "",
            "[project.optional-dependencies]",
            'test = ["pytest"]',
            "",
            "[tool.pytest.ini_options]",
            'pythonpath = ["."]',
            'testpaths = ["tests"]',
            "",
        ]),
        encoding="utf-8",
    )
    deps = ingest._parse_pyproject(str(pyproject))
    assert deps == ["pytest (extra: test)"]
    # tool.pytest array keys must not leak in as dependencies.
    assert not any("tests" == d or d == "." for d in deps)


# --- Issue 8: discover_tests still finds conftest + conventional tests ------


def test_discover_tests_finds_conftest_and_test_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef fx():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_thing.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8")
    (repo / "tests" / "helper.py").write_text(
        "def helper():\n    return 2\n", encoding="utf-8")
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")

    files = ingest.walk_repo(str(repo))
    tests = ingest.discover_tests(str(repo), files)
    paths = [t["path"] for t in tests]
    assert any(p.endswith("conftest.py") for p in paths), "conftest.py must be discovered"
    assert any(p.endswith("test_thing.py") for p in paths)
    # Non-test helper and __init__ inside tests/ must NOT be treated as tests.
    assert not any(p.endswith("helper.py") for p in paths)
    assert not any(p.endswith("__init__.py") for p in paths)


# --- Issue 5 & 7: docs are consistent ---------------------------------------


def _agent_skills_jetbrains_cell(doc_path):
    with open(doc_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.lstrip().startswith("| **Agent Skills**"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                # Columns: Mechanism | File | VS Code | Visual Studio | JetBrains | ...
                return cells[4]
    raise AssertionError("Agent Skills matrix row not found in {}".format(doc_path))


def test_jetbrains_agent_skills_cell_agrees_across_docs():
    arch = _agent_skills_jetbrains_cell(os.path.join(DOCS, "ARCHITECTURE.md"))
    port = _agent_skills_jetbrains_cell(os.path.join(DOCS, "PORTABILITY.md"))
    assert arch == port, (
        "ARCHITECTURE and PORTABILITY disagree on the JetBrains/Agent-Skills cell: "
        "{!r} vs {!r}".format(arch, port)
    )
    # And it must agree with the caveat that JetBrains does not honor Agent Skills.
    assert "No" in arch, "JetBrains Agent-Skills cell should be 'No' (unsupported)"


def test_docs_state_mcp_wiring_deferred_to_phase_2():
    with open(os.path.join(DOCS, "ARCHITECTURE.md"), "r", encoding="utf-8") as fh:
        text = fh.read().lower()
    assert "mcp wiring" in text
    # The explicit deferral statement must be present somewhere in the doc.
    assert "deferred to phase 2" in text or "all mcp wiring is deferred" in text, (
        "ARCHITECTURE.md must state that MCP wiring is deferred to Phase 2"
    )
