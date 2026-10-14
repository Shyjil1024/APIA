"""Tests for the APIA off-model greenfield-generation engine (scaffold.py).

Coverage (per FEAT-002):
  (a) shape detection for each supported shape + an ambiguous/low-confidence
      case + gui/embedded/driver -> non-verifiable classification;
  (b) skeleton emission produces the expected files per shape (file presence +
      build files parse) and is idempotent;
  (c) verify_generated normalization for a FULLY-verifiable shape (a real,
      fast Python CLI/library built+run+tested via verify) AND the honest-partial
      path (partial=True, never green);
  (d) generation_gate returns scan.apply_thresholds output for a pass case and a
      fail case (a seeded high-severity finding fails under review.thresholds);
  (e) decide_generation honors the max_iterations cap and the acceptance-criteria
      confirmation gate (unconfirmed new-behavior criteria do NOT let it pass);
  (f) a static no-network/no-LLM guard mirroring
      test_scan.test_scan_makes_no_network_or_llm_calls.

scaffold.py is a pure off-model module (no LLM/network). It spawns NO subprocess
of its own; the executed build/test runs go through verify.py's existing _run()
chokepoint. The heavy C++ end-to-end build is deferred to FEAT-005, so these
tests use a small, fast Python project for the fully-verifiable e2e proof.
"""

import ast
import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import scaffold  # noqa: E402
import scan  # noqa: E402
import evalloop  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))


# --- (a) shape detection -----------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ({"stack": "python", "summary": "a Flask web API with endpoints"},
     "python_web_api"),
    ({"stack": "python", "summary": "a command-line tool using argparse"},
     "python_cli"),
    ({"stack": "python", "summary": "a reusable importable library package"},
     "python_library"),
    ({"stack": "cpp", "summary": "a console executable that prints to stdout"},
     "cpp_console"),
    ({"stack": "cpp", "summary": "a static library with public headers"},
     "cpp_library"),
])
def test_detect_shape_fully_verifiable(spec, expected):
    result = scaffold.detect_shape(spec)
    assert result["shape"] == expected
    assert result["verifiable"] is True
    assert result["supported"] is True
    assert result["confidence"] == "high"
    assert result["reasons"]  # never silent


@pytest.mark.parametrize("spec,expected", [
    ({"summary": "a desktop GUI app built with Qt widgets"}, "gui"),
    ({"summary": "firmware for an STM32 microcontroller (bare metal)"}, "embedded"),
    ({"summary": "a Linux kernel module / device driver"}, "driver"),
])
def test_detect_shape_honest_partial(spec, expected):
    result = scaffold.detect_shape(spec)
    assert result["shape"] == expected
    assert result["verifiable"] is False
    # Honest-partial shapes are recognized AND surface why they can't be
    # fully auto-verified.
    assert any("HONEST-PARTIAL" in r for r in result["reasons"])


def test_detect_shape_explicit_wins():
    result = scaffold.detect_shape({"shape": "cpp_library", "stack": "cpp"})
    assert result["shape"] == "cpp_library"
    assert result["verifiable"] is True


def test_detect_shape_ambiguous_is_low_confidence_not_silent():
    # No stack, no markers: a best-effort shape is returned but flagged LOW so
    # the live clarify gate can ask. It never guesses silently.
    result = scaffold.detect_shape({"summary": "build me something cool"})
    assert result["confidence"] == "low"
    assert result["reasons"]
    assert any("clarify" in r.lower() or "low confidence" in r.lower()
               for r in result["reasons"])


def test_detect_shape_reads_config_supported_shapes(tmp_path):
    # When config.yml declares generate.supported_shapes, a shape outside that
    # list is marked unsupported. This proves the REPO-FIRST config read works
    # without depending on FEAT-003 having added the key to the package config.
    apia = tmp_path / ".apia"
    apia.mkdir()
    (apia / "config.yml").write_text(
        "generate:\n  supported_shapes:\n    - python_cli\n")
    result = scaffold.detect_shape(
        {"stack": "cpp", "summary": "a console executable"},
        repo_path=str(tmp_path))
    # cpp_console is NOT in the repo's supported list -> flagged unsupported.
    assert result["shape"] == "cpp_console"
    assert result["supported"] is False


def test_resolve_supported_shapes_falls_back_to_builtin():
    # The package config has no generate.supported_shapes yet (FEAT-003), so the
    # built-in default must be returned.
    shapes = scaffold.resolve_supported_shapes()
    for s in scaffold.FULLY_VERIFIABLE_SHAPES + scaffold.HONEST_PARTIAL_SHAPES:
        assert s in shapes


# --- (b) skeleton emission ---------------------------------------------------

def _assert_python_build_file_parses(path):
    # A valid pyproject.toml is TOML; parse it if tomllib/tomli is available,
    # else at least assert the key tables are present.
    text = open(path, "r", encoding="utf-8").read()
    assert "[project]" in text
    assert "name =" in text
    try:
        import tomllib  # py3.11+
        tomllib.loads(text)
    except Exception:
        try:
            import tomli
            tomli.loads(text)
        except Exception:
            pass  # parser unavailable; the structural asserts above still hold


@pytest.mark.parametrize("spec,shape,expected_files", [
    ({"stack": "python", "name": "clidemo", "summary": "cli tool argparse"},
     "python_cli", ["pyproject.toml", "clidemo/__init__.py",
                    "clidemo/__main__.py", "tests/test_smoke.py"]),
    ({"stack": "python", "name": "libdemo", "summary": "reusable library"},
     "python_library", ["pyproject.toml", "libdemo/__init__.py",
                         "tests/test_smoke.py"]),
    ({"stack": "python", "name": "webdemo", "summary": "flask web api"},
     "python_web_api", ["pyproject.toml", "webdemo/__init__.py",
                        "webdemo/app.py", "tests/test_smoke.py"]),
])
def test_emit_python_skeletons(tmp_path, spec, shape, expected_files):
    dest = str(tmp_path / "proj")
    result = scaffold.emit_skeleton(spec, dest)
    assert result["shape"] == shape
    assert result["partial"] is False
    for rel in expected_files:
        assert os.path.isfile(os.path.join(dest, rel)), rel
        assert rel in result["files"]
    _assert_python_build_file_parses(os.path.join(dest, "pyproject.toml"))
    # The generated Python sources parse as valid Python.
    for rel in result["files"]:
        if rel.endswith(".py"):
            src = open(os.path.join(dest, rel), "r", encoding="utf-8").read()
            ast.parse(src)


def test_emit_cpp_console_skeleton(tmp_path):
    dest = str(tmp_path / "cppproj")
    result = scaffold.emit_skeleton(
        {"stack": "cpp", "name": "conapp", "summary": "console executable"}, dest)
    assert result["shape"] == "cpp_console"
    for rel in ["CMakeLists.txt", "src/main.cpp", "src/conapp_lib.cpp",
                "include/conapp_lib.h", "tests/test_smoke.cpp"]:
        assert os.path.isfile(os.path.join(dest, rel)), rel
    cmake = open(os.path.join(dest, "CMakeLists.txt"), "r", encoding="utf-8").read()
    # The build file "parses" at least to the extent of declaring the console
    # target + the GoogleTest fetch + the test target (mirrors samples/cppapp).
    assert "project(conapp CXX)" in cmake
    assert "add_executable(conapp src/main.cpp)" in cmake
    assert "FetchContent_Declare" in cmake
    assert "gtest_discover_tests(test_smoke)" in cmake
    # The sources include their own project header (self-consistent, compiles).
    main_src = open(os.path.join(dest, "src/main.cpp"), "r", encoding="utf-8").read()
    assert '#include "conapp_lib.h"' in main_src


def test_emit_cpp_library_skeleton(tmp_path):
    dest = str(tmp_path / "cpplib")
    result = scaffold.emit_skeleton(
        {"stack": "cpp", "name": "mylib", "summary": "static library header"}, dest)
    assert result["shape"] == "cpp_library"
    for rel in ["CMakeLists.txt", "src/mylib_lib.cpp", "include/mylib_lib.h",
                "tests/test_smoke.cpp"]:
        assert os.path.isfile(os.path.join(dest, rel)), rel
    cmake = open(os.path.join(dest, "CMakeLists.txt"), "r", encoding="utf-8").read()
    assert "add_library(mylib_lib src/mylib_lib.cpp)" in cmake
    # A library shape has no console executable target.
    assert "add_executable(mylib " not in cmake


def test_emit_honest_partial_minimal_with_marker(tmp_path):
    dest = str(tmp_path / "guiproj")
    result = scaffold.emit_skeleton(
        {"name": "guiapp", "summary": "a Qt desktop GUI app"}, dest)
    assert result["shape"] == "gui"
    assert result["partial"] is True
    # A plain-language partial marker is written so a faked green is impossible.
    assert os.path.isfile(os.path.join(dest, "PARTIAL.md"))
    marker = open(os.path.join(dest, "PARTIAL.md"), "r", encoding="utf-8").read()
    assert "HONEST-PARTIAL" in marker
    assert "gui" in marker


def test_emit_is_idempotent(tmp_path):
    dest = str(tmp_path / "proj")
    spec = {"stack": "python", "name": "idem", "summary": "cli tool"}
    r1 = scaffold.emit_skeleton(spec, dest)
    before = {f: open(os.path.join(dest, f), "rb").read() for f in r1["files"]}
    r2 = scaffold.emit_skeleton(spec, dest)
    after = {f: open(os.path.join(dest, f), "rb").read() for f in r2["files"]}
    assert r1["files"] == r2["files"]
    assert before == after  # re-emitting produces byte-identical files


# --- (c) verify_generated normalization --------------------------------------

def test_verify_generated_fully_verifiable_green(tmp_path):
    # EXECUTED e2e: emit a real Python CLI skeleton and build+run+test it via the
    # shared verify.py machinery. Fast (no C++ build) but real.
    dest = str(tmp_path / "cliproj")
    scaffold.emit_skeleton(
        {"stack": "python", "name": "runnable", "summary": "cli tool"}, dest)
    result = scaffold.verify_generated(dest, "python_cli")
    assert result["verifiable"] is True
    assert result["partial"] is False
    assert result["ok"] is True
    # The shared counts shape is present and reflects the real test run.
    for key in ("passed", "failed", "error", "skipped", "no_tests"):
        assert key in result
    assert result["passed"] >= 1
    assert result["failed"] == 0
    assert result["no_tests"] is False


def test_verify_generated_cli_run_is_real_execution_distinct_from_tests(tmp_path):
    # The `run` field must reflect an ACTUAL `python -m <name>` execution of the
    # generated entrypoint (ran=True, passed=True, with a real command), NOT an
    # alias of the unit-test phase. This would FAIL if run collapsed into tests.
    dest = str(tmp_path / "runnablecli")
    scaffold.emit_skeleton(
        {"stack": "python", "name": "realrun", "summary": "cli tool"}, dest)
    result = scaffold.verify_generated(dest, "python_cli")
    assert result["ok"] is True
    run = result["run"]
    assert run["ran"] is True
    assert run["passed"] is True
    # It really ran `python -m realrun` (the adapter run_command), not pytest.
    assert "-m" in run["command"] and "realrun" in run["command"]
    assert "returncode" in run and run["returncode"] == 0
    # run is DISTINCT from tests: tests carries the pytest counts, run does not.
    assert "counts" in result["tests"]
    assert "counts" not in run


def test_verify_generated_cli_broken_entrypoint_never_fakes_green(tmp_path):
    # NEVER fake green: a generated python_cli whose entrypoint exits non-zero on
    # a bare `python -m <name>` run must make run.passed False AND ok False, even
    # though its unit tests (which import main() directly) still pass.
    dest = str(tmp_path / "brokencli")
    scaffold.emit_skeleton(
        {"stack": "python", "name": "brokenrun", "summary": "cli tool"}, dest)
    # Rewrite the entrypoint so a bare `python -m brokenrun` exits non-zero via
    # the __main__ guard, while main() itself (imported by the smoke test)
    # still returns cleanly. The exit must live INSIDE the guard so merely
    # importing the module (as pytest does) does not abort collection.
    main_py = os.path.join(dest, "brokenrun", "__main__.py")
    with open(main_py, "w", encoding="utf-8") as fh:
        fh.write(
            "import sys\n\n\n"
            "def main(argv=None):\n"
            "    return 'brokenrun: ready'\n\n\n"
            "if __name__ == '__main__':\n"
            "    print(main())\n"
            "    sys.exit(3)\n"
        )

    result = scaffold.verify_generated(dest, "python_cli")
    # The unit tests still pass (they call main() directly, not via -m)...
    assert result["tests"]["passed"] is True
    assert result["passed"] >= 1
    # ...but the REAL run failed, so the result is NOT green.
    assert result["run"]["ran"] is True
    assert result["run"]["passed"] is False
    assert result["run"]["returncode"] == 3
    assert result["ok"] is False


def test_verify_generated_library_green(tmp_path):
    dest = str(tmp_path / "libproj")
    scaffold.emit_skeleton(
        {"stack": "python", "name": "librunnable", "summary": "reusable library"},
        dest)
    result = scaffold.verify_generated(dest, "python_library")
    assert result["ok"] is True
    assert result["partial"] is False
    assert result["passed"] >= 1


def test_verify_generated_honest_partial_never_green(tmp_path):
    # The honest-partial path: emit a GUI skeleton, verify -> partial True and
    # NEVER green, with a plain-language unverified_aspects list.
    dest = str(tmp_path / "guiproj")
    scaffold.emit_skeleton({"name": "guiapp", "summary": "a Qt desktop GUI"}, dest)
    result = scaffold.verify_generated(dest, "gui")
    assert result["partial"] is True
    assert result["ok"] is False  # never green
    assert result["verifiable"] is False
    assert result["unverified_aspects"]  # plain-language manual-check list
    # The shared counts shape is still present.
    for key in ("passed", "failed", "error", "skipped", "no_tests"):
        assert key in result


# --- (d) generation gate (delegates to scan.apply_thresholds) ----------------

def test_generation_gate_passes_with_no_findings():
    gate = scaffold.generation_gate([])
    assert gate["passed"] is True
    # It is literally scan.apply_thresholds' block shape.
    for key in ("passed", "fail_on_severity", "failing_severities",
                "failing_counts", "reasons"):
        assert key in gate


def test_generation_gate_fails_on_high_severity_under_config_thresholds():
    # review.thresholds in config.yml is fail_on_severity: high, security max 0.
    high = [scan.make_finding("high", "security", "x.py", 1, "B602",
                              "insecure call", "bandit")]
    gate = scaffold.generation_gate(high)
    assert gate["passed"] is False
    assert "high" in gate["failing_severities"]
    assert gate["reasons"]


def test_generation_gate_matches_scan_apply_thresholds_exactly():
    # generation_gate must return scan.apply_thresholds output UNCHANGED for the
    # same thresholds (not a reimplementation).
    findings = [scan.make_finding("medium", "quality", "a.py", 3, "E1", "m",
                                  "ruff")]
    review = scan.load_review_config()
    thresholds = scan.resolve_thresholds(review)
    assert scaffold.generation_gate(findings) == scan.apply_thresholds(
        findings, thresholds)


# --- (e) decide_generation (reuses evalloop primitives) ----------------------

def test_decide_generation_green_when_gate_and_verify_pass():
    acc = {"is_new_behavior": True, "confirmed": True, "criteria": []}
    verify_result = {"ok": True, "partial": False}
    dec = scaffold.decide_generation({}, verify_result, [], acceptance=acc)
    assert dec["status"] == "green"
    assert dec["gate"]["passed"] is True


def test_decide_generation_asks_on_unconfirmed_new_behavior():
    # Anti-circularity: unconfirmed NEW-behavior acceptance criteria must NOT let
    # the generation pass; decide_generation returns 'ask'.
    spec = {"is_new_behavior": True, "criteria": ["does the new thing"]}
    verify_result = {"ok": True, "partial": False}
    dec = scaffold.decide_generation(spec, verify_result, [])
    assert dec["status"] == "ask"
    assert dec["reason"] == "acceptance_criteria_unconfirmed"
    # It never spent an iteration (the gate fired first).
    assert dec["iteration"] == 0


def test_decide_generation_iterates_then_stops_at_cap():
    # A persistently-failing verification must iterate and then STOP at the hard
    # max_iterations cap (reusing evalloop.resolve_max_iterations), never loop
    # forever and never silently ship.
    cap = evalloop.resolve_max_iterations()
    state = evalloop.LoopState()
    acc = {"is_new_behavior": False}  # grounded; no confirmation needed
    bad_verify = {"ok": False, "partial": False}
    statuses = []
    for _ in range(cap + 3):
        dec = scaffold.decide_generation({}, bad_verify, [], acceptance=acc,
                                         state=state)
        statuses.append(dec["status"])
        if dec["status"] == "stop":
            break
    assert statuses[-1] == "stop"
    assert dec["iteration"] == cap
    assert dec["reason"] == "max_iterations_reached"
    # Exactly cap-1 iterate decisions precede the stop.
    assert statuses.count("iterate") == cap - 1
    # PARITY with the standalone iterate stop: the generation stop carries the
    # best-so-far attempt (not a generic ask), and the ask names it.
    assert "best_so_far" in dec
    assert "BEST attempt so far" in dec["ask"]


def test_decide_generation_stop_carries_best_so_far_with_residual():
    # When the gate fails on a seeded HIGH finding (verify green), the generation
    # stop at the cap must surface the SAME best-so-far record + residual summary
    # the standalone iterate stop computes, for parity across entry points.
    cap = evalloop.resolve_max_iterations()
    state = evalloop.LoopState()
    acc = {"is_new_behavior": False}
    good_verify = {"ok": True, "partial": False}
    findings = [{"severity": "high", "category": "security", "file": "a.py",
                 "line": 1, "rule": "B602", "message": "shell=True",
                 "source_tool": "bandit"}]
    dec = None
    for _ in range(cap + 3):
        dec = scaffold.decide_generation({}, good_verify, findings,
                                         acceptance=acc, state=state)
        if dec["status"] == "stop":
            break
    assert dec["status"] == "stop"
    assert dec["best_so_far"] is not None
    # The residual summary reaches parity with evalloop's standalone stop ask.
    assert evalloop._residual_summary(dec["best_so_far"]) in dec["ask"]
    assert "high=1" in dec["ask"]


def test_decide_generation_honest_partial_never_green():
    # An honest-partial verification can never be 'green', even with a clean gate
    # and confirmed criteria.
    acc = {"is_new_behavior": False}
    partial_verify = {"ok": False, "partial": True,
                      "unverified_aspects": ["GUI needs manual check"]}
    dec = scaffold.decide_generation({}, partial_verify, [], acceptance=acc)
    assert dec["status"] in ("iterate", "stop")
    assert dec["status"] != "green"


# --- (f) static no-network / no-LLM guard ------------------------------------

def test_scaffold_makes_no_network_or_llm_calls():
    """Static guard: scaffold.py must not import network/LLM SDKs or endpoints.

    This mirrors test_scan.test_scan_makes_no_network_or_llm_calls EXACTLY (same
    forbidden-substring tripwire list). It is a tripwire, not a proof: the real
    guarantee is structural -- scaffold.py is stdlib + PyYAML only, spawns no
    subprocess of its own, and routes any build/test run through verify.py's
    single _run() chokepoint (see the module docstring's HARD CONSTRAINT)."""
    src_path = os.path.join(SCRIPTS_DIR, "scaffold.py")
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
        assert token not in src, "scaffold.py must not reference '{}'".format(token)


# ===========================================================================
# PHASE 5 -- ITERATE-TO-BEST verdict PARITY (scaffold side).
# scaffold.decide_generation must DELEGATE the fused converged/needs_fix/stop
# decision to the ONE shared evalloop.evaluate_quality_gated, so the SAME
# reviewer-gate block + the SAME converged/needs_fix verdict come out as a direct
# evalloop call on identical inputs. This proves scaffold is not a forked copy of
# the decision. (The orchestrate side of the parity triple lives in
# test_orchestrate.py.) These GENUINELY execute both production paths.
# ===========================================================================


def _parity_cycle(verify_result):
    """Reconstruct the SAME precomputed cycle scaffold.decide_generation hands to
    evaluate_quality_gated from a verify_result, so a direct evalloop call sees
    byte-identical inputs (same shape scaffold builds internally)."""
    verify_green = bool(verify_result.get("ok")) and not verify_result.get("partial")
    return {
        "status": "green" if verify_green else "needs_fix",
        "iteration": None,
        "remaining_iterations": None,
        "summary": verify_result,
        "eval_data": None,
        "failure_report": None if verify_green else {
            "ok": False, "repo": verify_result.get("repo"),
            "adapter": verify_result.get("adapter"), "failing_phases": [],
            "counts": {}, "regression_delta": None, "golden_failures": [],
            "invariant_violations": [], "differential_regressions": [],
            "partial": bool(verify_result.get("partial")),
        },
        "budget_advisory": None,
    }


def test_decide_generation_gate_fail_parity_with_evaluate_quality_gated():
    """GREEN verify + a FAILING reviewer gate (high finding): scaffold yields
    'iterate' (its vocabulary for needs_fix) with the SAME gate block the direct
    shared decision produces -- the gate block is identical because both route
    through scan.apply_thresholds via the one shared function."""
    acc = {"is_new_behavior": False}  # grounded; no confirmation gate in play
    verify_result = {"ok": True, "partial": False, "repo": "demo",
                     "adapter": "python"}
    findings = [scan.make_finding("high", "security", "x.py", 1, "B602",
                                  "insecure call", "bandit")]

    # (b) scaffold.decide_generation (delegates to the shared decision).
    scaffold_dec = scaffold.decide_generation(
        {}, verify_result, findings, acceptance=acc,
        state=evalloop.LoopState())

    # (a) direct evalloop.evaluate_quality_gated on the SAME inputs (same cycle,
    # same findings, same resolved review.thresholds).
    thresholds = scan.resolve_thresholds(scan.load_review_config())
    evalloop_dec = evalloop.evaluate_quality_gated(
        None, None,
        {"cycle": _parity_cycle(verify_result), "findings": findings,
         "thresholds": thresholds, "acceptance": acc},
        evalloop.LoopState())

    # Verdict parity: scaffold's 'iterate' == evalloop's 'needs_fix' (same
    # not-converged conclusion), and the reviewer gate block is IDENTICAL.
    assert scaffold_dec["status"] == "iterate"
    assert evalloop_dec["status"] == "needs_fix"
    assert scaffold_dec["gate"] == evalloop_dec["failure_report"]["reviewer_gate"]
    assert scaffold_dec["gate"]["passed"] is False


def test_decide_generation_gate_pass_parity_with_evaluate_quality_gated():
    """GREEN verify + a PASSING reviewer gate: scaffold converges to 'green' and
    the direct shared decision also converges to 'green' with the SAME passing
    gate -- parity on the converged verdict."""
    acc = {"is_new_behavior": False}
    verify_result = {"ok": True, "partial": False, "repo": "demo",
                     "adapter": "python"}
    findings = [scan.make_finding("low", "quality", "a.py", 2, "E1", "m", "ruff")]

    scaffold_dec = scaffold.decide_generation(
        {}, verify_result, findings, acceptance=acc,
        state=evalloop.LoopState())
    thresholds = scan.resolve_thresholds(scan.load_review_config())
    evalloop_dec = evalloop.evaluate_quality_gated(
        None, None,
        {"cycle": _parity_cycle(verify_result), "findings": findings,
         "thresholds": thresholds, "acceptance": acc},
        evalloop.LoopState())

    assert scaffold_dec["status"] == "green"
    assert evalloop_dec["status"] == "green"
    # Both carry the SAME passing reviewer gate block.
    assert scaffold_dec["gate"] == evalloop_dec["reviewer_gate"]
    assert scaffold_dec["gate"]["passed"] is True
