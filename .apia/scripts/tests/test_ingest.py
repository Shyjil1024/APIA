"""Tests for the APIA off-model ingestion engine (.apia/scripts/ingest.py).

These run the ingester against the committed sample repos (samples/pyapp and
samples/legacy_pyapp) and assert stack detection, test inventory, dependency
listing, cache writing, the incremental (no-change) cache, and the hard
no-network/no-LLM constraint.

All ingestion output is written to pytest's tmp_path so the committed knowledge
base is never touched by the test run.
"""

import json
import os
import sys

import pytest

# Make ingest.py importable regardless of pytest's rootdir / cwd.
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest  # noqa: E402

# Repo root is three levels up from this file: .apia/scripts/tests/ -> repo.
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
LEGACY = os.path.join(REPO_ROOT, "samples", "legacy_pyapp")


def _read(knowledge_dir, name):
    with open(os.path.join(knowledge_dir, name), "r", encoding="utf-8") as fh:
        return fh.read()


def test_samples_exist():
    assert os.path.isdir(PYAPP), "sample pyapp missing"
    assert os.path.isdir(LEGACY), "sample legacy_pyapp missing"


def test_pyapp_emits_all_artifacts(tmp_path):
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(PYAPP, kd)
    assert result["reprocessed"] is True
    for artifact in ingest.ARTIFACTS:
        path = os.path.join(kd, artifact)
        assert os.path.isfile(path), "missing artifact: {}".format(artifact)
        assert os.path.getsize(path) > 0, "empty artifact: {}".format(artifact)
    # The incremental cache index must be written.
    assert os.path.isfile(os.path.join(kd, ingest.INDEX_FILE))


def test_pyapp_stack_detected_as_python(tmp_path):
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(PYAPP, kd)
    assert result["stacks"] == ["python"]
    stack_md = _read(kd, "stack.md")
    assert "python" in stack_md
    assert "pytest" in stack_md  # framework detected
    # Non-python stacks must not be falsely detected.
    for other in ("cpp", "csharp", "groovy"):
        assert "| {} |".format(other) not in stack_md


def test_pyapp_test_inventory_lists_tests(tmp_path):
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(PYAPP, kd)
    # pyapp ships the original 4 regression tests plus the 3 new-behavior tests
    # added by the FEAT-004 worked example (tests/test_multiply.py).
    assert result["test_count"] == 7
    inv = _read(kd, "test-inventory.md")
    assert "tests/test_operations.py" in inv
    assert "tests/test_multiply.py" in inv
    for fn in ("test_add", "test_subtract", "test_divide", "test_divide_by_zero_raises",
               "test_multiply_positives"):
        assert fn in inv, "missing test function in inventory: {}".format(fn)


def test_pyapp_dependencies_listed(tmp_path):
    kd = str(tmp_path / "knowledge")
    ingest.ingest(PYAPP, kd)
    deps = _read(kd, "dependencies.md")
    assert "pyproject.toml" in deps
    assert "pytest" in deps  # declared under the test extra


def test_legacy_has_characterization_tests(tmp_path):
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(LEGACY, kd)
    assert result["stacks"] == ["python"]
    # legacy_pyapp started with NO tests; FEAT-004 proved the legacy path by
    # generating characterization tests (pin current behavior) + a new-behavior
    # test for the safe change, so the ingester now discovers them.
    assert result["test_count"] >= 1
    inv = _read(kd, "test-inventory.md")
    assert "tests/test_inventory_characterization.py" in inv
    assert "tests/test_reorder.py" in inv


def test_incremental_cache_no_change_reprocesses_nothing(tmp_path):
    kd = str(tmp_path / "knowledge")
    first = ingest.ingest(PYAPP, kd)
    assert first["reprocessed"] is True
    assert first["changed_paths"], "first run should report changed paths"

    # Second run with no file changes must be a cache hit (nothing reprocessed).
    second = ingest.ingest(PYAPP, kd)
    assert second["reprocessed"] is False
    assert second["changed_paths"] == []


def test_full_flag_forces_reingest(tmp_path):
    kd = str(tmp_path / "knowledge")
    ingest.ingest(PYAPP, kd)
    # Even with no changes, --full must reprocess.
    forced = ingest.ingest(PYAPP, kd, full=True)
    assert forced["reprocessed"] is True


def test_incremental_cache_detects_change(tmp_path):
    """Copy a sample, ingest, mutate a source file, confirm a re-ingest fires."""
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(PYAPP, str(repo))
    kd = str(tmp_path / "knowledge")

    ingest.ingest(str(repo), kd)
    hit = ingest.ingest(str(repo), kd)
    assert hit["reprocessed"] is False

    # Mutate a tracked source file -> its hash changes.
    target = repo / "calc" / "operations.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")

    changed = ingest.ingest(str(repo), kd)
    assert changed["reprocessed"] is True
    assert any("operations.py" in p for p in changed["changed_paths"])


def test_index_records_sources_and_hashes(tmp_path):
    kd = str(tmp_path / "knowledge")
    ingest.ingest(PYAPP, kd)
    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    assert "sources" in index and index["sources"], "index must record source hashes"
    # Per-artifact derivation sets must be recorded for every artifact.
    assert set(index["artifacts"].keys()) == set(ingest.ARTIFACTS)
    # pyproject.toml is a source and must have a hash entry.
    assert any(p.endswith("pyproject.toml") for p in index["sources"])


def test_ingest_makes_no_network_or_llm_calls():
    """Static guard: the ingest source must not import network/LLM SDKs."""
    src_path = os.path.join(SCRIPTS_DIR, "ingest.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    forbidden = [
        "import requests",
        "import openai",
        "import anthropic",
        "import httpx",
        "urllib.request",
        "http.client",
        "socket.socket",
        "api.openai.com",
        "chat/completions",
    ]
    for token in forbidden:
        assert token not in src, "ingest.py must not reference '{}'".format(token)


# --- Phase-6a LOCAL plugin wiring (additive) ---------------------------------

def test_detect_stacks_surfaces_plugin_stack(tmp_path):
    """A LOCAL adapter plugin's stack is detected when the repo has evidence."""
    repo = tmp_path / "repo"
    (repo / ".apia" / "plugins" / "adapters").mkdir(parents=True)
    (repo / ".apia" / "plugins" / "adapters" / "zig.yml").write_text(
        "name: zig\ntest_command: zig test\nextensions:\n  - .zig\n"
        "manifests:\n  - build.zig\n", encoding="utf-8")
    (repo / "main.zig").write_text("pub fn main() void {}\n", encoding="utf-8")
    (repo / "build.zig").write_text("// build\n", encoding="utf-8")
    files = ingest.walk_repo(str(repo))
    detected = ingest.detect_stacks(str(repo), files)
    assert "zig" in detected
    assert detected["zig"]["plugin"] is True
    # Without the plugin, the stack is not invented.
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "main.zig").write_text("pub fn main() void {}\n", encoding="utf-8")
    assert "zig" not in ingest.detect_stacks(str(plain), ingest.walk_repo(str(plain)))


# --- Multi-language test discovery + vendored exclusion (Bugs 1 & 2) ---------

def _make_cpp_repo(root):
    """Lay down a tiny CMake + GoogleTest C++ repo with a vendored tree.

    Mirrors the LibVITA layout that surfaced the bugs: PascalCase *Test.cpp
    GoogleTest files under cpp/test/, plus a cpp/third-parties/ dir holding a
    stray vendored .cpp that must NOT be ingested.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.14)\nproject(demo)\n"
        "enable_testing()\nfind_package(GTest REQUIRED)\n", encoding="utf-8")
    src = root / "cpp" / "src"
    src.mkdir(parents=True)
    (src / "math_functions.cpp").write_text(
        "int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    # PascalCase GoogleTest files under cpp/test/.
    test = root / "cpp" / "test"
    test.mkdir(parents=True)
    (test / "MathFunctionsTest.cpp").write_text(
        "#include <gtest/gtest.h>\n"
        "TEST(MathFunctions, Add) { EXPECT_EQ(add(1, 2), 3); }\n"
        "TEST_F(MathFixture, Sub) { EXPECT_EQ(sub(2, 1), 1); }\n",
        encoding="utf-8")
    (test / "DataSampleTest.cpp").write_text(
        "#include <gtest/gtest.h>\n"
        "TEST(DataSample, Loads) { EXPECT_TRUE(true); }\n",
        encoding="utf-8")
    # Vendored / third-party tree that must be skipped entirely.
    vendor = root / "cpp" / "third-parties" / "eigen"
    vendor.mkdir(parents=True)
    (vendor / "stray.cpp").write_text(
        "// vendored dependency source, not project code\n"
        "TEST(Vendored, ShouldBeIgnored) { }\n", encoding="utf-8")
    return root


def test_cpp_tests_counted_and_no_legacy_message(tmp_path):
    """Bug 1: a C++ repo with *Test.cpp files reports a non-zero test count."""
    repo = _make_cpp_repo(tmp_path / "cpprepo")
    kd = str(tmp_path / "knowledge")
    result = ingest.ingest(str(repo), kd)

    # The three GoogleTest cases across the two test files must be counted.
    assert result["test_count"] >= 3, result["test_count"]
    inv = _read(kd, "test-inventory.md")
    assert "MathFunctionsTest.cpp" in inv
    assert "DataSampleTest.cpp" in inv
    # The no-tests / legacy characterization path must NOT false-trigger.
    assert "No tests found" not in inv
    assert "Legacy / no-tests" not in inv


def test_cpp_vendored_dir_excluded_from_walk(tmp_path):
    """Bug 2: a vendored third-party dir is pruned from the walk + artifacts."""
    repo = _make_cpp_repo(tmp_path / "cpprepo2")
    kd = str(tmp_path / "knowledge")
    ingest.ingest(str(repo), kd)

    # The vendored source must not appear in any walked file / index entry.
    files = ingest.walk_repo(str(repo))
    assert not any("third-parties" in f for f in files), files
    with open(os.path.join(kd, ingest.INDEX_FILE), "r", encoding="utf-8") as fh:
        index = json.load(fh)
    assert not any("third-parties" in p for p in index["sources"])
    # The vendored TEST(...) macro must not inflate the test inventory either.
    inv = _read(kd, "test-inventory.md")
    assert "third-parties" not in inv
    assert "stray.cpp" not in inv


def test_default_ignores_cover_common_vendor_dirs():
    """The vendored/third-party dir names are all in the default-ignore set."""
    for name in ("third-parties", "third-party", "third_party", "thirdparty",
                 "vendor", "vendored", "external", "externals", "extern",
                 "deps", ".conan"):
        assert name in ingest.DEFAULT_IGNORES, name


def test_discover_tests_cpp_counts_framework_macros(tmp_path):
    """discover_tests counts GoogleTest/Catch2/Boost macros via static scan."""
    repo = tmp_path / "r"
    t = repo / "tests"
    t.mkdir(parents=True)
    (t / "WidgetTest.cpp").write_text(
        "#include <gtest/gtest.h>\n"
        "TEST(Widget, A) {}\nTEST_F(WidgetFix, B) {}\nTEST_P(WidgetParam, C) {}\n",
        encoding="utf-8")
    (t / "catch_test.cpp").write_text(
        "#include <catch2/catch.hpp>\nTEST_CASE(\"adds numbers\") {}\n",
        encoding="utf-8")
    files = ingest.walk_repo(str(repo))
    tests = ingest.discover_tests(str(repo), files)
    by_path = {os.path.basename(t["path"]): t for t in tests}
    assert "WidgetTest.cpp" in by_path
    assert len(by_path["WidgetTest.cpp"]["functions"]) == 3
    assert "catch_test.cpp" in by_path
    assert len(by_path["catch_test.cpp"]["functions"]) == 1


def test_discover_tests_python_path_unchanged(tmp_path):
    """Regression: the Python AST discovery path is unaffected by Bug 1 work."""
    repo = tmp_path / "py"
    repo.mkdir()
    (repo / "test_sample.py").write_text(
        "def test_one():\n    assert True\n\n"
        "class TestGroup:\n    def test_two(self):\n        assert True\n",
        encoding="utf-8")
    files = ingest.walk_repo(str(repo))
    tests = ingest.discover_tests(str(repo), files)
    assert len(tests) == 1
    funcs = tests[0]["functions"]
    assert "test_one" in funcs
    assert "TestGroup.test_two" in funcs
