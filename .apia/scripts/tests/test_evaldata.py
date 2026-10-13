"""Tests for APIA Layer-1 grounded eval-data generation (.apia/scripts/evaldata.py).

Two layers of coverage mirror the rest of the suite:

  1. UNIT tests (always run, no toolchain): golden comparison in all three modes
     (bytes match/mismatch, json order-insensitive, text newline-normalized);
     each built-in invariant check passing AND failing; characterization harvest
     of a trivial LOCAL command (run via sys.executable) asserting the frozen
     output + a stable hash; the artifact differential unchanged-vs-changed; the
     assemble_eval_data entry; and a static no-network/no-LLM guard (same spirit
     as test_ingest's guard) asserting the only subprocess is the adapter's local
     command (verify._run).

  2. REAL both-stacks tests: golden + invariant + characterization run for real
     against a Python-produced artifact (from samples/pyapp) and a C++-produced
     artifact (from samples/cppapp). The C++ parts are guarded by
     shutil.which('cmake')/('ctest') so the suite stays portable.

evaldata.py is a pure off-model module (no LLM/network). The only subprocess it
spawns is the adapter's own LOCAL characterization.capture_command, run through
the shared verify._run/_split_command path.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import evaldata  # noqa: E402
import verify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
PYAPP = os.path.join(REPO_ROOT, "samples", "pyapp")
CPPAPP = os.path.join(REPO_ROOT, "samples", "cppapp")
PY_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "python.yml")
CPP_ADAPTER = os.path.join(REPO_ROOT, ".apia", "adapters", "cpp.yml")

_HAVE_CMAKE = shutil.which("cmake") is not None and shutil.which("ctest") is not None
_requires_toolchain = pytest.mark.skipif(
    not _HAVE_CMAKE, reason="cmake/ctest not on PATH; C++ real-artifact tests skipped"
)


def _write(path, content, binary=False):
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# (b) GOLDEN compare: bytes / text / json
# ---------------------------------------------------------------------------


def test_golden_bytes_match(tmp_path):
    a = str(tmp_path / "a.bin")
    b = str(tmp_path / "b.bin")
    _write(a, b"\x00\x01hello", binary=True)
    _write(b, b"\x00\x01hello", binary=True)
    result = evaldata.compare_artifact(a, b, "bytes")
    assert result["match"] is True
    assert result["mode"] == "bytes"
    assert result["diff_summary"] == ""
    assert result["produced_hash"] == result["golden_hash"]


def test_golden_bytes_mismatch(tmp_path):
    a = str(tmp_path / "a.bin")
    b = str(tmp_path / "b.bin")
    _write(a, b"\x00\x01hello", binary=True)
    _write(b, b"\x00\x01world", binary=True)
    result = evaldata.compare_artifact(a, b, "bytes")
    assert result["match"] is False
    assert result["diff_summary"]  # non-empty reason
    assert result["produced_hash"] != result["golden_hash"]


def test_golden_json_order_insensitive_match(tmp_path):
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    # Same structure, different key order and whitespace -> must match in json mode.
    _write(a, '{"name": "widget", "count": 3, "tags": ["x", "y"]}')
    _write(b, '{\n  "count": 3,\n  "tags": ["x", "y"],\n  "name": "widget"\n}')
    result = evaldata.compare_artifact(a, b, "json")
    assert result["match"] is True
    assert result["produced_hash"] == result["golden_hash"]
    # But bytes mode (the default) rightly sees them as different.
    assert evaldata.compare_artifact(a, b, "bytes")["match"] is False


def test_golden_json_mismatch_reports_diff(tmp_path):
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    _write(a, '{"count": 3}')
    _write(b, '{"count": 4}')
    result = evaldata.compare_artifact(a, b, "json")
    assert result["match"] is False
    assert "json" in result["diff_summary"]


def test_golden_json_array_order_preserved(tmp_path):
    # Array order IS significant: a reordered list must NOT match.
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    _write(a, '[1, 2, 3]')
    _write(b, '[3, 2, 1]')
    assert evaldata.compare_artifact(a, b, "json")["match"] is False


def test_golden_text_newline_normalized_match(tmp_path):
    a = str(tmp_path / "a.txt")
    b = str(tmp_path / "b.txt")
    # CRLF vs LF plus a trailing newline difference -> equal in text mode.
    _write(a, "line one\r\nline two\r\n")
    _write(b, "line one\nline two")
    result = evaldata.compare_artifact(a, b, "text")
    assert result["match"] is True
    assert result["produced_hash"] == result["golden_hash"]
    # Byte-identical check would (correctly) fail on the raw difference.
    assert evaldata.compare_artifact(a, b, "bytes")["match"] is False


def test_golden_text_mismatch_reports_line(tmp_path):
    a = str(tmp_path / "a.txt")
    b = str(tmp_path / "b.txt")
    _write(a, "line one\nline two\n")
    _write(b, "line one\nline TWO\n")
    result = evaldata.compare_artifact(a, b, "text")
    assert result["match"] is False
    assert "line 2" in result["diff_summary"]


def test_golden_missing_file_is_not_a_match(tmp_path):
    a = str(tmp_path / "exists.json")
    _write(a, "{}")
    result = evaldata.compare_artifact(a, str(tmp_path / "nope.json"), "json")
    assert result["match"] is False
    assert "missing" in result["diff_summary"]


def test_mine_golden_resolves_glob_against_golden_dir(tmp_path):
    repo = tmp_path / "repo"
    (repo / "out").mkdir(parents=True)
    (repo / "tests" / "golden").mkdir(parents=True)
    _write(str(repo / "out" / "report.json"), '{"a": 1, "b": 2}')
    _write(str(repo / "tests" / "golden" / "report.json"), '{"b": 2, "a": 1}')
    adapter = {
        "name": "python",
        "golden": {
            "artifact_glob": ["out/*.json"],
            "golden_dir": "tests/golden",
            "compare": "json",
        },
    }
    results = evaldata.mine_golden(str(repo), adapter)
    assert len(results) == 1
    assert results[0]["match"] is True
    assert results[0]["artifact"] == os.path.join("out", "report.json")


def test_mine_golden_empty_when_no_block():
    assert evaldata.mine_golden("/tmp", {"name": "stub"}) == []


def test_resolve_compare_mode_string_is_global_and_backward_compatible():
    # A plain string applies to every glob (the original shape); the glob arg is
    # ignored. Absent compare falls back to the language-neutral bytes.
    assert evaldata._resolve_compare_mode("json", "out/**/*.json") == "json"
    assert evaldata._resolve_compare_mode("json", "out/**/*.csv") == "json"
    assert evaldata._resolve_compare_mode(None, "out/**/*.csv") == "bytes"


def test_resolve_compare_mode_mapping_is_per_glob_with_default():
    compare = {"out/**/*.json": "json", "out/**/*.csv": "text", "default": "bytes"}
    assert evaldata._resolve_compare_mode(compare, "out/**/*.json") == "json"
    assert evaldata._resolve_compare_mode(compare, "out/**/*.csv") == "text"
    # An unlisted glob falls back to the mapping's default.
    assert evaldata._resolve_compare_mode(compare, "out/**/*.png") == "bytes"
    # No default key -> language-neutral bytes.
    assert evaldata._resolve_compare_mode({"a": "json"}, "b") == "bytes"


def test_mine_golden_per_glob_compare_grades_each_kind_under_its_mode(tmp_path):
    # A single golden block mixing JSON + CSV under a PER-GLOB compare mapping:
    # the JSON report is graded json and the CSV table text; both match. Under a
    # single global compare: json this CSV would fail as "invalid JSON".
    repo = tmp_path / "repo"
    (repo / "out").mkdir(parents=True)
    (repo / "tests" / "golden").mkdir(parents=True)
    _write(str(repo / "out" / "report.json"), '{"a": 1, "b": 2}')
    _write(str(repo / "tests" / "golden" / "report.json"), '{"b": 2, "a": 1}')
    _write(str(repo / "out" / "table.csv"), "a,b\n1,2\n")
    # Golden CSV differs only by a trailing newline -> text mode still matches.
    _write(str(repo / "tests" / "golden" / "table.csv"), "a,b\n1,2")
    adapter = {
        "name": "python",
        "golden": {
            "artifact_glob": ["out/**/*.json", "out/**/*.csv"],
            "golden_dir": "tests/golden",
            "compare": {
                "out/**/*.json": "json",
                "out/**/*.csv": "text",
                "default": "bytes",
            },
        },
    }
    results = evaldata.mine_golden(str(repo), adapter)
    by_artifact = {r["artifact"]: r for r in results}
    assert set(by_artifact) == {
        os.path.join("out", "report.json"),
        os.path.join("out", "table.csv"),
    }
    assert all(r["match"] is True for r in results), results
    assert by_artifact[os.path.join("out", "report.json")]["mode"] == "json"
    assert by_artifact[os.path.join("out", "table.csv")]["mode"] == "text"


# ---------------------------------------------------------------------------
# (c) INVARIANTS: each built-in check passing AND failing
# ---------------------------------------------------------------------------


def _run_single_invariant(tmp_path, check_id, produced_content, context_extra=None):
    produced = str(tmp_path / "produced.json")
    _write(produced, produced_content)
    adapter = {"name": "python", "invariants": {"checks": [check_id]}}
    ctx = {"produced_path": produced}
    if context_extra:
        ctx.update(context_extra)
    results = evaldata.run_invariants(str(tmp_path), adapter, ctx)
    assert len(results) == 1
    return results[0]


def test_invariant_row_count_matches_input_holds(tmp_path):
    r = _run_single_invariant(
        tmp_path, "row_count_matches_input",
        '[{"id": 1}, {"id": 2}, {"id": 3}]',
        {"input_row_count": 3},
    )
    assert r["check_id"] == "row_count_matches_input"
    assert r["held"] is True


def test_invariant_row_count_matches_input_fails(tmp_path):
    r = _run_single_invariant(
        tmp_path, "row_count_matches_input",
        '[{"id": 1}, {"id": 2}]',
        {"input_row_count": 3},
    )
    assert r["held"] is False
    assert r["detail"]


def test_invariant_no_nans_holds(tmp_path):
    r = _run_single_invariant(
        tmp_path, "no_nans",
        '[{"a": 1, "b": "ok"}, {"a": 2, "b": "fine"}]',
    )
    assert r["held"] is True


def test_invariant_no_nans_fails_on_empty(tmp_path):
    r = _run_single_invariant(
        tmp_path, "no_nans",
        '[{"a": 1, "b": ""}, {"a": 2, "b": "fine"}]',
    )
    assert r["held"] is False
    assert "row 0" in r["detail"]


def test_invariant_no_nans_fails_on_null(tmp_path):
    r = _run_single_invariant(
        tmp_path, "no_nans",
        '[{"a": null}]',
    )
    assert r["held"] is False


def test_invariant_roundtrip_identity_holds(tmp_path):
    r = _run_single_invariant(
        tmp_path, "roundtrip_identity",
        '{"name": "x", "nested": {"k": [1, 2, 3]}}',
    )
    assert r["held"] is True


def test_invariant_roundtrip_identity_fails_on_bad_json(tmp_path):
    r = _run_single_invariant(
        tmp_path, "roundtrip_identity",
        'not valid json at all',
    )
    assert r["held"] is False
    assert r["detail"]


def test_invariant_unknown_check_is_ignored(tmp_path):
    produced = str(tmp_path / "produced.json")
    _write(produced, "[]")
    adapter = {"name": "python", "invariants": {"checks": ["totally_unknown"]}}
    results = evaldata.run_invariants(str(tmp_path), adapter, {"produced_path": produced})
    assert results == []


# ---------------------------------------------------------------------------
# (a) CHARACTERIZATION HARVEST (trivial local command via sys.executable)
# ---------------------------------------------------------------------------


def test_characterization_harvest_freezes_output_and_hash(tmp_path):
    # A trivial deterministic local command: print a fixed string via the SAME
    # interpreter running the tests (verify._split_command rewrites "python").
    adapter = {
        "name": "python",
        "characterization": {
            "capture_command": 'python -c "print(\'hello-reality\')"',
        },
    }
    first = evaldata.harvest_characterization(str(tmp_path), adapter)
    assert first is not None
    assert first["returncode"] == 0
    assert "hello-reality" in first["captured_output"]
    assert first["command"].startswith(sys.executable)
    # Deterministic: same input -> same frozen hash across runs.
    second = evaldata.harvest_characterization(str(tmp_path), adapter)
    assert second["output_hash"] == first["output_hash"]
    # The hash is the sha256 of the captured stdout.
    import hashlib
    expected = hashlib.sha256(first["captured_output"].encode("utf-8")).hexdigest()
    assert first["output_hash"] == expected


def test_characterization_harvest_none_without_command(tmp_path):
    # The real python.yml adapter ships capture_command: null -> no signal.
    adapter = verify.load_adapter(PY_ADAPTER)
    assert evaldata.harvest_characterization(str(tmp_path), adapter) is None


# ---------------------------------------------------------------------------
# (d) DIFFERENTIAL artifacts: unchanged vs changed
# ---------------------------------------------------------------------------


def test_differential_unchanged_identical(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _write(str(old / "a.csv"), "x,y\n1,2\n")
    _write(str(new / "a.csv"), "x,y\n1,2\n")
    result = evaldata.differential_artifacts(str(old), str(new), ["*.csv"], "bytes")
    assert result["unchanged"] == ["a.csv"]
    assert result["changed"] == []


def test_differential_flags_changed(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _write(str(old / "a.csv"), "x,y\n1,2\n")
    _write(str(new / "a.csv"), "x,y\n1,2\n")
    _write(str(old / "b.csv"), "x,y\n9,9\n")
    _write(str(new / "b.csv"), "x,y\n9,8\n")  # intended change
    result = evaldata.differential_artifacts(str(old), str(new), ["*.csv"], "bytes")
    assert result["unchanged"] == ["a.csv"]
    assert [c["artifact"] for c in result["changed"]] == ["b.csv"]
    assert result["changed"][0]["match"] is False


def test_differential_flags_added_artifact(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _write(str(new / "fresh.json"), "{}")
    result = evaldata.differential_artifacts(str(old), str(new), ["*.json"], "bytes")
    assert [c["artifact"] for c in result["changed"]] == ["fresh.json"]


# ---------------------------------------------------------------------------
# assemble_eval_data: single entry gathering enabled sources
# ---------------------------------------------------------------------------


def test_assemble_eval_data_gathers_enabled_sources(tmp_path):
    repo = tmp_path / "repo"
    (repo / "out").mkdir(parents=True)
    (repo / "tests" / "golden").mkdir(parents=True)
    produced = repo / "out" / "report.json"
    _write(str(produced), '[{"id": 1}, {"id": 2}]')
    _write(str(repo / "tests" / "golden" / "report.json"), '[{"id": 1}, {"id": 2}]')
    adapter = {
        "name": "python",
        "golden": {
            "artifact_glob": ["out/*.json"],
            "golden_dir": "tests/golden",
            "compare": "json",
        },
        "characterization": {"capture_command": None},
        "invariants": {"checks": ["row_count_matches_input", "no_nans"]},
    }
    data = evaldata.assemble_eval_data(
        str(repo), adapter,
        options={
            "invariant_context": {
                "produced_path": str(produced),
                "input_row_count": 2,
            },
        },
    )
    assert data["adapter"] == "python"
    assert data["trust_order"] == ["characterization", "golden", "invariant", "differential"]
    assert data["characterization"] is None  # capture_command is null
    assert len(data["golden"]) == 1 and data["golden"][0]["match"] is True
    held = {c["check_id"]: c["held"] for c in data["invariants"]}
    assert held == {"row_count_matches_input": True, "no_nans": True}
    assert data["differential"] is None  # not requested


def test_assemble_eval_data_runs_differential_when_requested(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _write(str(old / "a.json"), "{}")
    _write(str(new / "a.json"), "{}")
    data = evaldata.assemble_eval_data(
        str(tmp_path), {"name": "python"},
        options={
            "characterization": False,
            "golden": False,
            "invariants": False,
            "differential": {
                "old_dir": str(old), "new_dir": str(new),
                "globs": ["*.json"], "mode": "bytes",
            },
        },
    )
    assert data["differential"]["unchanged"] == ["a.json"]


# ---------------------------------------------------------------------------
# NO-NETWORK / NO-LLM static guard (same spirit as test_ingest's guard)
# ---------------------------------------------------------------------------


def test_evaldata_makes_no_network_or_llm_calls():
    """Static guard: evaldata.py imports no network/LLM SDK and reaches no
    remote/model endpoint; the only subprocess is the adapter's local command."""
    src_path = os.path.join(SCRIPTS_DIR, "evaldata.py")
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
        "api.anthropic.com",
        "chat/completions",
    ]
    for token in forbidden:
        assert token not in src, "evaldata.py must not reference '{}'".format(token)


def test_evaldata_only_subprocess_is_via_shared_local_run():
    """evaldata must not spawn its own subprocess: the ONLY process launch is the
    adapter's local command run through the shared verify._run primitive."""
    src_path = os.path.join(SCRIPTS_DIR, "evaldata.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    # No direct subprocess spawning in evaldata: it reuses verify._run.
    for token in ("subprocess.run", "subprocess.Popen", "os.system", "os.popen"):
        assert token not in src, "evaldata.py must spawn only via verify._run, not '{}'".format(token)
    assert "verify._run" in src


def test_characterization_harvest_spawns_only_the_adapter_command(monkeypatch, tmp_path):
    """The single subprocess is the adapter's capture_command and nothing else."""
    calls = []

    def _record(argv, cwd):
        calls.append((argv, cwd))
        return 0, "frozen\n", ""

    monkeypatch.setattr(verify, "_run", _record)
    adapter = {
        "name": "python",
        "characterization": {"capture_command": 'python -c "print(1)"'},
    }
    result = evaldata.harvest_characterization(str(tmp_path), adapter)
    assert result["captured_output"] == "frozen\n"
    assert len(calls) == 1  # exactly one local command spawned
    argv, cwd = calls[0]
    assert argv[0] == sys.executable  # the adapter's own python command
    assert cwd == str(tmp_path)


# ---------------------------------------------------------------------------
# REAL both-stacks: Python-produced artifact (always) + C++-produced (guarded)
# ---------------------------------------------------------------------------


def test_real_python_produced_artifact_golden_invariant_characterization(tmp_path):
    """Produce a REAL artifact by running samples/pyapp code, then run golden +
    invariant + characterization against it for real (no toolchain needed)."""
    repo = tmp_path / "repo"
    (repo / "out").mkdir(parents=True)
    (repo / "tests" / "golden").mkdir(parents=True)

    # Run the REAL pyapp operations to produce a report artifact deterministically.
    script = (
        "import sys, json\n"
        "sys.path.insert(0, {pyapp!r})\n"
        "from calc.operations import add, multiply\n"
        "rows = [\n"
        "    {{'a': 2, 'b': 3, 'sum': add(2, 3), 'prod': multiply(2, 3)}},\n"
        "    {{'a': 4, 'b': 5, 'sum': add(4, 5), 'prod': multiply(4, 5)}},\n"
        "]\n"
        "print(json.dumps(rows))\n"
    ).format(pyapp=PYAPP)
    produced = str(repo / "out" / "report.json")
    out = subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    assert out.returncode == 0, out.stderr
    _write(produced, out.stdout.strip())
    # Commit the same bytes as the golden truth (reordered keys to prove json mode).
    rows = json.loads(out.stdout)
    reordered = [dict(reversed(list(r.items()))) for r in rows]
    _write(str(repo / "tests" / "golden" / "report.json"), json.dumps(reordered))

    adapter = {
        "name": "python",
        "golden": {
            "artifact_glob": ["out/*.json"],
            "golden_dir": "tests/golden",
            "compare": "json",
        },
        "characterization": {
            "capture_command": 'python -c "print(\'pyapp-reality\')"',
        },
        "invariants": {"checks": ["row_count_matches_input", "no_nans", "roundtrip_identity"]},
    }

    # GOLDEN: json order-insensitive match against the real produced artifact.
    golden = evaldata.mine_golden(str(repo), adapter)
    assert len(golden) == 1 and golden[0]["match"] is True

    # INVARIANTS: all three hold on the real artifact (2 rows, no nans, roundtrips).
    inv = evaldata.run_invariants(
        str(repo), adapter,
        {"produced_path": produced, "input_row_count": 2},
    )
    assert all(c["held"] for c in inv), inv

    # CHARACTERIZATION: a real local command's stdout is frozen with a stable hash.
    char = evaldata.harvest_characterization(str(repo), adapter)
    assert char is not None and "pyapp-reality" in char["captured_output"]


@_requires_toolchain
def test_real_cpp_produced_artifact_golden_invariant_characterization(tmp_path):
    """Build samples/cppapp for real, run a REAL binary to produce an artifact,
    then run golden + invariant + characterization against it. Guarded by
    shutil.which(cmake/ctest) so the suite stays portable."""
    # Copy the C++ sample sources (not any build dir) so CMake configures fresh.
    src = CPPAPP
    repo = str(tmp_path / "cppapp")
    os.makedirs(repo)
    for name in os.listdir(src):
        if name == "build":
            continue
        s = os.path.join(src, name)
        d = os.path.join(repo, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    # Configure + build the real sample via the adapter's local build command.
    adapter = verify.load_adapter(CPP_ADAPTER)
    build = verify.run_build_phase(verify.resolve_build_command(adapter), repo)
    assert build["passed"] is True, build

    # Produce a REAL artifact from the compiled C++ logic. We write a tiny driver
    # that links the built mathops and emits a JSON report, compile it, and run
    # it. This is the C++ counterpart of the Python-produced artifact.
    driver = os.path.join(repo, "report_main.cpp")
    _write(
        driver,
        '#include "mathops.h"\n'
        "#include <cstdio>\n"
        "int main() {\n"
        '  std::printf("[{\\"a\\":2,\\"b\\":3,\\"sum\\":%d},'
        '{\\"a\\":4,\\"b\\":5,\\"sum\\":%d}]\\n",'
        " mathops::add(2,3), mathops::add(4,5));\n"
        "  return 0;\n"
        "}\n",
    )
    binary = os.path.join(repo, "report_main")
    compile_cmd = [
        "g++", "-std=c++14", "-I", os.path.join(repo, "include"),
        driver, os.path.join(repo, "src", "mathops.cpp"), "-o", binary,
    ]
    cc = subprocess.run(compile_cmd, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, universal_newlines=True)
    if cc.returncode != 0:
        pytest.skip("no C++ compiler to build the artifact driver: " + cc.stderr)
    run = subprocess.run([binary], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, universal_newlines=True)
    assert run.returncode == 0, run.stderr

    # The cppapp sample now ships a committed tests/golden tree (FEAT-004), so
    # these dirs may already exist in the copy; exist_ok keeps this older test
    # valid. This test writes its OWN golden copy below (a reordered-key variant)
    # to prove json mode independently of the committed byte-exact golden.
    os.makedirs(os.path.join(repo, "out"), exist_ok=True)
    os.makedirs(os.path.join(repo, "tests", "golden"), exist_ok=True)
    produced = os.path.join(repo, "out", "report.json")
    _write(produced, run.stdout.strip())
    # Golden: same structure, reordered keys -> json mode must still match.
    rows = json.loads(run.stdout)
    reordered = [dict(reversed(list(r.items()))) for r in rows]
    _write(os.path.join(repo, "tests", "golden", "report.json"), json.dumps(reordered))

    cpp_eval_adapter = {
        "name": "cpp",
        "golden": {
            "artifact_glob": ["out/*.json"],
            "golden_dir": "tests/golden",
            "compare": "json",
        },
        # The C++ characterization "capture" is simply running the real binary.
        "characterization": {"capture_command": binary},
        "invariants": {"checks": ["row_count_matches_input", "no_nans", "roundtrip_identity"]},
    }

    golden = evaldata.mine_golden(repo, cpp_eval_adapter)
    assert len(golden) == 1 and golden[0]["match"] is True, golden

    inv = evaldata.run_invariants(
        repo, cpp_eval_adapter,
        {"produced_path": produced, "input_row_count": 2},
    )
    assert all(c["held"] for c in inv), inv

    char = evaldata.harvest_characterization(repo, cpp_eval_adapter)
    assert char is not None
    assert char["returncode"] == 0
    assert char["output_hash"]  # stable frozen hash of the real binary's stdout
