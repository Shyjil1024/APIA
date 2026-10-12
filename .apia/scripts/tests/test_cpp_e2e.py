"""C++ end-to-end + parser proof for the verify harness (FEAT-003).

Two layers of coverage:

  1. UNIT tests (always run, no toolchain needed): the ctest/gtest/msbuild
     output parsers against CAPTURED fixture strings (so MSBuild/VSTest parsing
     is proven without a Windows toolchain), the result_format dispatch, the
     command-splitting rules (python rewrite only for pytest; ctest/cmake/
     absolute paths left as-is), the dry-run plan resolution (no subprocess),
     and build-only summary shape.

  2. E2E tests (run when cmake/ctest are on PATH; skipped gracefully otherwise):
     the REAL loop on samples/cppapp -- build + baseline + regression all green
     via ctest with >=3 passed; a --baseline-only then post-change before/after
     delta that catches a broken existing test; and the no-tests C++ case where
     build-only is green with zero tests.

verify.py and ingest.py are pure off-model modules (no LLM/network). The e2e
tests drive only the adapter's LOCAL cmake/ctest commands via subprocess.
"""

import os
import shutil
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import verify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
CPPAPP = os.path.join(REPO_ROOT, "samples", "cppapp")
LEGACY_CPP = os.path.join(REPO_ROOT, "samples", "legacy_cppapp")
CPP_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "cpp.yml")
PY_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")

# E2E tests need a real CMake + CTest toolchain. In this sandbox they ARE
# present (and the tests run + pass); elsewhere they skip gracefully.
_HAVE_CMAKE = shutil.which("cmake") is not None and shutil.which("ctest") is not None
_requires_toolchain = pytest.mark.skipif(
    not _HAVE_CMAKE, reason="cmake/ctest not on PATH; C++ e2e skipped"
)


def _phase(summary, name):
    for p in summary["phases"]:
        if p["phase"] == name:
            return p
    raise AssertionError("phase {!r} not in summary".format(name))


# Captured fixture strings (real tool output; see FEAT-003 findings) ----------

CTEST_PASS = """Test project /tmp/x/build
    Start 1: Add.AddsTwoPositives
1/5 Test #1: Add.AddsTwoPositives .............   Passed    0.00 sec
    Start 2: Add.AddsNegativeAndPositive
2/5 Test #2: Add.AddsNegativeAndPositive ......   Passed    0.00 sec
    Start 3: Subtract.SubtractsTwoNumbers
3/5 Test #3: Subtract.SubtractsTwoNumbers .....   Passed    0.00 sec
    Start 4: Divide.DividesEvenly
4/5 Test #4: Divide.DividesEvenly .............   Passed    0.00 sec
    Start 5: Divide.ThrowsOnDivideByZero
5/5 Test #5: Divide.ThrowsOnDivideByZero ......   Passed    0.00 sec

100% tests passed, 0 tests failed out of 5

Total Test time (real) =   0.01 sec
"""

CTEST_FAIL = """Test project /tmp/x/build
1/5 Test #1: Add.AddsTwoPositives .............***Failed    0.00 sec
2/5 Test #2: Add.AddsNegativeAndPositive ......   Passed    0.00 sec
3/5 Test #3: Subtract.SubtractsTwoNumbers .....   Passed    0.00 sec
4/5 Test #4: Divide.DividesEvenly .............   Passed    0.00 sec
5/5 Test #5: Divide.ThrowsOnDivideByZero ......   Passed    0.00 sec

80% tests passed, 1 tests failed out of 5

Total Test time (real) =   0.01 sec

The following tests FAILED:
          1 - Add.AddsTwoPositives (Failed)
"""

CTEST_ZERO = """Test project /tmp/empty/build
No tests were found!!!

Total Tests: 0
"""

CTEST_COLLECT = """Test project /tmp/x/build
  Test #1: Add.AddsTwoPositives
  Test #2: Add.AddsNegativeAndPositive

Total Tests: 2
"""

GTEST_PASS = """[==========] Running 5 tests from 3 test suites.
[----------] Global test environment set-up.
[ RUN      ] Add.AddsTwoPositives
[       OK ] Add.AddsTwoPositives (0 ms)
[----------] 2 tests from Divide (0 ms total)

[----------] Global test environment tear-down
[==========] 5 tests from 3 test suites ran. (0 ms total)
[  PASSED  ] 5 tests.
"""

GTEST_FAIL = """[==========] Running 5 tests from 3 test suites.
[ RUN      ] Add.AddsTwoPositives
[  FAILED  ] Add.AddsTwoPositives (0 ms)
[----------] Global test environment tear-down
[==========] 5 tests from 3 test suites ran. (0 ms total)
[  PASSED  ] 4 tests.
[  FAILED  ] 1 test, listed below:
[  FAILED  ] Add.AddsTwoPositives
"""

GTEST_ZERO = """[==========] Running 0 tests from 0 test suites.
[==========] 0 tests from 0 test suites ran. (0 ms total)
[  PASSED  ] 0 tests.
"""

# VSTest / MSBuild console output (captured fixture; NEVER executed here -- no
# MSVC toolchain exists in the Linux sandbox).
VSTEST_PASS = """Starting test execution, please wait...
A total of 1 test files matched the specified pattern.

Passed!  - Failed:     0, Passed:     5, Skipped:     0, Total:     5
"""

VSTEST_FAIL = """Starting test execution, please wait...

Failed!  - Failed:     2, Passed:     3, Skipped:     1, Total:     6
"""

VSTEST_ZERO = """Starting test execution, please wait...
No test is available. Total tests: 0
"""

MSBUILD_BUILD_FAILED = """Build started.
mathops.cpp(10): error C2065: 'undeclared' : undeclared identifier
Build FAILED.
    1 Error(s)
"""


# --- UNIT: ctest parser ------------------------------------------------------


def test_ctest_parser_summary_pass():
    c = verify._parse_ctest_output(CTEST_PASS, "")
    assert c == {"passed": 5, "failed": 0, "error": 0, "skipped": 0, "no_tests": False}


def test_ctest_parser_summary_fail():
    c = verify._parse_ctest_output(CTEST_FAIL, "")
    assert c["passed"] == 4
    assert c["failed"] == 1
    assert c["no_tests"] is False


def test_ctest_parser_zero_tests():
    c = verify._parse_ctest_output(CTEST_ZERO, "")
    assert c["no_tests"] is True
    assert c["passed"] == 0 and c["failed"] == 0


def test_ctest_parser_per_test_lines_without_summary():
    # A collection-style listing with no summary line still reports no_tests via
    # the "Total Tests" signal (2 here -> not zero, but no pass/fail counts).
    c = verify._parse_ctest_output(CTEST_COLLECT, "")
    assert c["no_tests"] is False  # Total Tests: 2


# --- UNIT: gtest parser ------------------------------------------------------


def test_gtest_parser_pass():
    c = verify._parse_gtest_output(GTEST_PASS, "")
    assert c["passed"] == 5
    assert c["failed"] == 0
    assert c["no_tests"] is False


def test_gtest_parser_fail():
    c = verify._parse_gtest_output(GTEST_FAIL, "")
    assert c["passed"] == 4
    assert c["failed"] == 1
    assert c["no_tests"] is False


def test_gtest_parser_zero():
    c = verify._parse_gtest_output(GTEST_ZERO, "")
    assert c["no_tests"] is True
    assert c["passed"] == 0


# --- UNIT: msbuild / vstest parser (fixture-only, no toolchain) --------------


def test_msbuild_parser_pass():
    c = verify._parse_msbuild_output(VSTEST_PASS, "")
    assert c["passed"] == 5
    assert c["failed"] == 0
    assert c["skipped"] == 0
    assert c["no_tests"] is False


def test_msbuild_parser_fail():
    c = verify._parse_msbuild_output(VSTEST_FAIL, "")
    assert c["passed"] == 3
    assert c["failed"] == 2
    assert c["skipped"] == 1
    assert c["no_tests"] is False


def test_msbuild_parser_zero():
    c = verify._parse_msbuild_output(VSTEST_ZERO, "")
    assert c["no_tests"] is True


def test_msbuild_parser_flags_build_failed():
    c = verify._parse_msbuild_output(MSBUILD_BUILD_FAILED, "")
    assert c["error"] >= 1


# --- UNIT: counts shape is uniform across parsers ----------------------------


def test_all_parsers_share_counts_shape():
    keys = {"passed", "failed", "error", "skipped", "no_tests"}
    for parser, fixture in (
        (verify._parse_ctest_output, CTEST_PASS),
        (verify._parse_gtest_output, GTEST_PASS),
        (verify._parse_msbuild_output, VSTEST_PASS),
    ):
        counts = parser(fixture, "")
        assert keys <= set(counts), parser


# --- UNIT: result_format dispatch -------------------------------------------


def test_resolve_result_format_defaults_pytest_for_python():
    adapter = verify.load_adapter(PY_ADAPTER)
    assert verify.resolve_result_format(adapter) == "pytest"


def test_resolve_result_format_ctest_for_cpp():
    adapter = verify.load_adapter(CPP_ADAPTER)
    # cpp.yml's first framework is googletest -> result_format ctest.
    assert verify.resolve_result_format(adapter) == "ctest"


def test_resolve_result_format_top_level_wins():
    assert verify.resolve_result_format({"result_format": "gtest"}) == "gtest"


def test_parser_for_unknown_falls_back_to_pytest():
    assert verify._parser_for("nope") is verify._parse_pytest_output


# --- UNIT: command splitting rules ------------------------------------------


def test_split_command_rewrites_python_to_interpreter():
    argv = verify._split_command("python -m pytest")
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "pytest"]


def test_split_command_leaves_ctest_untouched():
    argv = verify._split_command("ctest --test-dir build --output-on-failure")
    assert argv[0] == "ctest"


def test_split_command_leaves_absolute_tool_untouched():
    argv = verify._split_command("/usr/bin/ctest --test-dir build")
    assert argv[0] == "/usr/bin/ctest"


def test_split_command_leaves_cmake_untouched():
    argv = verify._split_command("cmake --build build")
    assert argv[0] == "cmake"


# --- UNIT: build command / override resolution -------------------------------


def test_resolve_build_command_cpp():
    adapter = verify.load_adapter(CPP_ADAPTER)
    assert verify.resolve_build_command(adapter) == "cmake -S . -B build && cmake --build build"


def test_resolve_build_command_python_is_none():
    adapter = verify.load_adapter(PY_ADAPTER)
    assert verify.resolve_build_command(adapter) is None


def test_cpp_overrides_block_optional_and_absent_by_default():
    # The committed config ships the cpp overrides block COMMENTED, so no active
    # override is loaded (fallback-only, never required).
    overrides = verify.load_cpp_overrides(REPO_ROOT)
    assert overrides == {}


def test_test_command_override_applied_when_present():
    adapter = verify.load_adapter(CPP_ADAPTER)
    tc, bc = verify._apply_cpp_overrides(
        adapter, {"test_command_override": "ctest --test-dir out"}
    )
    assert tc == "ctest --test-dir out"
    assert bc == "cmake -S . -B build && cmake --build build"


# --- UNIT: stub-adapter policy ----------------------------------------------


def test_completed_cpp_adapter_is_accepted():
    adapter = verify.load_adapter(CPP_ADAPTER)
    # The completed cpp adapter has a real test_command, so it must NOT raise.
    assert verify._test_command(adapter) == "ctest --test-dir build --output-on-failure"


def test_stub_adapters_still_refused():
    for name in ("csharp.yml", "groovy.yml"):
        stub = verify.load_adapter(
            os.path.join(REPO_ROOT, ".apia", "adapters", name)
        )
        with pytest.raises(verify.StubAdapterError):
            verify._test_command(stub)


# --- UNIT: dry-run plan (no subprocess, nothing written) ---------------------


def test_dry_run_lists_planned_commands_without_running(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bl = str(tmp_path / "baseline.json")
    summary = verify.verify(
        str(repo), adapter_path=CPP_ADAPTER, baseline_file=bl, dry_run=True
    )
    assert summary["dry_run"] is True
    assert summary["phases"] == []
    phases = [pc["phase"] for pc in summary["planned_commands"]]
    assert phases[0] == "build"
    assert "baseline" in phases and "regression" in phases
    # Dry-run must write NOTHING.
    assert not os.path.isfile(bl)
    assert summary["ok"] is True
    assert any("result_format: ctest" in r for r in summary["reasoning"])


def test_dry_run_cli_spawns_no_subprocess(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def _boom(*a, **k):
        raise AssertionError("dry-run must not spawn a subprocess")

    monkeypatch.setattr(verify, "_run", _boom)
    rc = verify.main([str(repo), "--adapter", CPP_ADAPTER, "--dry-run"])
    assert rc == 0


# --- UNIT: resolve_plan build-phase gating -----------------------------------


def test_plan_runs_build_for_cpp(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = verify.resolve_plan(str(repo), adapter_path=CPP_ADAPTER)
    assert plan["run_build"] is True
    assert plan["result_format"] == "ctest"


def test_plan_no_build_for_python(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = verify.resolve_plan(str(repo), adapter_path=PY_ADAPTER)
    assert plan["run_build"] is False
    assert plan["build_command"] is None


# --- E2E: the real CMake + GoogleTest loop (runs in this sandbox) ------------


def _copy_cpp_sources(src, dst):
    """Copy a C++ sample's SOURCES (not the build dir) so CMake configures fresh."""
    os.makedirs(dst)
    for name in os.listdir(src):
        if name == "build":
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    return dst


@_requires_toolchain
def test_e2e_cppapp_build_baseline_regression_all_green(tmp_path):
    repo = _copy_cpp_sources(CPPAPP, str(tmp_path / "cppapp"))
    summary = verify.verify(repo, adapter_path=CPP_ADAPTER,
                            baseline_file=str(tmp_path / "bl.json"))
    assert summary["result_format"] == "ctest"
    build = _phase(summary, "build")
    baseline = _phase(summary, "baseline")
    regression = _phase(summary, "regression")
    assert build["passed"] is True
    assert baseline["passed"] and baseline["counts"]["passed"] >= 3
    assert regression["passed"] and regression["counts"]["failed"] == 0
    assert summary["ok"] is True


@_requires_toolchain
def test_e2e_cppapp_baseline_then_regression_delta_catches_break(tmp_path):
    repo = _copy_cpp_sources(CPPAPP, str(tmp_path / "cppapp"))
    bl = str(tmp_path / "bl.json")

    # Capture the GREEN pre-change baseline (build + baseline, persisted).
    pre = verify.verify(repo, adapter_path=CPP_ADAPTER, baseline_only=True,
                        baseline_file=bl)
    assert pre["ok"] is True
    assert os.path.isfile(bl)

    # Break an EXISTING test so ctest reports a failure post-change.
    test_file = os.path.join(repo, "tests", "test_mathops.cpp")
    with open(test_file, "r", encoding="utf-8") as fh:
        src = fh.read()
    with open(test_file, "w", encoding="utf-8") as fh:
        fh.write(src.replace("mathops::add(2, 3), 5", "mathops::add(2, 3), 999"))

    post = verify.verify(repo, adapter_path=CPP_ADAPTER, baseline_file=bl)
    regression = _phase(post, "regression")
    assert regression["counts"]["failed"] >= 1
    assert regression["passed"] is False
    assert post["ok"] is False


@_requires_toolchain
def test_e2e_cpp_no_tests_build_only_is_green(tmp_path):
    """A C++ project with ZERO tests: build-only is the regression signal."""
    repo = str(tmp_path / "notest")
    os.makedirs(os.path.join(repo, "src"))
    os.makedirs(os.path.join(repo, "include"))
    with open(os.path.join(repo, "include", "widget.h"), "w", encoding="utf-8") as fh:
        fh.write("#pragma once\nint answer();\n")
    with open(os.path.join(repo, "src", "widget.cpp"), "w", encoding="utf-8") as fh:
        fh.write('#include "widget.h"\nint answer() { return 42; }\n')
    with open(os.path.join(repo, "CMakeLists.txt"), "w", encoding="utf-8") as fh:
        fh.write(
            "cmake_minimum_required(VERSION 3.14)\n"
            "project(notest CXX)\n"
            "set(CMAKE_CXX_STANDARD 14)\n"
            "add_library(widget src/widget.cpp)\n"
            "target_include_directories(widget PUBLIC include)\n"
            "enable_testing()\n"
        )
    summary = verify.verify(repo, adapter_path=CPP_ADAPTER, build_only=True,
                            baseline_file=str(tmp_path / "bl.json"))
    assert summary["build_only"] is True
    build = _phase(summary, "build")
    assert build["passed"] is True, build
    assert summary["ok"] is True
    # No test phases ran in build-only mode.
    assert [p["phase"] for p in summary["phases"]] == ["build"]


@_requires_toolchain
def test_e2e_legacy_cppapp_characterization_green(tmp_path):
    """The committed no-tests-origin C++ sample now has characterization tests
    that are green via the same CMake+CTest loop."""
    if not os.path.isdir(LEGACY_CPP):
        pytest.skip("samples/legacy_cppapp not present")
    repo = _copy_cpp_sources(LEGACY_CPP, str(tmp_path / "legacy_cppapp"))
    summary = verify.verify(repo, adapter_path=CPP_ADAPTER,
                            baseline_file=str(tmp_path / "bl.json"))
    build = _phase(summary, "build")
    baseline = _phase(summary, "baseline")
    assert build["passed"] is True
    assert baseline["passed"] and baseline["counts"]["passed"] >= 1
    assert summary["ok"] is True
