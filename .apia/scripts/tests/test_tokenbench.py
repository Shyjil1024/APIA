"""Tests for the APIA context-efficiency benchmark (.apia/scripts/tokenbench.py).

Coverage (per tokenbench-design.md §10). The measurement core is PURE over a file
list, so every number is checked against hand-computed values on tiny fixtures. The
only integration-flavored piece is the real ``ingest.ingest()`` call (test 9), which
is still network/scanner-free (pure static analysis), so it runs deterministically
everywhere. The no-network/no-LLM static guard, the recorded tokenizer identity, the
graceful heuristic fallback, determinism, and the four-surface scope note are all
asserted. The tiktoken-present case is ``skipif``-guarded so the suite stays green
whether or not the optional dependency is installed.
"""

import builtins
import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest       # noqa: E402
import tokenbench   # noqa: E402

REPO_ROOT = tokenbench.REPO_ROOT

try:
    import tiktoken as _tiktoken  # noqa: F401  availability probe only
    _TIKTOKEN_PRESENT = True
except Exception:  # pragma: no cover - environment-dependent
    _TIKTOKEN_PRESENT = False


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _write(root, rel, text):
    """Write a text file under ``root`` creating parent dirs."""
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def _synthetic_project(label, baseline, apia, include_apia=False):
    """A minimal per-project dict shaped like measure_project's output."""
    return {
        "label": label,
        "repo": label,
        "include_apia": include_apia,
        "baseline": baseline,
        "apia": apia,
        "reduction": tokenbench.reduction(baseline, apia),
    }


# ---------------------------------------------------------------------------
# 1. Reduction math (FR3).
# ---------------------------------------------------------------------------

def test_reduction_math():
    red = tokenbench.reduction({"bytes": 100, "tokens": 25}, {"bytes": 40, "tokens": 10})
    assert red["bytes_abs"] == 60
    assert red["bytes_pct"] == pytest.approx(60.0)
    assert red["tokens_abs"] == 15
    assert red["tokens_pct"] == pytest.approx(60.0)
    # Negative reduction (APIA larger) is reported honestly, never clamped.
    neg = tokenbench.reduction({"bytes": 10, "tokens": 2}, {"bytes": 15, "tokens": 4})
    assert neg["bytes_abs"] == -5
    assert neg["bytes_pct"] == pytest.approx(-50.0)
    # Zero baseline -> None percentage (no crash), abs still reported.
    zero = tokenbench.reduction({"bytes": 0, "tokens": 0}, {"bytes": 7, "tokens": 3})
    assert zero["bytes_pct"] is None
    assert zero["tokens_pct"] is None
    assert zero["bytes_abs"] == -7


# ---------------------------------------------------------------------------
# 2. Aggregate pooled totals (OD-5).
# ---------------------------------------------------------------------------

def test_aggregate_pooled():
    projects = [
        _synthetic_project("big", {"bytes": 1000, "tokens": 250}, {"bytes": 100, "tokens": 25}),
        _synthetic_project("small", {"bytes": 10, "tokens": 2}, {"bytes": 20, "tokens": 5}),
    ]
    agg = tokenbench.aggregate(projects)
    assert agg["method"] == "pooled-totals"
    # Pooled sums, NOT an average of per-project percentages.
    assert agg["baseline"]["bytes"] == 1010
    assert agg["apia"]["bytes"] == 120
    assert agg["baseline"]["tokens"] == 252
    assert agg["apia"]["tokens"] == 30
    assert agg["reduction"]["bytes_abs"] == 890
    assert agg["reduction"]["bytes_pct"] == pytest.approx(100.0 * 890 / 1010)
    # Mean of per-project percentages would be (90 + -100)/2 = -5.0; prove we do NOT do that.
    assert agg["reduction"]["bytes_pct"] != pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# 3. Shared-universe invariant (FR4 / NFR-INV, Acceptance 5).
# ---------------------------------------------------------------------------

def _build_tiny_repo(root):
    _write(root, "pkg/__init__.py", "x = 1\n")
    _write(root, "pkg/core.py", "def f():\n    return 1\n")
    _write(root, "tests/test_core.py", "def test_f():\n    assert True\n")
    _write(root, "README.md", "# demo\n")
    # a binary-ish file that is_countable must reject (NUL byte)
    with open(os.path.join(root, "blob.bin"), "wb") as fh:
        fh.write(b"abc\x00def")


@pytest.mark.parametrize("include_apia", [False, True])
def test_shared_universe(tmp_path, include_apia):
    root = str(tmp_path)
    _build_tiny_repo(root)
    # For the include_apia=True case, seed a carved-out artifact that must drop.
    if include_apia:
        _write(root, tokenbench.SELF_ARTIFACT_PREFIX + "latest.json", "{}\n")

    files = ingest.walk_repo(root, include_apia=include_apia)
    files = [r for r in files
             if not r.replace(os.sep, "/").startswith(tokenbench.SELF_ARTIFACT_PREFIX)]
    expected_countable = {r for r in files if tokenbench.is_countable(root, r)}

    cf = tokenbench.heuristic_token_count
    # The baseline counts exactly the countable subset of the shared universe.
    baseline_countable = [r for r in files if tokenbench.is_countable(root, r)]
    assert set(baseline_countable) == expected_countable
    assert "blob.bin" not in expected_countable  # NUL sniff excluded it

    # The slice is a subset of the shared countable universe.
    slice_rels = tokenbench.retrieval_slice(root, files)
    assert set(slice_rels) <= expected_countable

    # No surviving path references the carved-out benchmark artifact tree.
    assert not any(
        r.replace(os.sep, "/").startswith(tokenbench.SELF_ARTIFACT_PREFIX) for r in files)

    # And the end-to-end measure_project sees the same universe on both sides.
    p = tokenbench.measure_project("tiny", root, include_apia, cf)
    assert p["baseline"]["files"] == len(expected_countable)


# ---------------------------------------------------------------------------
# 4. Tokenizer identity + graceful fallback (FR5 / FR8).
# ---------------------------------------------------------------------------

def test_tokenizer_identity_and_fallback(monkeypatch):
    # Unit-pin the documented heuristic formula.
    assert tokenbench.heuristic_token_count("abcd") == 1
    assert tokenbench.heuristic_token_count("") == 0
    assert tokenbench.heuristic_token_count("a" * 8) == 2

    # Force the ImportError branch -> heuristic fallback + recorded identifier.
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    count_fn, identifier = tokenbench.resolve_tokenizer()
    assert identifier == "heuristic:ceil(len_utf8_bytes/4)"
    assert count_fn("abcd") == 1


@pytest.mark.skipif(not _TIKTOKEN_PRESENT, reason="tiktoken not installed")
def test_tokenizer_tiktoken_present():
    count_fn, identifier = tokenbench.resolve_tokenizer()
    assert identifier == "tiktoken:cl100k_base"
    assert isinstance(count_fn("hello world"), int)


# ---------------------------------------------------------------------------
# 5. Determinism (NFR2, Acceptance 9) — synthetic + real committed corpus.
# ---------------------------------------------------------------------------

def _blank_nonstable(results):
    clone = json.loads(json.dumps(results, sort_keys=True))
    clone["generated_at"] = ""
    return clone


def test_determinism_synthetic(tmp_path):
    root = str(tmp_path)
    _build_tiny_repo(root)
    cf, tid = tokenbench.heuristic_token_count, "heuristic:ceil(len_utf8_bytes/4)"
    p1 = tokenbench.measure_project("tiny", root, False, cf)
    p2 = tokenbench.measure_project("tiny", root, False, cf)
    a = _blank_nonstable(tokenbench.build_results([p1], tid))
    b = _blank_nonstable(tokenbench.build_results([p2], tid))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


_SNAPSHOT = os.path.join(REPO_ROOT, ".apia", "eval", "tokenbench", "baseline.json")
_CORPUS_PRESENT = all(
    os.path.isdir(os.path.abspath(os.path.join(REPO_ROOT, rel)))
    for _, rel, _ in tokenbench.CORPUS)


@pytest.mark.skipif(
    not (_CORPUS_PRESENT and os.path.isfile(_SNAPSHOT)),
    reason="corpus sample path or committed snapshot missing")
def test_determinism_committed_corpus():
    cf, tid = tokenbench.resolve_tokenizer()

    def _run():
        ps = [tokenbench.measure_project(
                  label, os.path.abspath(os.path.join(REPO_ROOT, rel)), inc, cf)
              for label, rel, inc in tokenbench.CORPUS]
        return _blank_nonstable(tokenbench.build_results(ps, tid))

    a = _run()
    b = _run()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    with open(_SNAPSHOT, "r", encoding="utf-8") as fh:
        committed = _blank_nonstable(json.load(fh))
    # A stray working-output `latest.json` (gitignored, regenerated on demand) would
    # perturb the apia_self self-ingest universe relative to the clean committed
    # tree the snapshot was produced from; the snapshot match only holds from a
    # clean tree. Determinism (run == run) above is unconditional.
    stray_latest = os.path.exists(
        os.path.join(REPO_ROOT, ".apia", "eval", "tokenbench", "latest.json"))
    # The committed snapshot records the heuristic (tiktoken absent in the
    # reference env); only compare byte-for-byte when the runtime tokenizer matches.
    if committed["tokenizer"] == a["tokenizer"] and not stray_latest:
        assert json.dumps(a, sort_keys=True) == json.dumps(committed, sort_keys=True)


# ---------------------------------------------------------------------------
# 6. No-network / no-LLM static guard (NFR1) — same token list as the suite.
# ---------------------------------------------------------------------------

def test_no_network_or_llm_guard():
    src_path = os.path.join(SCRIPTS_DIR, "tokenbench.py")
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
        assert token not in src, "tokenbench.py must not reference '{}'".format(token)


# ---------------------------------------------------------------------------
# 7. Schema + serializability (FR9).
# ---------------------------------------------------------------------------

def test_schema_and_serializable():
    projects = [
        _synthetic_project("a", {"bytes": 100, "tokens": 25},
                           {"bytes": 40, "tokens": 10,
                            "knowledge": {"bytes": 30, "tokens": 8, "files": 8},
                            "slice": {"bytes": 10, "tokens": 2, "files": ["a/x.py"]}}),
        _synthetic_project("b", {"bytes": 200, "tokens": 50},
                           {"bytes": 60, "tokens": 15,
                            "knowledge": {"bytes": 50, "tokens": 12, "files": 8},
                            "slice": {"bytes": 10, "tokens": 3, "files": ["b/y.py"]}}),
    ]
    results = tokenbench.build_results(projects, "heuristic:ceil(len_utf8_bytes/4)")
    assert results["schema_version"] == 1
    assert {"generated_at", "tokenizer", "tiktoken_present", "retrieval_slice",
            "scope_note", "projects", "aggregate"} <= set(results)
    assert results["tiktoken_present"] is False
    assert results["retrieval_slice"]["max_files"] == tokenbench.RETRIEVAL_SLICE_MAX
    for p in results["projects"]:
        for side in ("baseline", "apia"):
            assert "bytes" in p[side] and "tokens" in p[side]
        for key in ("bytes_abs", "bytes_pct", "tokens_abs", "tokens_pct"):
            assert key in p["reduction"]
    agg = results["aggregate"]
    assert {"baseline", "apia", "reduction", "method"} <= set(agg)
    # JSON round-trips.
    reloaded = json.loads(json.dumps(results, sort_keys=True))
    assert reloaded == results


# ---------------------------------------------------------------------------
# 8. Engines are imported, not shadowed (FR7).
# ---------------------------------------------------------------------------

def test_engines_not_shadowed():
    assert tokenbench.ingest is ingest
    ingest_path = os.path.abspath(ingest.__file__)
    assert ingest_path.startswith(os.path.abspath(SCRIPTS_DIR) + os.sep)
    # tokenbench must NOT define its own walk/ignore logic.
    assert not hasattr(tokenbench, "walk_repo")
    assert not hasattr(tokenbench, "DEFAULT_IGNORES")


# ---------------------------------------------------------------------------
# 9. End-to-end-lite measurement on a tiny fixture (FR1/FR2).
# ---------------------------------------------------------------------------

def test_measure_on_fixture(tmp_path):
    root = str(tmp_path)
    _build_tiny_repo(root)
    cf = tokenbench.heuristic_token_count

    files = ingest.walk_repo(root, include_apia=False)
    countable = [r for r in files if tokenbench.is_countable(root, r)]
    expected_bytes = 0
    for rel in countable:
        with open(os.path.join(root, rel), "r", encoding="utf-8", errors="replace") as fh:
            expected_bytes += len(fh.read().encode("utf-8"))

    p = tokenbench.measure_project("tiny", root, False, cf)
    # Baseline bytes == sum of countable file sizes-as-counted.
    assert p["baseline"]["bytes"] == expected_bytes
    # Disjoint-sum arithmetic (NOT a de-duplicating union).
    assert p["apia"]["bytes"] == p["apia"]["knowledge"]["bytes"] + p["apia"]["slice"]["bytes"]
    assert p["apia"]["tokens"] == p["apia"]["knowledge"]["tokens"] + p["apia"]["slice"]["tokens"]
    assert p["apia"]["files"] == p["apia"]["knowledge"]["files"] + len(p["apia"]["slice"]["files"])
    # All 8 artifacts were produced (knowledge.files == 8 — ingest is pure static).
    assert p["apia"]["knowledge"]["files"] == len(ingest.ARTIFACTS)
    assert len(ingest.ARTIFACTS) == 8


def test_single_benchmark_layer_walk(tmp_path, monkeypatch):
    """Integrity (design §13 R1): the benchmark layer walks the universe exactly
    ONCE per project (in measure_project); measure_apia MUST NOT walk again — it
    consumes the single shared ``files`` list. Patching ``ingest.walk_repo`` counts
    both the benchmark-layer call AND the one ``ingest.ingest`` performs internally,
    so measure_project totals exactly 2 and measure_apia alone totals exactly 1
    (the internal ingest walk only) — proving no second benchmark-layer walk."""
    root = str(tmp_path)
    _build_tiny_repo(root)
    cf = tokenbench.heuristic_token_count

    real_walk = ingest.walk_repo
    calls = {"n": 0}

    def counting_walk(repo_path, include_apia=False):
        calls["n"] += 1
        return real_walk(repo_path, include_apia=include_apia)

    monkeypatch.setattr(ingest, "walk_repo", counting_walk)

    # measure_apia is handed the shared files list; the ONLY walk it triggers is
    # ingest.ingest's internal one (not a second benchmark-layer walk).
    files = real_walk(root, include_apia=False)  # pre-built via the real fn (uncounted)
    calls["n"] = 0
    tokenbench.measure_apia(root, files, cf, False, "tiny")
    assert calls["n"] == 1  # only ingest.ingest's internal walk

    # measure_project adds exactly one benchmark-layer walk on top of that.
    calls["n"] = 0
    tokenbench.measure_project("tiny", root, False, cf)
    assert calls["n"] == 2  # 1 benchmark-layer (measure_project) + 1 internal (ingest)


# ---------------------------------------------------------------------------
# 10. Scope note surfaces in the two hand-written prose docs (Acceptance 10).
# ---------------------------------------------------------------------------

def test_scope_note_in_prose_surfaces():
    phrases = ["context-size proxy", "NOT billed", "no network", "no LLM"]
    baseline_md = os.path.join(REPO_ROOT, ".apia", "eval", "tokenbench", "BASELINE.md")
    readme = os.path.join(REPO_ROOT, "README.md")
    for path in (baseline_md, readme):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().lower()
        for phrase in phrases:
            assert phrase.lower() in text, "{} missing caveat phrase: {}".format(path, phrase)
