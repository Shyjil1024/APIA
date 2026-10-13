"""Tests for the APIA off-model scanner engine (.apia/scripts/scan.py).

Coverage (per FEAT-002):
  (a) command-assembly for each scanner (argv shape, python-interpreter rewrite,
      full-string absolute-path tolerance);
  (b) output normalization for EACH tool parser from captured fixture output
      into the canonical finding shape (bandit / pip-audit / ruff / flake8 /
      radon / cppcheck / clang-tidy);
  (c) apply_thresholds gate pass/fail and the failing-reason detail;
  (d) graceful handling of a MISSING tool (tool_unavailable marker, no crash,
      no fabricated finding);
  (e) the structured report shape and JSON-serializability;
  (f) EXECUTED end-to-end: run the real Python scanners against samples/pyapp and
      assert specific findings (bandit rule ids + the pip-audit CVE on the
      fixture dependency) -- proves the pipeline, not just parsing;
  (g) for C++, EXECUTE cppcheck against samples/cppapp (present in this sandbox)
      and assert findings; clang-tidy is additionally executed directly on the
      smelly fixture when reachable, else its normalization is proven from a
      captured fixture (documented in PROOF.md);
  (h) a static no-network/no-LLM guard mirroring
      test_ingest.test_ingest_makes_no_network_or_llm_calls.

scan.py is a pure off-model module (no LLM/network). The executed tests drive
only the adapter's LOCAL scanner tools via subprocess.
"""

import json
import os
import shutil
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import scan  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
CPPAPP = os.path.join(REPO_ROOT, "samples", "cppapp")
PY_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")
CPP_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "cpp.yml")

_HAVE_CPPCHECK = shutil.which("cppcheck") is not None
_HAVE_CLANG_TIDY = shutil.which("clang-tidy") is not None


# --- Captured fixture output (real tool output; see PROOF.md) ----------------

BANDIT_JSON = json.dumps({
    "errors": [],
    "results": [
        {
            "filename": "/repo/calc/insecure_demo.py",
            "issue_confidence": "HIGH",
            "issue_severity": "HIGH",
            "issue_text": "subprocess call with shell=True identified, security issue.",
            "line_number": 24,
            "test_id": "B602",
            "test_name": "subprocess_popen_with_shell_equals_true",
        },
        {
            "filename": "/repo/calc/insecure_demo.py",
            "issue_confidence": "MEDIUM",
            "issue_severity": "LOW",
            "issue_text": "Possible hardcoded password: 's3cr3t'",
            "line_number": 19,
            "test_id": "B105",
            "test_name": "hardcoded_password_string",
        },
    ],
})

PIP_AUDIT_JSON = json.dumps({
    "dependencies": [
        {
            "name": "jinja2",
            "version": "2.10",
            "vulns": [
                {
                    "id": "PYSEC-2019-217",
                    "fix_versions": ["2.10.1"],
                    "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45"],
                    "description": "sandbox escape via str.format_map.",
                },
            ],
        },
        {"name": "pytest", "version": "8.4.2", "vulns": []},
    ],
})

RUFF_JSON = json.dumps([
    {
        "code": "I001",
        "filename": "/repo/calc/__init__.py",
        "location": {"row": 3, "column": 1},
        "message": "Import block is un-sorted or un-formatted",
        "severity": "error",
    },
    {
        "code": "UP032",
        "filename": "/repo/calc/operations.py",
        "location": {"row": 28, "column": 5},
        "message": "Use f-string instead of `format` call",
        "severity": "warning",
    },
])

FLAKE8_TEXT = (
    "/repo/calc/s.py:1:1: F401 'os' imported but unused\n"
    "/repo/calc/s.py:2:2: E225 missing whitespace around operator\n"
)

RADON_JSON = json.dumps({
    "/repo/calc/insecure_demo.py": [
        {"type": "function", "rank": "D", "lineno": 25, "complexity": 22,
         "name": "over_complex", "closures": []},
        {"type": "function", "rank": "A", "lineno": 2, "complexity": 1,
         "name": "run_shell", "closures": []},
    ],
})

CPPCHECK_XML = """Checking src/smelly.cpp ...
<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
    <cppcheck version="2.22.0"/>
    <errors>
        <error id="uninitvar" severity="error" msg="Uninitialized variable: x" cwe="457">
            <location file="/repo/src/smelly.cpp" line="22" column="12"/>
        </error>
        <error id="memleak" severity="error" msg="Memory leak: buf" cwe="401">
            <location file="/repo/src/smelly.cpp" line="29" column="5"/>
        </error>
        <error id="passedByValue" severity="performance" msg="Function parameter 'thing' should be passed by const reference.">
            <location file="/repo/src/smelly.cpp" line="38" column="23"/>
        </error>
        <error id="unusedFunction" severity="style" msg="The function 'leaky' is never used.">
            <location file="/repo/src/smelly.cpp" line="26" column="5"/>
        </error>
        <error id="checkersReport" severity="information" msg="Active checkers: 176/189"/>
    </errors>
</results>
"""

CLANG_TIDY_TEXT = (
    "2 warnings generated.\n"
    "/repo/src/smelly.cpp:22:12: warning: Undefined or garbage value returned "
    "to caller [clang-analyzer-core.uninitialized.UndefReturn]\n"
    "    22 |     return x;\n"
    "/repo/src/smelly.cpp:29:5: error: use of undeclared identifier 'foo' "
    "[clang-diagnostic-error]\n"
)


# --- (b) per-tool normalization ---------------------------------------------

def _assert_canonical(f):
    assert set(f.keys()) == {
        "severity", "category", "file", "line", "rule", "message", "source_tool"
    }
    assert f["severity"] in scan.SEVERITIES
    assert f["category"] in scan.CATEGORIES
    assert f["line"] is None or isinstance(f["line"], int)


def test_normalize_bandit():
    findings = scan.normalize_bandit(BANDIT_JSON, "", "/repo", "security")
    for f in findings:
        _assert_canonical(f)
    rules = {f["rule"]: f for f in findings}
    assert "B602" in rules and rules["B602"]["severity"] == "high"
    assert rules["B602"]["category"] == "security"
    assert rules["B602"]["file"] == "calc/insecure_demo.py"
    assert rules["B602"]["line"] == 24
    assert rules["B602"]["source_tool"] == "bandit"
    assert "B105" in rules and rules["B105"]["severity"] == "low"


def test_normalize_pip_audit():
    findings = scan.normalize_pip_audit(PIP_AUDIT_JSON, "", "/repo", "security")
    assert len(findings) == 1  # pytest has no vulns -> skipped
    f = findings[0]
    _assert_canonical(f)
    assert f["rule"] == "PYSEC-2019-217"
    assert f["severity"] == "high"
    assert f["category"] == "security"
    assert f["source_tool"] == "pip-audit"
    assert "CVE-2019-10906" in f["message"]
    assert "jinja2" in f["file"]


def test_normalize_ruff():
    findings = scan.normalize_ruff(RUFF_JSON, "", "/repo", "quality")
    rules = {f["rule"]: f for f in findings}
    assert rules["I001"]["severity"] == "medium"  # ruff "error"
    assert rules["UP032"]["severity"] == "low"     # ruff "warning"
    assert rules["I001"]["line"] == 3
    assert rules["I001"]["file"] == "calc/__init__.py"
    for f in findings:
        _assert_canonical(f)
        assert f["source_tool"] == "ruff"
        assert f["category"] == "quality"


def test_normalize_flake8():
    findings = scan.normalize_flake8(FLAKE8_TEXT, "", "/repo", "quality")
    assert len(findings) == 2
    rules = {f["rule"]: f for f in findings}
    assert set(rules) == {"F401", "E225"}
    assert rules["F401"]["line"] == 1
    assert rules["E225"]["line"] == 2
    for f in findings:
        _assert_canonical(f)
        assert f["severity"] == "low"
        assert f["source_tool"] == "flake8"


def test_normalize_radon():
    findings = scan.normalize_radon(RADON_JSON, "", "/repo", "perf")
    # Only the rank-D function is surfaced (rank A is below the complexity floor).
    assert len(findings) == 1
    f = findings[0]
    _assert_canonical(f)
    assert f["severity"] == "medium"  # rank D
    assert f["category"] == "perf"
    assert "over_complex" in f["message"]
    assert f["line"] == 25
    assert f["source_tool"] == "radon"


def test_normalize_cppcheck_self_categorizes():
    # category=None -> cppcheck's own severity kind decides the category.
    findings = scan.normalize_cppcheck("", CPPCHECK_XML, "/repo", None)
    by_rule = {f["rule"]: f for f in findings}
    # The information-only checkersReport (no location) is skipped.
    assert "checkersReport" not in by_rule
    assert by_rule["uninitvar"]["severity"] == "high"
    assert by_rule["uninitvar"]["category"] == "security"
    assert by_rule["uninitvar"]["file"] == "src/smelly.cpp"
    assert by_rule["uninitvar"]["line"] == 22
    assert by_rule["memleak"]["category"] == "security"
    assert by_rule["passedByValue"]["category"] == "perf"
    assert by_rule["passedByValue"]["severity"] == "medium"
    assert by_rule["unusedFunction"]["category"] == "quality"
    for f in findings:
        _assert_canonical(f)
        assert f["source_tool"] == "cppcheck"


def test_normalize_cppcheck_empty_output():
    assert scan.normalize_cppcheck("", "", "/repo", None) == []
    assert scan.normalize_cppcheck("no xml here", "noise", "/repo", None) == []


def test_normalize_clang_tidy():
    findings = scan.normalize_clang_tidy(CLANG_TIDY_TEXT, "", "/repo", "quality")
    # The "note" continuation / source snippet lines are ignored.
    by_rule = {f["rule"]: f for f in findings}
    assert "clang-analyzer-core.uninitialized.UndefReturn" in by_rule
    warn = by_rule["clang-analyzer-core.uninitialized.UndefReturn"]
    assert warn["severity"] == "medium"
    assert warn["line"] == 22
    assert warn["file"] == "src/smelly.cpp"
    assert by_rule["clang-diagnostic-error"]["severity"] == "high"  # error
    for f in findings:
        _assert_canonical(f)
        assert f["source_tool"] == "clang-tidy"


def test_normalizers_handle_empty_cleanly():
    assert scan.normalize_bandit("", "", "/r", "security") == []
    assert scan.normalize_bandit("{}", "", "/r", "security") == []
    assert scan.normalize_pip_audit("{}", "", "/r", "security") == []
    assert scan.normalize_ruff("[]", "", "/r", "quality") == []
    assert scan.normalize_flake8("", "", "/r", "quality") == []
    assert scan.normalize_radon("{}", "", "/r", "perf") == []


# --- (a) command assembly ----------------------------------------------------

def test_split_command_rewrites_python_interpreter():
    argv = scan._split_command("python -m bandit -r . -f json")
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "bandit", "-r", ".", "-f", "json"]


def test_split_command_leaves_native_tool_unchanged():
    argv = scan._split_command("cppcheck --enable=all --xml src")
    assert argv[0] == "cppcheck"


def test_split_command_tolerates_absolute_path():
    argv = scan._split_command("/opt/llvm/bin/clang-tidy -p build src/a.cpp")
    assert argv[0] == "/opt/llvm/bin/clang-tidy"
    assert argv[-1] == "src/a.cpp"


def test_scanner_exe_reports_logical_tool():
    assert scan._scanner_exe({"command": "python -m bandit -r ."}) == "bandit"
    assert scan._scanner_exe({"command": "cppcheck --enable=all src"}) == "cppcheck"


# --- (c) threshold gating ----------------------------------------------------

def _mk(sev, cat):
    return scan.make_finding(sev, cat, "f.py", 1, "R1", "msg", "tool")


def test_apply_thresholds_passes_when_below_severity():
    findings = [_mk("low", "quality"), _mk("medium", "quality")]
    gate = scan.apply_thresholds(findings, {"fail_on_severity": "high"})
    assert gate["passed"] is True
    assert gate["failing_severities"] == []
    assert gate["reasons"] == []


def test_apply_thresholds_fails_on_severity_with_reason():
    findings = [_mk("critical", "quality"), _mk("high", "security"), _mk("low", "quality")]
    gate = scan.apply_thresholds(findings, {"fail_on_severity": "high"})
    assert gate["passed"] is False
    assert "critical" in gate["failing_severities"]
    assert "high" in gate["failing_severities"]
    assert gate["reasons"]
    assert "high" in gate["reasons"][0]


def test_apply_thresholds_fails_on_max_findings_category():
    findings = [_mk("low", "security")]
    gate = scan.apply_thresholds(findings, {"max_findings": {"security": 0}})
    assert gate["passed"] is False
    assert gate["failing_counts"]["security"] == {"limit": 0, "actual": 1}
    assert any("security" in r for r in gate["reasons"])


def test_apply_thresholds_total_cap():
    findings = [_mk("low", "quality"), _mk("low", "quality"), _mk("low", "quality")]
    gate = scan.apply_thresholds(findings, {"max_findings": {"total": 2}})
    assert gate["passed"] is False
    assert gate["failing_counts"]["total"] == {"limit": 2, "actual": 3}


def test_apply_thresholds_empty_thresholds_passes():
    gate = scan.apply_thresholds([_mk("critical", "security")], {})
    assert gate["passed"] is True
    gate_none = scan.apply_thresholds([_mk("critical", "security")], None)
    assert gate_none["passed"] is True


# --- (d) missing-tool degradation --------------------------------------------

def test_run_scanner_missing_tool_degrades_gracefully():
    scanner_def = {
        "tool": "definitely-not-a-real-tool",
        "command": "definitely-not-a-real-tool --scan .",
        "format": "flake8",
    }
    findings, meta = scan.run_scanner("/tmp", "quality", scanner_def)
    assert findings == []  # no fabricated finding
    assert meta["tool_unavailable"] is True
    assert "error" in meta


def test_run_scanner_unknown_format_degrades():
    scanner_def = {"tool": "x", "command": "echo hi", "format": "no_such_format"}
    findings, meta = scan.run_scanner("/tmp", "quality", scanner_def)
    assert findings == []
    assert meta["tool_unavailable"] is True


def test_scan_with_missing_tool_still_returns_valid_report(tmp_path):
    adapter = {
        "name": "faux",
        "scanners": {
            "quality": [{
                "tool": "nope-tool",
                "command": "nope-tool .",
                "format": "flake8",
            }],
        },
    }
    report = scan.scan(str(tmp_path), adapter, {})
    assert report["findings"] == []
    assert "nope-tool" in report["unavailable_tools"]
    assert report["gate"]["passed"] is True  # nothing to fail on
    # Report is JSON-serializable.
    json.dumps(report)


# --- stub adapter refusal ----------------------------------------------------

def test_scan_refuses_adapter_without_scanners(tmp_path):
    with pytest.raises(scan.StubAdapterError):
        scan.scan(str(tmp_path), {"name": "stub"}, {})


# --- (e) report shape + JSON-serializability ---------------------------------

def test_report_shape_and_json_serializable():
    adapter = scan.load_adapter(PY_ADAPTER)
    report = scan.scan(PYAPP, adapter, {"category": "quality"})
    for key in ("repo", "repo_path", "stack", "findings", "counts_by_severity",
                "counts_by_category", "threshold", "gate", "tools", "scanners",
                "unavailable_tools"):
        assert key in report, "missing report key: {}".format(key)
    assert report["stack"] == "python"
    assert set(report["counts_by_severity"]) == set(scan.SEVERITIES)
    # gate shape.
    for key in ("passed", "failing_severities", "failing_counts", "reasons"):
        assert key in report["gate"]
    # Fully JSON-serializable.
    json.dumps(report)


# --- (f) EXECUTED Python end-to-end ------------------------------------------

def test_executed_python_scanners_against_pyapp():
    """REAL run of the Python scanners against samples/pyapp.

    Proves the whole pipeline (command assembly -> subprocess -> normalization
    -> aggregation -> gate), not just parsing. Asserts the intentional fixture
    findings surface: a bandit smell on insecure_demo.py AND the pip-audit CVE
    on the fixture dependency jinja2==2.10.
    """
    adapter = scan.load_adapter(PY_ADAPTER)
    report = scan.scan(PYAPP, adapter, {})

    # bandit must flag the intentional shell=True smell (B602) in the fixture.
    bandit = [f for f in report["findings"] if f["source_tool"] == "bandit"]
    assert any(f["rule"] == "B602" for f in bandit), \
        "expected bandit B602 (shell=True) on the fixture"
    assert any("insecure_demo.py" in (f["file"] or "") for f in bandit)

    # pip-audit must flag the known-vulnerable fixture dependency.
    audit = [f for f in report["findings"] if f["source_tool"] == "pip-audit"]
    assert audit, "expected at least one pip-audit finding on jinja2==2.10"
    assert all(f["category"] == "security" and f["severity"] == "high" for f in audit)
    assert any("jinja2" in (f["file"] or "") for f in audit)
    assert any(f["rule"] and ("PYSEC" in f["rule"] or "CVE" in f["rule"]) for f in audit) \
        or any("CVE" in (f["message"] or "") for f in audit)

    # Every finding is the canonical shape and the report is JSON-serializable.
    for f in report["findings"]:
        _assert_canonical(f)
    json.dumps(report)

    # Default thresholds (security max 0 + fail_on high) => gate fails here.
    assert report["gate"]["passed"] is False
    assert report["gate"]["reasons"]


def test_executed_python_scanner_category_filter():
    """Category filter runs only the matching scanners."""
    adapter = scan.load_adapter(PY_ADAPTER)
    report = scan.scan(PYAPP, adapter, {"category": "perf"})
    # perf category => only radon ran; no bandit/pip-audit/ruff findings.
    tools = {f["source_tool"] for f in report["findings"]}
    assert tools <= {"radon"}


# --- (g) EXECUTED C++ (cppcheck present in this sandbox) ----------------------

@pytest.mark.skipif(not _HAVE_CPPCHECK, reason="cppcheck not on PATH")
def test_executed_cppcheck_against_cppapp():
    """REAL cppcheck run against samples/cppapp's smelly fixture.

    cppcheck IS present in this sandbox (2.22.0), so this executes the real
    scanner and asserts the intentional uninitvar + memleak findings surface and
    self-categorize (security). See PROOF.md for the executed-vs-fixture split.
    """
    adapter = scan.load_adapter(CPP_ADAPTER)
    report = scan.scan(CPPAPP, adapter, {})
    cpp = [f for f in report["findings"] if f["source_tool"] == "cppcheck"]
    rules = {f["rule"] for f in cpp}
    assert "uninitvar" in rules, "expected cppcheck uninitvar on smelly.cpp"
    assert "memleak" in rules, "expected cppcheck memleak on smelly.cpp"
    for f in cpp:
        if f["rule"] in ("uninitvar", "memleak"):
            assert f["category"] == "security"
            assert f["severity"] == "high"
            assert "smelly.cpp" in (f["file"] or "")
    json.dumps(report)
    # Default cpp gate (fail_on high) fails on the uninitvar/memleak.
    assert report["gate"]["passed"] is False


@pytest.mark.skipif(not _HAVE_CLANG_TIDY, reason="clang-tidy not on PATH")
def test_executed_clang_tidy_against_smelly(tmp_path):
    """REAL clang-tidy run directly on the smelly fixture source.

    The adapter's declared clang-tidy command uses a compile database (-p build)
    that need not contain smelly.cpp; here we drive clang-tidy directly on the
    fixture with explicit checks + flags so the EXECUTED path is proven. Parsing
    from a fixture is covered by test_normalize_clang_tidy.
    """
    smelly = os.path.join(CPPAPP, "src", "smelly.cpp")
    scanner_def = {
        "tool": "clang-tidy",
        "command": "clang-tidy --checks=clang-analyzer-*,bugprone-* {} -- -std=c++14".format(smelly),
        "format": "clang_tidy",
    }
    findings, meta = scan.run_scanner(CPPAPP, "quality", scanner_def)
    assert meta["tool_unavailable"] is False
    # clang-tidy's static analyzer should flag the uninitialized return/value.
    assert findings, "expected at least one clang-tidy finding on smelly.cpp"
    for f in findings:
        _assert_canonical(f)
        assert f["source_tool"] == "clang-tidy"


# --- stack auto-detection (no --adapter given) -------------------------------

def test_detect_stack_and_adapter_picks_cpp_for_cppapp():
    """scan auto-detects the C++ stack for samples/cppapp and resolves cpp.yml.

    Mirrors the no---adapter path: ingest.detect_stacks drives the choice and the
    adapter is resolved from config.yml stacks[].adapter, so a C++ repo loads the
    cpp adapter (not the python default).
    """
    stack, adapter_path, detected = scan.detect_stack_and_adapter(CPPAPP)
    assert stack == "cpp", "expected cpp to be the detected primary stack"
    assert adapter_path is not None and os.path.basename(adapter_path) == "cpp.yml"
    assert os.path.isfile(adapter_path)
    adapter = scan.load_adapter(adapter_path)
    assert adapter.get("name") == "cpp"
    # The cpp adapter selects the cpp scanners (cppcheck / clang-tidy), not the
    # Python ones.
    tools = {sd.get("tool") for _, sd in scan._iter_scanner_defs(adapter, "all")}
    assert "cppcheck" in tools
    assert "bandit" not in tools


def test_detect_stack_and_adapter_picks_python_for_pyapp():
    """samples/pyapp auto-detects the python stack and resolves python.yml."""
    stack, adapter_path, detected = scan.detect_stack_and_adapter(PYAPP)
    assert stack == "python"
    assert adapter_path is not None and os.path.basename(adapter_path) == "python.yml"


# --- generated/vendored exclusion filter -------------------------------------

def test_exclusion_filter_drops_build_and_vendored_paths():
    """Pure-unit test of the exclusion filter with SYNTHETIC paths.

    Runs without any scanner tool: it proves that findings whose file path lives
    under an excluded (generated/vendored) directory -- a CMake build/ tree, its
    _deps vendoring dir, node_modules, .venv, etc. -- are dropped, while the
    project's OWN source findings pass through.
    """
    findings = [
        _mk("high", "security"),  # file.py at repo root -> kept
        scan.make_finding("high", "security",
                          "build/_deps/googletest-src/x.py", 1, "B307", "m", "bandit"),
        scan.make_finding("low", "quality",
                          "../../build/_deps/googletest-src/y.py", 2, "E111", "m", "flake8"),
        scan.make_finding("high", "security", "src/smelly.cpp", 22, "uninitvar", "m", "cppcheck"),
        scan.make_finding("low", "quality", "node_modules/pkg/a.js", 3, "X", "m", "t"),
        scan.make_finding("low", "quality", ".venv/lib/dep.py", 4, "Y", "m", "t"),
        scan.make_finding("low", "quality", "calc/real.py", 5, "Z", "m", "t"),
    ]
    kept = scan.filter_excluded_findings(findings)
    kept_files = {f["file"] for f in kept}
    assert "src/smelly.cpp" in kept_files
    assert "calc/real.py" in kept_files
    assert "f.py" in kept_files  # the _mk default
    for bad in ("build/_deps/googletest-src/x.py",
                "../../build/_deps/googletest-src/y.py",
                "node_modules/pkg/a.js", ".venv/lib/dep.py"):
        assert bad not in kept_files, "{} should have been excluded".format(bad)
    # And the low-level predicate matches on any path component.
    assert scan._is_excluded_path("a/b/build/c.py") is True
    assert scan._is_excluded_path("src/_deps/vendor.cpp") is True
    assert scan._is_excluded_path("src/app/main.cpp") is False
    assert scan._is_excluded_path(None) is False


@pytest.mark.skipif(not _HAVE_CPPCHECK, reason="cppcheck not on PATH")
def test_executed_scan_excludes_build_tree_for_cppapp():
    """EXECUTED: scan samples/cppapp and assert build/_deps findings are gone.

    The smelly.cpp findings (uninitvar/memleak) must appear while NO finding's
    file path points into the gitignored CMake build/ tree (incl. build/_deps,
    GoogleTest's own vendored Python files that previously produced ~941 spurious
    findings).
    """
    stack, adapter_path, _ = scan.detect_stack_and_adapter(CPPAPP)
    assert stack == "cpp"
    adapter = scan.load_adapter(adapter_path)
    report = scan.scan(CPPAPP, adapter, {})
    files = [f.get("file") or "" for f in report["findings"]]
    for path in files:
        norm = path.replace("\\", "/")
        assert "build/_deps" not in norm, "build/_deps finding leaked: {}".format(path)
        assert "/build/" not in norm, "build/ finding leaked: {}".format(path)
        assert not norm.split("/").count("build"), "build/ component leaked: {}".format(path)
    # The project's own smells still surface.
    rules = {f["rule"] for f in report["findings"] if f["source_tool"] == "cppcheck"}
    assert "uninitvar" in rules
    assert "memleak" in rules


# --- doctor reports module-invoked scanners as present -----------------------

def test_doctor_reports_module_invoked_scanner_present():
    """An installed `python -m` scanner is reported PRESENT by scan.py's doctor.

    bandit is invoked as `python -m bandit` (no console script on PATH in this
    venv), so a shutil.which probe would under-report it. The module probe must
    report it present. Skip only if bandit is genuinely not importable here.
    """
    import importlib.util
    if importlib.util.find_spec("bandit") is None:
        pytest.skip("bandit module not importable in this environment")

    import ingest  # noqa: E402
    report = ingest.doctor(repo_path=None)
    by_name = {e["tool"]: e for e in report["tools"]}
    assert by_name["bandit"]["present"] is True, \
        "module-invoked bandit must be reported present"
    # It must NOT be counted as a hard failure (scanner category is non-gating).
    assert "bandit" not in report["missing_required"]

    # The adapter scanner table scan.py surfaces must also show it present.
    adapter = scan.load_adapter(PY_ADAPTER)
    tools = scan.scanner_tools_report(adapter)
    assert "bandit" in tools["present"]


def test_doctor_scanner_absence_is_non_gating():
    """A missing scanner stays a warning, never a hard failure (non-gating)."""
    import ingest  # noqa: E402
    tools = [
        ("not-a-real-scanner", ["-m", "not_a_real_scanner", "--version"],
         "Deliberately absent scanner.", "scanner", "definitely_absent_module_xyz"),
    ]
    report = ingest.doctor(tools=tools, required_categories=set())
    by_name = {e["tool"]: e for e in report["tools"]}
    assert by_name["not-a-real-scanner"]["present"] is False
    assert report["missing_required"] == []  # scanner category never gates


# --- (h) static no-network / no-LLM guard ------------------------------------

def test_scan_makes_no_network_or_llm_calls():
    """Static guard: scan.py must not import network/LLM SDKs or model endpoints."""
    src_path = os.path.join(SCRIPTS_DIR, "scan.py")
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
        assert token not in src, "scan.py must not reference '{}'".format(token)
