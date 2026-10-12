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

Usage:
    python3 .apia/scripts/verify.py <repo_path> [--adapter PATH] [--json]
                                    [--new-tests GLOB_OR_PATH ...]
                                    [--baseline-only]

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
import subprocess
import sys


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

    The adapter spells its commands as ``python -m pytest``; we run them with the
    SAME interpreter that launched this harness (``sys.executable``) so the
    harness and the tests share one environment (e.g. the project's .venv).
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


def run_phase(name, command, repo_path, extra_args=None):
    """Run a single test phase and return a structured result dict."""
    argv = _split_command(command)
    if extra_args:
        argv = argv + list(extra_args)
    returncode, stdout, stderr = _run(argv, cwd=repo_path)
    counts = _parse_pytest_output(stdout, stderr)
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


def collect_nodes(adapter, repo_path, targets=None):
    """Return the sorted list of pytest node ids the suite currently collects.

    Runs the adapter's collect_command (``pytest --collect-only -q``) and parses
    the node ids it prints (one per line, e.g. ``tests/test_x.py::test_y``). This
    is the "what tests exist" oracle used to persist a baseline node set and to
    detect regression-by-removal (a pre-change test that disappeared).
    """
    argv = _split_command(_collect_command(adapter))
    if targets:
        argv = argv + list(targets)
    _, stdout, stderr = _run(argv, cwd=repo_path)
    text = (stdout or "") + "\n" + (stderr or "")
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        # pytest -q --collect-only prints node ids containing "::"; the summary
        # footer lines ("N tests collected", "no tests ran") do not.
        if "::" in line and not line.startswith(("<", "=")):
            nodes.append(line)
    return sorted(set(nodes))


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
        change deleted/renamed -> potential silent loss of coverage).
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


def verify(repo_path, adapter_path=None, new_tests=None, baseline_only=False,
           baseline_file=None):
    """Run the verification phases and return a summary dict.

    Phases:
      - baseline:   the project's EXISTING tests, with the ``--new-tests`` target
                    EXCLUDED so "existing suite" is genuinely the pre-change set.
      - regression: the existing suite again; when a stored baseline captured by a
                    prior ``--baseline-only`` run exists, it is compared against
                    that baseline (counts + collected node set) so a true
                    before/after delta is demonstrated rather than a blind repeat.
      - new:        only the newly added test target(s), if provided.

    When ``baseline_only`` is True, only the baseline phase runs and its result
    (counts + collected node set) is persisted to ``baseline_file`` so it can be
    consumed by a later post-change run. Capturing on the pre-change tree, then
    running again after the change, is what makes the regression guarantee
    self-contained.
    """
    repo_path = os.path.abspath(repo_path)
    adapter_path = adapter_path or default_adapter_path()
    adapter = load_adapter(adapter_path)
    test_command = _test_command(adapter)
    baseline_file = baseline_file or os.path.join(repo_path, DEFAULT_BASELINE_FILE)

    # Exclude the new-test target(s) from the existing-suite phases so the
    # baseline/regression collection is the pre-change suite, not "everything on
    # disk including the committed new test". pytest's --deselect does this per
    # node/path.
    existing_suite_args = []
    for t in (new_tests or []):
        existing_suite_args.extend(["--ignore=" + t, "--deselect", t])

    phases = []
    no_tests = detect_no_tests(adapter, repo_path)
    # Node set of the EXISTING suite (new-test target excluded) -- the oracle for
    # the persisted baseline and for regression-by-removal detection.
    existing_nodes = [
        n for n in collect_nodes(adapter, repo_path)
        if not any(t in n for t in (new_tests or []))
    ]

    # (a) BASELINE: the project's existing test suite (new tests excluded).
    baseline = run_phase("baseline", test_command, repo_path,
                         extra_args=existing_suite_args or None)
    phases.append(baseline)

    regression_delta = None
    if baseline_only:
        # Persist the pre-change baseline for a later post-change comparison.
        save_baseline(baseline_file, {
            "repo": os.path.basename(repo_path.rstrip(os.sep)),
            "test_command": test_command,
            "phases": phases,
        }, existing_nodes)
    else:
        # (b) REGRESSION: re-run the existing suite. If a baseline was captured
        # before the change, compare against it so the "no regression" claim is a
        # real before/after delta, not a blind repeat of the same disk state.
        regression = run_phase("regression", test_command, repo_path,
                               extra_args=existing_suite_args or None)
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
            new_result = run_phase("new", test_command, repo_path, extra_args=list(new_tests))
            phases.append(new_result)

    all_passed = all(p["passed"] for p in phases)
    summary = {
        "repo": os.path.basename(repo_path.rstrip(os.sep)),
        "repo_path": repo_path,
        "adapter": adapter.get("name", "unknown"),
        "adapter_path": adapter_path,
        "test_command": test_command,
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
    if summary["had_no_tests_before"]:
        lines.append("  note: no tests detected before this run "
                     "(legacy / characterization path).")
    if summary.get("baseline_only"):
        lines.append("  note: baseline-only run; captured pre-change baseline to {}".format(
            summary.get("baseline_file")))
    for p in summary["phases"]:
        c = p["counts"]
        status = "PASS" if p["passed"] else "FAIL"
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
    elif not summary.get("baseline_only"):
        lines.append("  baseline delta: none captured (run --baseline-only on the "
                     "pre-change tree first for a true before/after delta).")
    lines.append("  => {}".format("ALL GREEN" if summary["ok"] else "VERIFICATION FAILED"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA verification harness (pure local test orchestration; "
            "no LLM/network calls)."
        )
    )
    parser.add_argument("repo_path", help="Path to the repository to verify.")
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
        "--json",
        action="store_true",
        help="Emit the machine-readable JSON summary (in addition to the human one).",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.repo_path):
        parser.error("repo_path is not a directory: {}".format(args.repo_path))

    try:
        summary = verify(
            args.repo_path,
            adapter_path=args.adapter,
            new_tests=args.new_tests,
            baseline_only=args.baseline_only,
            baseline_file=args.baseline_file,
        )
    except StubAdapterError as exc:
        parser.error(str(exc))

    print(format_human(summary))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
