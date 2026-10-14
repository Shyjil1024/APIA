#!/usr/bin/env python3
"""APIA off-model verification harness (Stage 4 TEST of the ingest -> clarify ->
modify -> test -> apply loop).

This script is the *scriptable* counterpart to the apia-test-authoring skill. It
proves the TEST stage objectively, without a live Copilot session, by driving
only the per-stack adapter's LOCAL test commands:

  (a) BASELINE  - run the project's EXISTING tests to capture the current green
                  baseline (the regression guard).
  (b) REGRESSION - after a change is present, re-run the existing tests to prove
                  no regression was introduced (old behavior still passes).
  (c) NEW       - run the newly added test(s) for the change to prove the new
                  behavior is verified.

It emits a compact machine-readable JSON summary and a human-readable summary.

HARD CONSTRAINT (confirmed with the user; see docs/ARCHITECTURE.md sections 1-3):
this script performs ZERO network and ZERO LLM calls. It is PURE LOCAL TEST
ORCHESTRATION. It must NEVER import ``requests``, ``urllib``-for-network,
``openai``, ``anthropic`` or any other LLM/network SDK, and must never reach a
remote endpoint. Model inference always runs on the user's existing Copilot
session; this harness only runs the adapter's local test commands and reports
pass/fail counts. The ONLY subprocess it ever launches is the stack adapter's
own test command (e.g. ``python -m pytest``).

    # assert: no network/LLM calls anywhere in this module -- the only
    # subprocess spawned is the adapter's local test_command (pytest).

C++ SUPPORT (Phase-2): the harness is stack-agnostic. It selects a result
parser via the adapter's declared ``result_format`` (pytest | ctest | gtest |
msbuild; pytest is the unchanged default). For C++ it also runs a BUILD phase
(cmake configure+build) BEFORE tests so a project with no tests still has a
regression signal, and it still only ever spawns the adapter's own LOCAL
build/test command. A ``--dry-run`` mode resolves and prints the commands that
WOULD run without spawning any subprocess or writing any file, and ``--doctor``
runs the shared offline toolchain preflight.

Usage:
    python3 .apia/scripts/verify.py <repo_path> [--adapter PATH] [--json]
                                    [--new-tests GLOB_OR_PATH ...]
                                    [--baseline-only] [--build-only]
                                    [--dry-run] [--doctor]

Exit code is 0 only when every executed phase passed.
"""

# NOTE: standard-library only (plus the optional PyYAML import guarded below for
# reading the adapter). There is deliberately NO networking/LLM import here --
# see the module docstring's HARD CONSTRAINT.
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

# ingest.py ships the shared, reusable offline toolchain preflight (doctor() /
# format_doctor_report()). Import it so --doctor reuses the SAME preflight
# instead of duplicating it. The scripts dir is already this file's directory,
# so make sure it is importable regardless of the caller's cwd (mirrors how the
# tests put SCRIPTS_DIR on sys.path). This import is a LOCAL stdlib-only module;
# it performs no network/LLM calls (enforced by test_ingest's guard).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import ingest  # noqa: E402  (reused for doctor()/format_doctor_report())
except ImportError:
    ingest = None

# evalloop.py ships the Layer-2 self-correction loop machinery (one deterministic
# verifier cycle, the hard max_iterations cap, the token-budget advisory, the
# acceptance-criteria confirmation gate). It is imported LAZILY inside the
# --iterate CLI path (see main()) so a plain verify() run never pays for it and so
# verify.py has no hard import cycle (evalloop imports verify). Like ingest, it is
# a LOCAL stdlib-only module that performs no network/LLM calls.


# Signal that pytest prints when it collects zero tests (mirrors the adapter's
# no_tests_detection.zero_tests_signal). Used to classify an "empty" run.
ZERO_TESTS_SIGNAL = "collected 0 items"

# pytest phrases a zero-collection result several ways depending on whether it
# ran from testpaths, fell back to recursive discovery, or collected nothing at
# all. The characterization/legacy trigger must recognize all of them, otherwise
# an actually-untested tree reports "has tests" and the no-tests path never
# fires. The adapter's zero_tests_signal is always included as well.
ZERO_TESTS_SIGNALS = (
    "collected 0 items",
    "no tests collected",
    "no tests ran",
)

# Regexes for parsing a pytest summary line, e.g.
#   "4 passed in 0.01s"  /  "1 failed, 3 passed in 0.02s"  /  "collected 0 items"
_COUNT_RE = {
    "passed": re.compile(r"(\d+)\s+passed"),
    "failed": re.compile(r"(\d+)\s+failed"),
    "error": re.compile(r"(\d+)\s+error"),
    "skipped": re.compile(r"(\d+)\s+skipped"),
    "xfailed": re.compile(r"(\d+)\s+xfailed"),
}


def default_adapter_path():
    """Return the Python adapter path shipped next to this script's package."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    apia_dir = os.path.dirname(scripts_dir)
    return os.path.join(apia_dir, "adapters", "python.yml")


def load_adapter(adapter_path):
    """Load the stack adapter (YAML). PyYAML is the only optional dependency.

    Falls back to a minimal built-in map if PyYAML is unavailable, so the
    harness still runs the standard pytest commands.
    """
    try:
        import yaml  # optional; only used to read the declarative adapter
    except ImportError:
        yaml = None

    if yaml is not None and adapter_path and os.path.isfile(adapter_path):
        with open(adapter_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data

    # Minimal fallback mirroring .apia/adapters/python.yml.
    return {
        "name": "python",
        "test_command": "python -m pytest",
        "no_tests_detection": {
            "collect_command": "python -m pytest --collect-only -q",
            "zero_tests_signal": ZERO_TESTS_SIGNAL,
        },
    }


def _split_command(command):
    """Split an adapter command string into argv, replacing the interpreter.

    The Python adapter spells its commands as ``python -m pytest``; we run them
    with the SAME interpreter that launched this harness (``sys.executable``) so
    the harness and the tests share one environment (e.g. the project's .venv).

    This interpreter-rewrite is applied ONLY when the command's argv[0] is
    ``python``/``python3``. For C++ commands (``ctest``/``cmake``/``msbuild`` or
    an absolute tool path such as ``/usr/bin/ctest`` or
    ``C:/.../msbuild.exe``) argv[0] is left UNCHANGED so the real tool runs
    as-is. The MSBuild/absolute-path handling here is unit-tested against fixture
    strings; it is never executed in the Linux sandbox.
    """
    parts = shlex.split(command)
    if parts and parts[0] in ("python", "python3"):
        parts[0] = sys.executable
    return parts


def _parse_pytest_output(stdout, stderr):
    """Extract pass/fail/error/skip counts and the zero-tests flag from output."""
    text = (stdout or "") + "\n" + (stderr or "")
    counts = {}
    for key, rx in _COUNT_RE.items():
        matches = rx.findall(text)
        counts[key] = int(matches[-1]) if matches else 0
    # A run collected zero tests when pytest says so in ANY of its phrasings AND
    # no test actually passed (guards against a stray substring in test output).
    lowered = text.lower()
    counts["no_tests"] = (
        any(sig in lowered for sig in ZERO_TESTS_SIGNALS) and counts["passed"] == 0
    )
    return counts


# --- C++ output parsers -----------------------------------------------------
#
# Every parser below normalizes its runner's output to the SAME counts dict
# shape the pytest parser and run_phase/format_human already consume:
#   {passed, failed, error, skipped, no_tests}
# (pytest additionally carries xfailed; the C++ runners have no xfail concept so
# that key is simply absent for them). This keeps run_phase/format_human and the
# baseline/regression logic completely parser-agnostic.

# CTest summary line, e.g. "100% tests passed, 0 tests failed out of 5".
_CTEST_SUMMARY_RE = re.compile(
    r"(\d+)%\s+tests passed,\s+(\d+)\s+tests failed out of\s+(\d+)"
)
# CTest "Total Tests: N" (collection / -N listing and the zero-tests signal).
_CTEST_TOTAL_RE = re.compile(r"Total Tests:\s+(\d+)")


def _parse_ctest_output(stdout, stderr):
    """Parse ``ctest`` stdout into the normalized counts dict.

    Recognizes the summary line ("X% tests passed, Y tests failed out of N"),
    the "Total Tests: 0" zero-tests signal, and counts per-test "Passed"/
    "Failed" result lines as a fallback. CTest has no error/skipped distinction
    from the summary, so failures land in ``failed``.
    """
    text = (stdout or "") + "\n" + (stderr or "")
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "no_tests": False}

    summary = _CTEST_SUMMARY_RE.search(text)
    if summary:
        failed = int(summary.group(2))
        total = int(summary.group(3))
        counts["failed"] = failed
        counts["passed"] = max(total - failed, 0)
        counts["no_tests"] = total == 0
        return counts

    # No summary line (e.g. a collection run or an aborted run): fall back to the
    # "Total Tests: N" listing and per-test result lines.
    total_match = _CTEST_TOTAL_RE.search(text)
    if total_match and int(total_match.group(1)) == 0:
        counts["no_tests"] = True
        return counts

    passed = 0
    failed = 0
    for line in text.splitlines():
        # Per-test lines look like "1/5 Test #1: Name .....   Passed  0.00 sec".
        if re.search(r"\bPassed\b", line) and "Test #" in line:
            passed += 1
        elif re.search(r"\*{3}Failed|\bFailed\b", line) and "Test #" in line:
            failed += 1
    counts["passed"] = passed
    counts["failed"] = failed
    # Zero tests seen anywhere and the explicit signal present => no tests.
    if passed == 0 and failed == 0:
        counts["no_tests"] = "Total Tests: 0" in text
    return counts


# GoogleTest console summary lines, e.g.
#   "[==========] 5 tests from 3 test suites ran. (0 ms total)"
#   "[  PASSED  ] 5 tests."
#   "[  FAILED  ] 1 test, listed below:"
_GTEST_RAN_RE = re.compile(r"\[=+\]\s+(\d+)\s+tests?\s+from\s+.*ran")
_GTEST_RAN_ALT_RE = re.compile(r"\[=+\]\s+Running\s+(\d+)\s+tests?")
_GTEST_PASSED_RE = re.compile(r"\[\s*PASSED\s*\]\s+(\d+)\s+tests?")
_GTEST_FAILED_RE = re.compile(r"\[\s*FAILED\s*\]\s+(\d+)\s+tests?")


def _parse_gtest_output(stdout, stderr):
    """Parse a GoogleTest binary's console output into the normalized counts dict.

    Reads the "[  PASSED  ] N tests", "[  FAILED  ] N tests" and
    "[==========] N tests ... ran" summary lines. When zero tests ran it sets
    ``no_tests``.
    """
    text = (stdout or "") + "\n" + (stderr or "")
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "no_tests": False}

    ran_match = _GTEST_RAN_RE.search(text) or _GTEST_RAN_ALT_RE.search(text)
    ran = int(ran_match.group(1)) if ran_match else None

    passed_match = _GTEST_PASSED_RE.search(text)
    failed_match = _GTEST_FAILED_RE.search(text)
    counts["passed"] = int(passed_match.group(1)) if passed_match else 0
    counts["failed"] = int(failed_match.group(1)) if failed_match else 0

    if ran is not None:
        counts["no_tests"] = ran == 0
    else:
        counts["no_tests"] = counts["passed"] == 0 and counts["failed"] == 0
    return counts


# MSBuild / VSTest result summary, e.g. (captured fixture strings only; no MSVC
# toolchain exists in this sandbox so this path is unit-tested, never executed):
#   "Passed!  - Failed:     0, Passed:     5, Skipped:     0, Total:     5"
#   "Total tests: 5     Passed: 5     Failed: 0"
_MSBUILD_PASSED_RE = re.compile(r"Passed:\s+(\d+)")
_MSBUILD_FAILED_RE = re.compile(r"Failed:\s+(\d+)")
_MSBUILD_SKIPPED_RE = re.compile(r"Skipped:\s+(\d+)")
_MSBUILD_TOTAL_RE = re.compile(r"Total(?:\s+tests)?:\s+(\d+)")
# A hard MSBuild compile error (e.g. "error C2065", "Build FAILED.").
_MSBUILD_BUILD_FAILED_RE = re.compile(r"Build FAILED", re.IGNORECASE)


def _parse_msbuild_output(stdout, stderr):
    """Parse MSBuild/VSTest console output into the normalized counts dict.

    VSTest prints a "Passed: N, Failed: N, Skipped: N, Total: N" summary. This is
    proven by captured FIXTURE strings in the unit tests; it is never executed in
    this Linux sandbox (no MSVC/MSBuild/VSTest toolchain).
    """
    text = (stdout or "") + "\n" + (stderr or "")
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "no_tests": False}

    passed_match = _MSBUILD_PASSED_RE.search(text)
    failed_match = _MSBUILD_FAILED_RE.search(text)
    skipped_match = _MSBUILD_SKIPPED_RE.search(text)
    total_match = _MSBUILD_TOTAL_RE.search(text)

    counts["passed"] = int(passed_match.group(1)) if passed_match else 0
    counts["failed"] = int(failed_match.group(1)) if failed_match else 0
    counts["skipped"] = int(skipped_match.group(1)) if skipped_match else 0
    if _MSBUILD_BUILD_FAILED_RE.search(text):
        counts["error"] = counts["error"] or 1
    if total_match is not None:
        counts["no_tests"] = int(total_match.group(1)) == 0
    else:
        counts["no_tests"] = (
            counts["passed"] == 0 and counts["failed"] == 0 and counts["skipped"] == 0
        )
    return counts


# result_format -> parser dispatch. pytest is the DEFAULT so the Python path is
# byte-for-byte unchanged for every existing test.
_RESULT_PARSERS = {
    "pytest": _parse_pytest_output,
    "ctest": _parse_ctest_output,
    "gtest": _parse_gtest_output,
    "msbuild": _parse_msbuild_output,
}


def _run(argv, cwd):
    """Run a command locally and capture output. Pure local subprocess.

    This is the ONLY place the harness spawns a process, and it only ever runs
    the adapter's local test command -- never a network client.
    """
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,  # text mode (Python 3.6+ friendly)
    )
    return proc.returncode, proc.stdout, proc.stderr


def resolve_result_format(adapter):
    """Return the result_format for this adapter (pytest | ctest | gtest | msbuild).

    Resolution order:
      1. the adapter's top-level ``result_format`` key, if present;
      2. the ``result_format`` of the adapter's first/default test_framework
         entry (``frameworks[0]`` keyed into ``test_frameworks``);
      3. ``pytest`` as the default (so the Python path is unchanged).
    """
    fmt = adapter.get("result_format")
    if fmt:
        return fmt
    frameworks = adapter.get("frameworks") or []
    tf = adapter.get("test_frameworks") or {}
    if frameworks and isinstance(tf, dict):
        entry = tf.get(frameworks[0]) or {}
        if isinstance(entry, dict) and entry.get("result_format"):
            return entry["result_format"]
    return "pytest"


def _parser_for(result_format):
    """Return the parser callable for a result_format, defaulting to pytest."""
    return _RESULT_PARSERS.get(result_format, _parse_pytest_output)


def resolve_build_command(adapter):
    """Return the adapter's build command, or None when it has none (pytest).

    Prefers the top-level ``build_command``; falls back to the selected build
    system's ``configure_command`` + ``build_command`` joined with ``&&``.
    """
    cmd = adapter.get("build_command")
    if cmd:
        return cmd
    build_systems = adapter.get("build_systems") or {}
    if isinstance(build_systems, dict) and build_systems:
        # Prefer cmake when present (portable default), else the first entry.
        key = "cmake" if "cmake" in build_systems else sorted(build_systems)[0]
        entry = build_systems.get(key) or {}
        parts = [entry.get("configure_command"), entry.get("build_command")]
        parts = [p for p in parts if p]
        if parts:
            return " && ".join(parts)
    return None


def _config_paths_for(repo_path=None):
    """Return candidate .apia/config.yml paths in RESOLUTION ORDER (repo first).

    Mirrors ingest._load_confidence_threshold: the TARGET repo's own
    ``.apia/config.yml`` is consulted first (so a project can pin its own
    ``cpp:`` overrides), then the APIA PACKAGE config shipped next to this
    script. Only paths that exist on disk are returned.
    """
    candidates = []
    if repo_path:
        candidates.append(os.path.join(repo_path, ".apia", "config.yml"))
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    candidates.append(os.path.join(apia_dir, "config.yml"))
    return [c for c in candidates if os.path.isfile(c)]


def load_cpp_overrides(repo_path=None):
    """Return the optional ``cpp:`` overrides block, or {}.

    Resolution is REPO-FIRST (consistent with how ingest resolves
    confidence_threshold): the target repo's own ``.apia/config.yml`` wins, then
    the APIA package config is used as a fallback. The first config file that
    actually declares a ``cpp:`` block provides the overrides.

    This is a FALLBACK: present only when a project pins non-default values
    (build_dir, cmake_path, build_config, test_command_override, build_system).
    Never required for the default CMake + CTest path.
    """
    try:
        import yaml
    except ImportError:
        return {}
    for cfg in _config_paths_for(repo_path):
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            continue
        block = data.get("cpp")
        if isinstance(block, dict):
            return block
    return {}


def _apply_cpp_overrides(adapter, overrides):
    """Return (test_command, build_command) after applying any cpp overrides.

    An explicit ``test_command_override`` wins; otherwise the adapter defaults
    stand. Overrides are consulted only for the cpp adapter and never required.
    """
    test_command = adapter.get("test_command")
    build_command = resolve_build_command(adapter)
    if adapter.get("name") == "cpp" and overrides:
        if overrides.get("test_command_override"):
            test_command = overrides["test_command_override"]
    return test_command, build_command


def run_phase(name, command, repo_path, extra_args=None, result_format="pytest"):
    """Run a single test phase and return a structured result dict.

    ``result_format`` selects the output parser (pytest default). The returned
    dict shape is identical across all formats so callers stay parser-agnostic.
    """
    argv = _split_command(command)
    if extra_args:
        argv = argv + list(extra_args)
    returncode, stdout, stderr = _run(argv, cwd=repo_path)
    counts = _parser_for(result_format)(stdout, stderr)
    # A phase "passed" when pytest exited 0 with no failures/errors AND it is not
    # a zero-tests run (an empty run is not evidence of anything).
    passed = (
        returncode == 0
        and counts["failed"] == 0
        and counts["error"] == 0
        and not counts["no_tests"]
    )
    return {
        "phase": name,
        "command": " ".join(argv),
        "returncode": returncode,
        "passed": passed,
        "counts": counts,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def run_build_phase(command, repo_path):
    """Run the adapter's build_command and report pass/fail from the exit code.

    This is the BUILD-ONLY validation phase ("does it still compile?"). It runs
    NO tests; the phase passes iff the build process exits 0. For a C++ project
    with no tests this is the only regression signal available, so it must be
    able to stand alone. The result dict matches run_phase's shape (a zeroed
    counts dict) so it flows through summary['phases'] / format_human / ok.

    The command may chain configure+build with ``&&`` (as the cpp adapter's
    default build_command does); it is run via a shell-free split per subcommand.
    """
    results = []
    returncode = 0
    stdout_all = []
    stderr_all = []
    # Support "cmake -S . -B build && cmake --build build" by running each
    # &&-joined subcommand in order and stopping on the first failure. No shell
    # is invoked (still a direct, local subprocess per subcommand).
    for sub in [c.strip() for c in command.split("&&")]:
        if not sub:
            continue
        argv = _split_command(sub)
        rc, out, err = _run(argv, cwd=repo_path)
        stdout_all.append(out or "")
        stderr_all.append(err or "")
        results.append(sub)
        returncode = rc
        if rc != 0:
            break
    passed = returncode == 0
    return {
        "phase": "build",
        "command": command,
        "returncode": returncode,
        "passed": passed,
        "counts": {"passed": 0, "failed": 0, "error": 0, "skipped": 0,
                   "no_tests": False},
        "stdout_tail": _tail("\n".join(stdout_all)),
        "stderr_tail": _tail("\n".join(stderr_all)),
    }


def _tail(text, max_lines=15):
    """Keep only the last few lines of output so the summary stays compact."""
    if not text:
        return ""
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-max_lines:])


class StubAdapterError(RuntimeError):
    """Raised when the harness is pointed at a stub adapter (null test_command).

    The Phase-1 stubs (cpp/csharp/groovy) spell every command as ``null`` with a
    ``TODO(phase-2)`` marker. Silently defaulting those to pytest would run the
    wrong test runner against a non-Python repo, so we refuse instead.
    """


def _test_command(adapter):
    """Return the adapter's declared test command, refusing stub adapters.

    An adapter whose ``test_command`` is null/empty is a Phase-1 stub (see
    .apia/adapters/{cpp,csharp,groovy}.yml). Defaulting such an adapter to
    ``python -m pytest`` would silently run pytest against a C#/C++/Groovy repo,
    so we fail closed with a clear, actionable error.
    """
    command = adapter.get("test_command")
    if not command:
        name = adapter.get("name", "unknown")
        raise StubAdapterError(
            "adapter {!r} declares no test_command (it is a Phase-1 stub). "
            "The verify harness refuses to default to 'python -m pytest' for a "
            "non-Python stack. Fill in the adapter's test_command (see "
            "TODO(phase-2) markers) or point --adapter at a complete adapter "
            "such as .apia/adapters/python.yml.".format(name)
        )
    return command


def _collect_command(adapter):
    ntd = adapter.get("no_tests_detection") or {}
    return ntd.get("collect_command") or "python -m pytest --collect-only -q"


# CTest "-N" listing line, e.g. "  Test #1: InventoryTest.ReordersWhenLow".
_CTEST_LISTING_RE = re.compile(r"Test\s+#\d+:\s+(\S.*)$")


def _parse_pytest_nodes(text):
    """Parse ``pytest --collect-only -q`` output into node ids (unchanged).

    pytest -q --collect-only prints node ids containing ``::``; the summary
    footer lines ("N tests collected", "no tests ran") do not. This is the
    EXACT, byte-for-byte original pytest behavior and must not change.
    """
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(("<", "=")):
            nodes.append(line)
    return nodes


def _parse_ctest_nodes(text):
    """Parse ``ctest -N`` output into stable test-name node ids.

    ``ctest -N`` lists each registered test as ``  Test #N: <name>`` and ends
    with ``Total Tests: N``. We key the node set on the test NAME (not the
    volatile ``#N`` index), prefixed with ``ctest::`` so a C++ node id is never
    mistaken for a pytest ``path::func`` id. This gives the removal check a real
    oracle for ctest: a deleted/renamed C++ test drops out of the set and is
    flagged as a removed node, mirroring the pytest guarantee.
    """
    nodes = []
    for line in text.splitlines():
        m = _CTEST_LISTING_RE.search(line)
        if m:
            name = m.group(1).strip()
            if name:
                nodes.append("ctest::" + name)
    return nodes


# result_format -> collect-output node parser. pytest is the default so the
# Python path is byte-for-byte unchanged.
_NODE_PARSERS = {
    "pytest": _parse_pytest_nodes,
    "ctest": _parse_ctest_nodes,
    # gtest/msbuild list tests differently and are not executed in-sandbox; they
    # fall through to pytest's parser (returning [] for their listings), so the
    # counts-based regression check still applies while removal-detection is a
    # no-op for them (documented in compare_to_baseline).
}


def collect_nodes(adapter, repo_path, targets=None, result_format="pytest"):
    """Return the sorted list of test node ids the suite currently collects.

    Runs the adapter's collect_command and parses the node ids it prints. The
    parser is selected by ``result_format``:
      * pytest -> ``path::func`` node ids (unchanged default behavior);
      * ctest  -> ``ctest::<test-name>`` ids parsed from ``ctest -N``'s listing,
        so a deleted/renamed C++ test is caught by regression-by-removal.
    This is the "what tests exist" oracle used to persist a baseline node set and
    to detect regression-by-removal (a pre-change test that disappeared).
    """
    argv = _split_command(_collect_command(adapter))
    if targets:
        argv = argv + list(targets)
    _, stdout, stderr = _run(argv, cwd=repo_path)
    text = (stdout or "") + "\n" + (stderr or "")
    parser = _NODE_PARSERS.get(result_format, _parse_pytest_nodes)
    return sorted(set(parser(text)))


def detect_no_tests(adapter, repo_path):
    """Return True when the repo currently has no collectable tests (legacy case).

    Uses the adapter's no_tests_detection.collect_command and zero_tests_signal.
    """
    ntd = adapter.get("no_tests_detection") or {}
    adapter_signal = ntd.get("zero_tests_signal") or ZERO_TESTS_SIGNAL
    # Recognize the adapter's declared signal plus pytest's other zero-collection
    # phrasings (testpaths-fallback, "no tests ran", etc.).
    signals = set(ZERO_TESTS_SIGNALS) | {adapter_signal}
    argv = _split_command(_collect_command(adapter))
    returncode, stdout, stderr = _run(argv, cwd=repo_path)
    text = ((stdout or "") + "\n" + (stderr or "")).lower()
    # Collection of a non-empty suite prints node ids ("::"); if we see any, the
    # tree is NOT untested regardless of other phrasing.
    if "::" in text:
        return False
    return any(sig.lower() in text for sig in signals)


DEFAULT_BASELINE_FILE = ".apia-baseline.json"


def save_baseline(path, summary, node_set):
    """Persist a pre-change baseline (pass/fail counts + collected node set).

    Written by ``--baseline-only`` BEFORE a change is applied so the regression
    phase of a LATER (post-change) run can compare against it and demonstrate a
    genuine before/after delta instead of re-running the same disk state twice.
    """
    baseline_phase = summary["phases"][0]
    payload = {
        "repo": summary["repo"],
        "test_command": summary["test_command"],
        "counts": baseline_phase["counts"],
        "passed": baseline_phase["passed"],
        "nodes": sorted(node_set),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    return payload


def load_baseline(path):
    """Load a persisted baseline, or None if it is absent/unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def compare_to_baseline(stored, current_counts, current_nodes):
    """Compare a post-change existing-suite run to a stored pre-change baseline.

    Returns a dict describing the before/after delta. The regression guarantee is
    "no pre-change passing test now fails and no pre-change test disappeared":

      - ``removed_nodes``: pre-change node ids no longer collectable (a test the
        change deleted/renamed -> potential silent loss of coverage). This now
        works for both pytest (``path::func`` ids) and ctest (``ctest::<name>``
        ids from ``ctest -N``); gtest/msbuild listings are not parsed into nodes
        (not executed in-sandbox), so for those the counts-based delta below is
        the active regression signal and removal-detection is a no-op.
      - ``passed_delta``: current passed minus baseline passed (negative means
        fewer tests pass than before -> regression).
      - ``regressed``: True when the change broke the pre-change guarantee.
    """
    stored_nodes = set(stored.get("nodes", []))
    current_nodes = set(current_nodes)
    removed = sorted(stored_nodes - current_nodes)
    added = sorted(current_nodes - stored_nodes)
    stored_passed = stored.get("counts", {}).get("passed", 0)
    passed_delta = current_counts.get("passed", 0) - stored_passed
    new_failures = current_counts.get("failed", 0) + current_counts.get("error", 0)
    regressed = bool(removed) or passed_delta < 0 or new_failures > 0
    return {
        "baseline_passed": stored_passed,
        "current_passed": current_counts.get("passed", 0),
        "passed_delta": passed_delta,
        "removed_nodes": removed,
        "added_nodes": added,
        "new_failures": new_failures,
        "regressed": regressed,
    }


def resolve_plan(repo_path, adapter_path=None, new_tests=None,
                 baseline_only=False, build_only=False, baseline_file=None):
    """Resolve WHICH commands a verify() run would spawn, without spawning any.

    Returns a dict with the resolved adapter facts and the ordered list of
    planned (phase, command) pairs. Used by --dry-run / --explain to report the
    plan + reasoning with ZERO subprocesses and ZERO file writes, and internally
    by verify() so the plan and the real run agree.
    """
    repo_path = os.path.abspath(repo_path)
    adapter_path = adapter_path or default_adapter_path()
    adapter = load_adapter(adapter_path)
    test_command = _test_command(adapter)
    overrides = load_cpp_overrides(repo_path) if adapter.get("name") == "cpp" else {}
    test_command, build_command = _apply_cpp_overrides(adapter, overrides) \
        if overrides else (test_command, resolve_build_command(adapter))
    result_format = resolve_result_format(adapter)
    is_cpp = adapter.get("name") == "cpp"
    baseline_file = baseline_file or os.path.join(repo_path, DEFAULT_BASELINE_FILE)

    planned = []
    # BUILD phase runs first for C++ (compile-before-test, so the test runner has
    # fresh binaries) or whenever --build-only is requested and the adapter has a
    # build command. It runs in baseline-only mode too (the baseline tests need
    # the build dir to exist first).
    run_build = bool(build_command) and (build_only or is_cpp)
    if run_build:
        planned.append(("build", build_command))
    if not build_only:
        planned.append(("baseline", test_command))
        if not baseline_only:
            planned.append(("regression", test_command))
            if new_tests:
                planned.append(("new", test_command))

    reasoning = [
        "adapter: {} ({})".format(adapter.get("name", "unknown"), adapter_path),
        "result_format: {}".format(result_format),
        "test_command: {}".format(test_command),
        "build_command: {}".format(build_command if build_command else "(none)"),
        "build_system(s): {}".format(
            ", ".join(sorted((adapter.get("build_systems") or {}).keys())) or "(n/a)"),
        "test_framework(s): {}".format(
            ", ".join(adapter.get("frameworks") or []) or "(n/a)"),
    ]
    if overrides:
        reasoning.append("cpp overrides applied: {}".format(sorted(overrides.keys())))
    return {
        "repo_path": repo_path,
        "adapter": adapter,
        "adapter_name": adapter.get("name", "unknown"),
        "adapter_path": adapter_path,
        "test_command": test_command,
        "build_command": build_command,
        "result_format": result_format,
        "is_cpp": is_cpp,
        "run_build": run_build,
        "overrides": overrides,
        "baseline_file": baseline_file,
        "planned_commands": [{"phase": ph, "command": cmd} for ph, cmd in planned],
        "reasoning": reasoning,
    }


def preflight_missing_tool(plan):
    """Return a clear actionable message when a required tool is missing, else None.

    Looks at the resolved build/test commands, finds argv[0] of each, and uses
    shutil.which to check reachability BEFORE spawning anything. This turns a
    confusing subprocess OSError (e.g. ctest absent, or MSBuild requested with no
    Developer shell) into an upfront, legible message. The pytest path uses
    sys.executable, which always resolves, so this never affects Python runs.
    """
    checked = set()
    cmds = [plan["build_command"], plan["test_command"]]
    for cmd in cmds:
        if not cmd:
            continue
        for sub in cmd.split("&&"):
            parts = _split_command(sub)
            if not parts:
                continue
            exe = parts[0]
            if exe == sys.executable or exe in checked:
                continue
            checked.add(exe)
            # Absolute path: check existence directly; else PATH lookup.
            found = (os.path.isfile(exe) if os.path.isabs(exe)
                     else shutil.which(exe) is not None)
            if not found:
                if os.path.basename(exe).lower() in ("msbuild", "msbuild.exe",
                                                      "cl", "cl.exe",
                                                      "vstest.console.exe"):
                    return (
                        "required tool '{}' is not reachable. On Windows it is "
                        "available only inside the 'VS 2022 Developer Command "
                        "Prompt' (which sets up MSBuild/cl/VSTest). Launch APIA "
                        "from that Developer shell, or run --doctor to preflight."
                        .format(exe)
                    )
                return (
                    "required tool '{}' is not reachable (not on PATH / not an "
                    "existing path). Install it or run --doctor to preflight "
                    "before this {} run.".format(exe, plan["adapter_name"])
                )
    return None


def verify(repo_path, adapter_path=None, new_tests=None, baseline_only=False,
           baseline_file=None, build_only=False, dry_run=False):
    """Run the verification phases and return a summary dict.

    Phases:
      - build:      (C++ only, or any --build-only run) the adapter build_command
                    (cmake configure+build); pass/fail from the exit code, no
                    tests. Runs FIRST so a no-test project still has a regression
                    signal. Skipped for the pytest path (no build_command).
      - baseline:   the project's EXISTING tests, with the ``--new-tests`` target
                    EXCLUDED so "existing suite" is genuinely the pre-change set.
      - regression: the existing suite again; when a stored baseline captured by a
                    prior ``--baseline-only`` run exists, it is compared against
                    that baseline (counts + collected node set) so a true
                    before/after delta is demonstrated rather than a blind repeat.
      - new:        only the newly added test target(s), if provided.

    When ``baseline_only`` is True, only the baseline phase runs and its result
    (counts + collected node set) is persisted to ``baseline_file`` so it can be
    consumed by a later post-change run.

    When ``build_only`` is True, ONLY the build phase runs (compile-pass/fail is
    the regression signal). When ``dry_run`` is True, NO subprocess is spawned
    and NO file is written: the resolved plan is returned with dry_run=True.
    """
    plan = resolve_plan(repo_path, adapter_path=adapter_path, new_tests=new_tests,
                        baseline_only=baseline_only, build_only=build_only,
                        baseline_file=baseline_file)
    repo_path = plan["repo_path"]
    adapter = plan["adapter"]
    test_command = plan["test_command"]
    build_command = plan["build_command"]
    result_format = plan["result_format"]
    baseline_file = plan["baseline_file"]

    if dry_run:
        # Explain-the-plan: resolve and report, writing nothing, spawning nothing.
        return {
            "repo": os.path.basename(repo_path.rstrip(os.sep)),
            "repo_path": repo_path,
            "adapter": plan["adapter_name"],
            "adapter_path": plan["adapter_path"],
            "test_command": test_command,
            "build_command": build_command,
            "result_format": result_format,
            "dry_run": True,
            "baseline_only": bool(baseline_only),
            "build_only": bool(build_only),
            "baseline_file": baseline_file,
            "had_no_tests_before": None,
            "planned_commands": plan["planned_commands"],
            "reasoning": plan["reasoning"],
            "phases": [],
            "ok": True,
        }

    # Exclude the new-test target(s) from the existing-suite phases so the
    # baseline/regression collection is the pre-change suite, not "everything on
    # disk including the committed new test". pytest's --deselect does this per
    # node/path. These flags are pytest-specific; other runners (ctest/gtest/
    # msbuild) select tests differently, so we only apply them for pytest and
    # otherwise rely on the runner running its full registered suite.
    existing_suite_args = []
    if result_format == "pytest":
        for t in (new_tests or []):
            existing_suite_args.extend(["--ignore=" + t, "--deselect", t])

    phases = []
    regression_delta = None

    # (0) BUILD: compile-before-test / build-only regression signal.
    if plan["run_build"]:
        build = run_build_phase(build_command, repo_path)
        phases.append(build)
        # If the build failed, running tests against stale/absent binaries is
        # pointless; a build-only run stops here by definition.
        build_failed = not build["passed"]
    else:
        build_failed = False

    if build_only:
        # Build-only: the compile result IS the verification outcome.
        all_passed = all(p["passed"] for p in phases)
        summary = {
            "repo": os.path.basename(repo_path.rstrip(os.sep)),
            "repo_path": repo_path,
            "adapter": plan["adapter_name"],
            "adapter_path": plan["adapter_path"],
            "test_command": test_command,
            "build_command": build_command,
            "result_format": result_format,
            "build_only": True,
            "had_no_tests_before": None,
            "baseline_only": False,
            "baseline_file": baseline_file,
            "baseline_compared": False,
            "regression_delta": None,
            "existing_suite_nodes": [],
            "phases": phases,
            "ok": all_passed,
        }
        return summary

    no_tests = detect_no_tests(adapter, repo_path)
    # Node set of the EXISTING suite (new-test target excluded) -- the oracle for
    # the persisted baseline and for regression-by-removal detection.
    existing_nodes = [
        n for n in collect_nodes(adapter, repo_path, result_format=result_format)
        if not any(t in n for t in (new_tests or []))
    ]

    # (a) BASELINE: the project's existing test suite (new tests excluded).
    baseline = run_phase("baseline", test_command, repo_path,
                         extra_args=existing_suite_args or None,
                         result_format=result_format)
    phases.append(baseline)

    if baseline_only:
        # Persist the pre-change baseline for a later post-change comparison.
        save_baseline(baseline_file, {
            "repo": os.path.basename(repo_path.rstrip(os.sep)),
            "test_command": test_command,
            "phases": [baseline],
        }, existing_nodes)
    else:
        # (b) REGRESSION: re-run the existing suite. If a baseline was captured
        # before the change, compare against it so the "no regression" claim is a
        # real before/after delta, not a blind repeat of the same disk state.
        regression = run_phase("regression", test_command, repo_path,
                               extra_args=existing_suite_args or None,
                               result_format=result_format)
        stored = load_baseline(baseline_file)
        if stored is not None:
            regression_delta = compare_to_baseline(
                stored, regression["counts"], existing_nodes
            )
            # A regression against the stored baseline fails the phase even if the
            # current run is self-consistently green.
            regression["passed"] = regression["passed"] and not regression_delta["regressed"]
            regression["baseline_comparison"] = regression_delta
        phases.append(regression)

        # (c) NEW: run only the newly added tests, when the caller points at them.
        if new_tests:
            new_result = run_phase("new", test_command, repo_path,
                                   extra_args=list(new_tests),
                                   result_format=result_format)
            phases.append(new_result)

    all_passed = all(p["passed"] for p in phases)
    summary = {
        "repo": os.path.basename(repo_path.rstrip(os.sep)),
        "repo_path": repo_path,
        "adapter": plan["adapter_name"],
        "adapter_path": plan["adapter_path"],
        "test_command": test_command,
        "build_command": build_command,
        "result_format": result_format,
        "had_no_tests_before": no_tests,
        "baseline_only": bool(baseline_only),
        "baseline_file": baseline_file,
        "baseline_compared": regression_delta is not None,
        "regression_delta": regression_delta,
        "existing_suite_nodes": existing_nodes,
        "phases": phases,
        "ok": all_passed,
    }
    return summary


def format_human(summary):
    """Render a compact human-readable summary of a verify() result."""
    lines = []
    lines.append("APIA verify :: {} (adapter: {})".format(summary["repo"], summary["adapter"]))
    lines.append("  test command: {}".format(summary["test_command"]))
    if summary.get("build_command"):
        lines.append("  build command: {}".format(summary["build_command"]))

    # --dry-run / --explain: report the resolved plan + reasoning, nothing ran.
    if summary.get("dry_run"):
        lines.append("  mode: DRY-RUN (no subprocess spawned, no file written)")
        lines.append("  reasoning:")
        for r in summary.get("reasoning", []):
            lines.append("    - {}".format(r))
        lines.append("  planned commands (in order):")
        for pc in summary.get("planned_commands", []):
            lines.append("    [{:<10}] {}".format(pc["phase"], pc["command"]))
        lines.append("  => DRY-RUN OK (plan resolved; run without --dry-run to execute)")
        return "\n".join(lines)

    if summary.get("had_no_tests_before"):
        lines.append("  note: no tests detected before this run "
                     "(legacy / characterization path).")
    if summary.get("build_only"):
        lines.append("  note: build-only run; compile result is the regression "
                     "signal (no tests executed).")
    if summary.get("baseline_only"):
        lines.append("  note: baseline-only run; captured pre-change baseline to {}".format(
            summary.get("baseline_file")))
    for p in summary["phases"]:
        c = p["counts"]
        status = "PASS" if p["passed"] else "FAIL"
        if p["phase"] == "build":
            detail = "compiled OK" if p["passed"] else "compile FAILED (rc={})".format(
                p.get("returncode"))
            lines.append("  [{}] {:<10} {}".format(status, p["phase"], detail))
            continue
        detail = "{} passed, {} failed, {} error, {} skipped".format(
            c["passed"], c["failed"], c["error"], c["skipped"]
        )
        if c["no_tests"]:
            detail += " (collected 0 items)"
        lines.append("  [{}] {:<10} {}".format(status, p["phase"], detail))
    delta = summary.get("regression_delta")
    if delta is not None:
        verdict = "REGRESSION" if delta["regressed"] else "no regression vs. baseline"
        lines.append("  baseline delta: passed {:+d} ({} -> {}); removed {} node(s) => {}".format(
            delta["passed_delta"], delta["baseline_passed"], delta["current_passed"],
            len(delta["removed_nodes"]), verdict))
    elif not summary.get("baseline_only") and not summary.get("build_only"):
        lines.append("  baseline delta: none captured (run --baseline-only on the "
                     "pre-change tree first for a true before/after delta).")
    lines.append("  => {}".format("ALL GREEN" if summary["ok"] else "VERIFICATION FAILED"))
    return "\n".join(lines)


def _format_iterate_human(decision, emit_report=False):
    """Render a compact human summary of an evalloop.evaluate() decision.

    Prints the status (GREEN / NEEDS FIX / STOP / NEEDS CONFIRMATION), the
    iteration counter against the hard cap, the token-budget advisory, and -- when
    ``emit_report`` is set and a structured failure report exists -- the
    JSON-serializable failure report a model would consume. This is the
    DETERMINISTIC machinery's output only; the model fix is a LIVE Copilot step.
    """
    status = decision.get("status")
    cap = decision.get("max_iterations")
    it = decision.get("iteration")
    lines = ["APIA iterate :: one deterministic self-correction cycle"]
    lines.append("  iteration {} / cap {}".format(it, cap))

    advisory = decision.get("budget_advisory")
    if advisory:
        state = "within budget" if advisory.get("within_budget") else "OVER BUDGET"
        lines.append("  token budget: ~{} tokens vs {} ({})".format(
            advisory.get("estimated_tokens"), advisory.get("budget"), state))
        if advisory.get("warning"):
            lines.append("  advisory: {}".format(advisory["warning"]))

    # Reviewer-gate dimension (iterate-to-best): when the gate was in play, show
    # whether the configured review.thresholds passed and why not. ``reviewer_gate``
    # is None when no --findings were supplied (gate not in play).
    gate = decision.get("reviewer_gate")
    if gate is not None:
        if gate.get("passed"):
            lines.append("  reviewer gate: PASSED (meets review.thresholds)")
        else:
            lines.append("  reviewer gate: FAILED -- {}".format(
                "; ".join(gate.get("reasons") or ["thresholds breached"])))
    else:
        lines.append("  reviewer gate: not in play (no --findings; tests-green "
                     "mode)")

    # Best-so-far: the BEST attempt reached across iterations (not necessarily
    # the last), so a stop presents the best, not the final, attempt.
    best = decision.get("best_so_far")
    if best:
        score = best.get("score") or {}
        lines.append("  best so far: iteration {} (residual score {}, {} "
                     "finding(s), {} failing phase(s))".format(
                         best.get("iteration"), score.get("score"),
                         score.get("finding_count"),
                         score.get("failing_phase_count")))

    if status == "green":
        lines.append("  => GREEN (tests pass AND reviewer gate meets the "
                     "configured thresholds; the loop stops -- best reached)")
    elif status == "needs_confirmation":
        lines.append("  => NEEDS CONFIRMATION (anti-circularity gate)")
        lines.append("  ask: {}".format(decision.get("ask")))
    elif status == "stop":
        lines.append("  => STOP ({}) -- the loop will NOT auto-apply a "
                     "sub-threshold change".format(decision.get("reason")))
        lines.append("  ask: {}".format(decision.get("ask")))
    else:  # needs_fix
        lines.append("  => NEEDS FIX (hand the structured failure -- including "
                     "any reviewer findings -- to the live model, then re-run "
                     "the next cycle)")

    report = decision.get("failure_report")
    if emit_report:
        if report is not None:
            lines.append("  --- structured failure report (model-consumable) ---")
            lines.append(json.dumps(report, indent=2, sort_keys=True, default=str))
        elif status == "green":
            lines.append("  --- no failure report: cycle is GREEN ---")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA verification harness (pure local test orchestration; "
            "no LLM/network calls)."
        )
    )
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="Path to the repository to verify (optional for --doctor).",
    )
    parser.add_argument(
        "--adapter",
        default=None,
        help="Path to the stack adapter YAML (default: .apia/adapters/python.yml).",
    )
    parser.add_argument(
        "--new-tests",
        nargs="*",
        default=None,
        help="One or more test file paths / node ids (relative to repo) for the "
             "NEW-behavior phase.",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Only run the baseline (existing tests) phase and persist its "
             "counts + collected node set to --baseline-file. Run this on the "
             "PRE-change tree, then re-run (without this flag) after the change "
             "for a true before/after regression delta.",
    )
    parser.add_argument(
        "--baseline-file",
        default=None,
        help="Where to read/write the persisted baseline (default: "
             "<repo>/.apia-baseline.json).",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Only run the BUILD phase (adapter build_command). Compile pass/fail "
             "is the sole regression signal -- essential for a C++ project with "
             "no tests yet.",
    )
    parser.add_argument(
        "--dry-run",
        "--explain",
        dest="dry_run",
        action="store_true",
        help="Resolve and print the build/test commands that WOULD run plus the "
             "detected build-system/framework reasoning, then exit WITHOUT "
             "spawning any subprocess or writing any baseline file.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run the shared offline toolchain preflight (reachable tools + "
             "versions + VS Developer-shell / missing-tool warnings) and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable JSON summary (in addition to the human one).",
    )
    parser.add_argument(
        "--iterate",
        action="store_true",
        help="Run ONE deterministic Layer-2 self-correction CYCLE (assemble "
             "Layer-1 eval data -> run the verify phases -> decide green/stop/ask) "
             "and print the result. This runs the DETERMINISTIC machinery only; "
             "the model fix between cycles is a LIVE Copilot action, never done "
             "here. Honors the hard max_iterations cap and the token-budget "
             "advisory from .apia/config.yml.",
    )
    parser.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=None,
        help="Override the hard max_iterations cap for an --iterate run (default: "
             "the config.yml value). Lets a headless proof drive the cap "
             "deterministically; the loop is ALWAYS bounded by it.",
    )
    parser.add_argument(
        "--emit-failure-report",
        dest="emit_failure_report",
        action="store_true",
        help="With --iterate, print the STRUCTURED, JSON-serializable failure "
             "report a model would consume (or GREEN) plus the stop/ask decision.",
    )
    parser.add_argument(
        "--findings",
        dest="findings",
        default=None,
        help="With --iterate: path to a JSON file of NORMALIZED scanner findings "
             "(the scan.py --json 'findings' list, or the whole scan report) that "
             "turns on the REVIEWER GATE so the loop targets green tests AND the "
             "configured review.thresholds (iterate-to-best). When omitted the "
             "fused decision degrades to the tests-green behavior (backward "
             "compatible). The findings come from a LIVE scan; this path only "
             "APPLIES the thresholds -- it never scans or calls a model.",
    )
    args = parser.parse_args(argv)

    # --doctor: shared offline preflight. A repo_path is OPTIONAL but, when a
    # real directory is given, it is used to resolve which tools are REQUIRED for
    # that repo's detected stack (so the examples in PROOF.md/USAGE that pass
    # samples/cppapp are meaningful: a C++ repo requires cmake/ctest, not msbuild
    # on Linux).
    if args.doctor:
        if ingest is None:
            parser.error("doctor preflight unavailable: could not import ingest.py")
        doctor_repo = (args.repo_path
                       if args.repo_path and os.path.isdir(args.repo_path)
                       else None)
        report = ingest.doctor(repo_path=doctor_repo)
        print(ingest.format_doctor_report(report), end="")
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        # Exit non-zero ONLY when a REQUIRED tool for the resolved stack is
        # missing, so a preflight can gate a run without false-failing on absent
        # optional/other-platform tools (msbuild/cl on Linux, standalone pytest).
        return 0 if not report["missing_required"] else 1

    if not args.repo_path:
        parser.error("repo_path is required (except with --doctor).")
    if not os.path.isdir(args.repo_path):
        parser.error("repo_path is not a directory: {}".format(args.repo_path))

    # Up-front missing-tool preflight for the resolved commands (skips dry-run,
    # which spawns nothing): turn a confusing subprocess error into a clear
    # actionable message when e.g. ctest/MSBuild are unavailable.
    if not args.dry_run:
        try:
            plan = resolve_plan(
                args.repo_path, adapter_path=args.adapter, new_tests=args.new_tests,
                baseline_only=args.baseline_only, build_only=args.build_only,
                baseline_file=args.baseline_file,
            )
        except StubAdapterError as exc:
            parser.error(str(exc))
        missing = preflight_missing_tool(plan)
        if missing:
            parser.error(missing + " (run 'verify.py --doctor' for details)")

    # --iterate: run ONE deterministic Layer-2 self-correction cycle via the
    # evalloop machinery and report green/stop/ask + the structured failure
    # report. This is the headless proof surface for the loop; the model fix
    # between cycles is a LIVE Copilot action, never performed here. evalloop is
    # imported lazily so a plain verify() run never pays for it and there is no
    # hard import cycle (evalloop imports verify).
    if args.iterate:
        try:
            import evalloop  # noqa: E402  (lazy; Layer-2 loop machinery)
        except ImportError as exc:
            parser.error("iterate machinery unavailable: {}".format(exc))
        adapter = load_adapter(args.adapter or default_adapter_path())
        iterate_options = {
            "adapter_path": args.adapter,
            "new_tests": args.new_tests,
            "baseline_file": args.baseline_file,
            "build_only": args.build_only,
            "max_iterations": args.max_iterations,
        }
        # --findings turns on the REVIEWER GATE (iterate-to-best): load the
        # NORMALIZED scanner findings produced by a LIVE scan (scan.py --json)
        # and hand them to the fused decision. Accept either the raw findings
        # list or a full scan report (whose ``findings`` key holds the list).
        # When absent, the gate is not in play and behavior matches today.
        if args.findings is not None:
            try:
                with open(args.findings, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except (OSError, ValueError) as exc:
                parser.error("could not read --findings {!r}: {}".format(
                    args.findings, exc))
            if isinstance(loaded, dict):
                loaded = loaded.get("findings") or []
            if not isinstance(loaded, list):
                parser.error("--findings must be a JSON list of normalized "
                             "findings or a scan report with a 'findings' list")
            iterate_options["findings"] = loaded
        try:
            decision = evalloop.evaluate(args.repo_path, adapter, iterate_options)
        except StubAdapterError as exc:
            parser.error(str(exc))
        print(_format_iterate_human(decision, emit_report=args.emit_failure_report))
        if args.json:
            print(json.dumps(decision, indent=2, sort_keys=True, default=str))
        # Exit 0 only when the single cycle is green; a needs_fix/stop/
        # needs_confirmation decision exits non-zero so a proof can gate on it.
        return 0 if decision.get("status") == "green" else 1

    try:
        summary = verify(
            args.repo_path,
            adapter_path=args.adapter,
            new_tests=args.new_tests,
            baseline_only=args.baseline_only,
            baseline_file=args.baseline_file,
            build_only=args.build_only,
            dry_run=args.dry_run,
        )
    except StubAdapterError as exc:
        parser.error(str(exc))

    print(format_human(summary))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
