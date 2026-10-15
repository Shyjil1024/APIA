#!/usr/bin/env python3
"""APIA context-efficiency benchmark (``apia-tokenbench``) — the one-command runner.

This is a sibling of the ``apia-eval`` harness (``.apia/scripts/evalbench.py``). It
substantiates APIA's "minimize token/credit usage" design driver with a repeatable,
honestly scoped **context-size proxy** measurement. For each sample project it
measures how much repository context a model would consume under two approaches:

* a **naive full-context baseline** — every tracked, non-binary source file
  concatenated, and
* APIA's **retrieve-over-re-reading** approach — its distilled
  ``.apia/knowledge/*.md`` base (produced by ``ingest.ingest()``) plus a bounded,
  representative "retrieved slice".

It reports the reduction from baseline to APIA in bytes and tokenizer-estimated
tokens, per project and in aggregate.

HARD INTEGRITY CONSTRAINT. This benchmark measures a deterministic CONTEXT-SIZE
PROXY. It MUST NOT call any model and MUST NOT make any network call, exactly like
``ingest.py`` / ``scan.py`` / ``evalbench.py``. The tokens it reports are ESTIMATES
from the recorded tokenizer, NOT billed Copilot tokens, and the benchmark proves no
token/credit/dollar savings. The scope boundary is carried verbatim in the JSON
``scope_note``, the human-summary footer, the committed ``BASELINE.md``, and the
README — the same four-surface discipline ``apia-eval`` uses.

It REUSES the engines (``ingest.walk_repo`` / ``ingest.DEFAULT_IGNORES`` for the file
universe, ``ingest.ingest`` for the knowledge base, ``ingest.discover_tests`` /
``ingest.map_modules`` for the slice) and changes NO engine behavior — it only
imports and reads what they return.
"""

import argparse
import datetime
import json
import math
import os
import shutil
import sys
import tempfile

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import ingest      # noqa: E402  reused public API (walk_repo/DEFAULT_IGNORES/ingest/...)

SCHEMA_VERSION = 1

# Repo root is two dirs up from .apia/scripts/ (.apia/scripts -> .apia -> root).
REPO_ROOT = os.path.dirname(os.path.dirname(_SCRIPTS_DIR))

DEFAULT_OUT = os.path.join(".apia", "eval", "tokenbench", "latest.json")

# Upper bound on the representative "retrieved slice" (design §5).
RETRIEVAL_SLICE_MAX = 8

# The benchmark's own artifact tree. For the apia_self case (include_apia=True)
# walk_repo would otherwise surface this benchmark's own output files, making the
# count self-referential and non-reproducible; it is carved out of the single
# shared universe before either side counts (design §7/§8).
SELF_ARTIFACT_PREFIX = ".apia/eval/tokenbench/"

SCOPE_NOTE = (
    "Measures a deterministic CONTEXT-SIZE PROXY: repo bytes/tokens a model would "
    "read under APIA's distilled knowledge base + a fixed representative retrieved "
    "slice, versus a naive whole-repo baseline. Tokens are ESTIMATES from the "
    "recorded tokenizer, NOT billed Copilot tokens; this proves NO token/credit/"
    "dollar savings. Fully offline; no network, no LLM. Real retrieval for a "
    "specific task may read more or fewer files than the fixed slice."
)

# Extension blocklist for the text/binary rule (design §12). A model would not
# read these as context, so counting them would inflate the proxy.
BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".whl", ".so", ".dll", ".dylib", ".exe", ".bin", ".class", ".pyc",
    ".o", ".a", ".lib", ".pdb", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
    ".mov", ".mp3",
})

# Primary entry-point basenames for the retrieval slice (design §5 tier 1).
_ENTRYPOINT_BASENAMES = frozenset({
    "main.py", "__main__.py", "app.py", "cli.py", "main.cpp", "report_main.cpp",
})

# Fixed, ordered corpus (label, repo_path, include_apia); repo paths resolved
# against REPO_ROOT (design §7).
CORPUS = [
    ("pyapp", "samples/pyapp", False),
    ("legacy_pyapp", "samples/legacy_pyapp", False),
    ("python_cli", "samples/generated/python_cli", False),
    ("apia_self", ".", True),
]


class BenchError(Exception):
    """A fatal harness/usage/config error (exit 2); carries a human message."""


# ---------------------------------------------------------------------------
# Tokenizer (design §6) — optional tiktoken, else a documented heuristic.
# ---------------------------------------------------------------------------

def heuristic_token_count(text):
    """Documented, dependency-free token estimate: ceil(utf-8 bytes / 4)."""
    return math.ceil(len(text.encode("utf-8")) / 4)


def resolve_tokenizer():
    """Return ``(count_fn, identifier)``, preferring tiktoken when importable.

    Mirrors the optional-dependency pattern APIA already uses (evalbench's
    ``try: import yaml`` guard, scan's ``tool_unavailable`` marker): tiktoken is
    NEVER a hard import at module top. On ANY failure (ImportError or an encoding
    that cannot load) it degrades to the documented heuristic and records that in
    the returned identifier, so the output always names the method actually used.
    """
    try:
        import tiktoken  # OPTIONAL — never a hard import at module top
        _enc = tiktoken.get_encoding("cl100k_base")
        count_fn = lambda text: len(_enc.encode(text, disallowed_special=()))
        identifier = "tiktoken:cl100k_base"
    except Exception:  # ImportError or any encoding-load failure
        count_fn = heuristic_token_count
        identifier = "heuristic:ceil(len_utf8_bytes/4)"
    return count_fn, identifier


# ---------------------------------------------------------------------------
# Measurement primitives (pure given a file list — design §4/§12).
# ---------------------------------------------------------------------------

def is_countable(root, rel):
    """Benchmark-only text/binary filter (design §12).

    ``ingest`` applies NO binary-exclusion rule; this layers an extension
    blocklist + a NUL-byte sniff on top of ``walk_repo`` so binaries do not
    inflate the proxy. A file counts iff its extension is not in ``BINARY_EXTS``
    AND the first 8192 bytes contain no NUL. Applied identically to the baseline,
    the knowledge base, and the slice so the shared-universe invariant holds on
    the counted set.
    """
    _, ext = os.path.splitext(rel)
    if ext.lower() in BINARY_EXTS:
        return False
    full = os.path.join(root, rel)
    try:
        with open(full, "rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return False
    return b"\x00" not in chunk


def measure_files(root, rels, count_fn):
    """Count total bytes/tokens over ``rels`` under ``root`` (any directory).

    Content reading mirrors ``ingest``: text is opened with
    ``errors="replace"``. A file that cannot be read (``OSError``/``UnicodeError``)
    is skipped from the count, never aborting the run (design §11). ``root`` may be
    the project repo OR a temp knowledge dir, so the parameter is named generically.
    """
    total_bytes = 0
    total_tokens = 0
    counted = 0
    for rel in rels:
        full = os.path.join(root, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (OSError, UnicodeError):
            continue
        total_bytes += len(text.encode("utf-8"))
        total_tokens += count_fn(text)
        counted += 1
    return {"bytes": total_bytes, "tokens": total_tokens, "files": counted}


def reduction(baseline, apia):
    """Reduction from baseline to APIA, abs + pct, for bytes and tokens.

    pct = 100 * abs / baseline. A zero baseline yields a ``None`` percentage
    (never a crash); the absolute figure is still reported.
    """
    def _pair(base_val, apia_val):
        abs_val = base_val - apia_val
        pct = (100.0 * abs_val / base_val) if base_val else None
        return abs_val, pct

    bytes_abs, bytes_pct = _pair(baseline["bytes"], apia["bytes"])
    tokens_abs, tokens_pct = _pair(baseline["tokens"], apia["tokens"])
    return {
        "bytes_abs": bytes_abs,
        "bytes_pct": bytes_pct,
        "tokens_abs": tokens_abs,
        "tokens_pct": tokens_pct,
    }


def aggregate(projects):
    """Pooled-totals aggregate (design §7 / OD-5): Σbaseline vs Σapia.

    One pooled percentage per metric, NOT a mean of per-project percentages.
    A zero summed baseline yields a ``None`` percentage.
    """
    base_bytes = sum(p["baseline"]["bytes"] for p in projects)
    base_tokens = sum(p["baseline"]["tokens"] for p in projects)
    apia_bytes = sum(p["apia"]["bytes"] for p in projects)
    apia_tokens = sum(p["apia"]["tokens"] for p in projects)
    baseline = {"bytes": base_bytes, "tokens": base_tokens}
    apia = {"bytes": apia_bytes, "tokens": apia_tokens}
    return {
        "baseline": baseline,
        "apia": apia,
        "reduction": reduction(baseline, apia),
        "method": "pooled-totals",
    }


# ---------------------------------------------------------------------------
# The two measurement sides + shared-universe measure_project (design §5/§8).
# ---------------------------------------------------------------------------

def retrieval_slice(repo, files):
    """Deterministic, non-cherry-picked representative slice (design §5 Decision 3).

    Operates on ``C`` — the countable subset of the single carved shared ``files``
    universe. Selects up to ``RETRIEVAL_SLICE_MAX`` files across ordered tiers
    (entry points -> public surface -> one representative test per module group ->
    fill), each tier in its stated total order, de-duplicated as it goes, then the
    result is re-sorted lexicographically for stable output.
    """
    countable = [rel for rel in files if is_countable(repo, rel)]
    test_paths = {d["path"] for d in ingest.discover_tests(repo, files)}

    selected = []
    seen = set()

    def _take(candidate):
        if candidate not in seen:
            seen.add(candidate)
            selected.append(candidate)

    # Tier 1 — primary entry points (lexicographic by repo-relative path).
    tier1 = sorted(r for r in countable if os.path.basename(r) in _ENTRYPOINT_BASENAMES)
    for rel in tier1:
        if len(selected) >= RETRIEVAL_SLICE_MAX:
            break
        _take(rel)

    # Tier 2 — public surface: top-level __init__.py + include/*.h|*.hpp
    # (lexicographic by repo-relative path).
    def _is_public_surface(rel):
        norm = rel.replace(os.sep, "/")
        if norm == "__init__.py":
            return True
        if norm.startswith("include/") and norm.lower().endswith((".h", ".hpp")):
            return True
        return False

    tier2 = sorted(r for r in countable if _is_public_surface(r))
    for rel in tier2:
        if len(selected) >= RETRIEVAL_SLICE_MAX:
            break
        _take(rel)

    # Tier 3 — one representative test per module group: for each top-level group
    # from ingest.map_modules (keys iterated in sorted order), the lexicographically
    # first group member that is also a discover_tests path.
    groups = ingest.map_modules(repo, files)
    for _top in sorted(groups):
        if len(selected) >= RETRIEVAL_SLICE_MAX:
            break
        for rel in sorted(groups[_top]):
            if rel in test_paths and rel in countable:
                _take(rel)
                break

    # Tier 4 — fill from remaining countable sources, shortest path-depth first,
    # then lexicographic.
    tier4 = sorted(countable, key=lambda p: (p.count("/"), p))
    for rel in tier4:
        if len(selected) >= RETRIEVAL_SLICE_MAX:
            break
        _take(rel)

    return sorted(selected)


def measure_baseline(repo, files, count_fn):
    """Naive full-context baseline (FR1): count the countable subset of ``files``."""
    countable = [rel for rel in files if is_countable(repo, rel)]
    return measure_files(repo, countable, count_fn)


def measure_apia(repo, files, count_fn, include_apia, label):
    """APIA figure (FR2) = distilled knowledge base + bounded retrieved slice.

    ``files`` is the single carved, shared universe computed once by
    ``measure_project``; this function MUST NOT walk again. The two parts are
    disjoint by construction (temp-dir ``.md`` artifacts vs repo sources), so
    ``apia.bytes = knowledge.bytes + slice.bytes`` (tokens/files likewise) with no
    de-dup.
    """
    tmp_kdir = tempfile.mkdtemp(prefix="apia-tokenbench-")
    try:
        # full=True makes artifact production unconditional (design §5a); a fresh
        # temp dir alone would only trigger reprocess as a hidden precondition.
        ingest.ingest(repo, tmp_kdir, full=True, include_apia=include_apia)
        produced = [a for a in ingest.ARTIFACTS
                    if os.path.isfile(os.path.join(tmp_kdir, a))]
        if len(produced) != len(ingest.ARTIFACTS):
            raise BenchError(
                "ingest produced {}/{} artifacts for {}".format(
                    len(produced), len(ingest.ARTIFACTS), label))
        # The .index.json change-oracle is NOT counted — it is not model-facing.
        knowledge = measure_files(tmp_kdir, ingest.ARTIFACTS, count_fn)
    finally:
        shutil.rmtree(tmp_kdir, ignore_errors=True)

    slice_rels = retrieval_slice(repo, files)
    slice_measure = measure_files(repo, slice_rels, count_fn)
    slice_block = {
        "bytes": slice_measure["bytes"],
        "tokens": slice_measure["tokens"],
        "files": slice_rels,
    }
    return {
        "bytes": knowledge["bytes"] + slice_measure["bytes"],
        "tokens": knowledge["tokens"] + slice_measure["tokens"],
        "files": knowledge["files"] + len(slice_rels),
        "knowledge": knowledge,
        "slice": slice_block,
    }


def measure_project(label, repo, include_apia, count_fn):
    """Measure one project, enforcing the shared-universe invariant (design §8).

    Calls ``ingest.walk_repo`` ONCE, applies the self-artifact carve-out, and feeds
    that single ``files`` list to BOTH ``measure_baseline`` and ``measure_apia`` so
    neither side can see a different universe.
    """
    files = ingest.walk_repo(repo, include_apia=include_apia)
    files = [r for r in files if not r.replace(os.sep, "/").startswith(SELF_ARTIFACT_PREFIX)]

    baseline = measure_baseline(repo, files, count_fn)
    apia = measure_apia(repo, files, count_fn, include_apia, label)
    repo_rel = os.path.relpath(repo, REPO_ROOT).replace(os.sep, "/")
    return {
        "label": label,
        "repo": repo_rel,
        "include_apia": include_apia,
        "baseline": baseline,
        "apia": apia,
        "reduction": reduction(baseline, apia),
    }


# ---------------------------------------------------------------------------
# Results assembly + human summary (design §9).
# ---------------------------------------------------------------------------

def build_results(projects, tokenizer_id):
    """Assemble the full JSON results object (design §9 schema)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),  # NON-STABLE
        "tokenizer": tokenizer_id,
        "tiktoken_present": tokenizer_id.startswith("tiktoken:"),
        "retrieval_slice": {
            "rule": "entrypoint+public+test+fill",
            "max_files": RETRIEVAL_SLICE_MAX,
        },
        "scope_note": SCOPE_NOTE,
        "projects": projects,
        "aggregate": aggregate(projects),
    }


def _fmt_pct(pct):
    return "n/a" if pct is None else "{:.1f}%".format(pct)


def format_summary(results):
    """Render a scan.format_human-style text summary (design §9 surface 2)."""
    lines = []
    lines.append("APIA tokenbench :: {} project(s)".format(len(results["projects"])))
    for p in results["projects"]:
        red = p["reduction"]
        lines.append(
            "  {:<16} bytes {}->{} ({}) tokens {}->{} ({})".format(
                p["label"],
                p["baseline"]["bytes"], p["apia"]["bytes"], _fmt_pct(red["bytes_pct"]),
                p["baseline"]["tokens"], p["apia"]["tokens"], _fmt_pct(red["tokens_pct"])))
    agg = results["aggregate"]
    ared = agg["reduction"]
    lines.append("  --- aggregate (pooled-totals) ---")
    lines.append("  bytes:  {}->{} reduction {} ({})".format(
        agg["baseline"]["bytes"], agg["apia"]["bytes"],
        ared["bytes_abs"], _fmt_pct(ared["bytes_pct"])))
    lines.append("  tokens: {}->{} reduction {} ({})".format(
        agg["baseline"]["tokens"], agg["apia"]["tokens"],
        ared["tokens_abs"], _fmt_pct(ared["tokens_pct"])))
    lines.append("  tokenizer: {}".format(results["tokenizer"]))
    lines.append("  scope: {}".format(SCOPE_NOTE))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (design §11).
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "APIA context-efficiency benchmark (apia-tokenbench): measures a "
            "deterministic context-size PROXY (bytes + estimated tokens) of APIA's "
            "retrieve-over-re-reading approach vs a naive full-context baseline, "
            "per sample project and in aggregate. No network, no LLM; tokens are "
            "ESTIMATES, not billed Copilot tokens."))
    parser.add_argument(
        "--out", default=None,
        help="Path to write the JSON results (default: {}).".format(DEFAULT_OUT))
    parser.add_argument(
        "--json-only", dest="json_only", action="store_true",
        help="Suppress the human summary (JSON is still written).")
    parser.add_argument(
        "--summary-only", dest="summary_only", action="store_true",
        help="Suppress the JSON file (summary to stdout only).")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.summary_only and args.out is not None:
        parser.error("--out and --summary-only are mutually exclusive")

    try:
        count_fn, tokenizer_id = resolve_tokenizer()
        projects = []
        for label, rel_repo, include_apia in CORPUS:
            repo = os.path.abspath(os.path.join(REPO_ROOT, rel_repo))
            if not os.path.isdir(repo):
                raise BenchError(
                    "corpus repo path does not exist: {} ({})".format(rel_repo, repo))
            projects.append(measure_project(label, repo, include_apia, count_fn))
    except BenchError as exc:
        sys.stderr.write("tokenbench: {}\n".format(exc))
        return 2

    results = build_results(projects, tokenizer_id)

    if not args.summary_only:
        out_path = args.out or DEFAULT_OUT
        try:
            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except OSError as exc:
            sys.stderr.write("tokenbench: cannot write --out {}: {}\n".format(out_path, exc))
            return 2

    if args.json_only:
        # Suppress the human summary; emit the machine-readable JSON to stdout
        # (mirrors evalbench --json-only). The JSON file is ALSO written to --out
        # above unless --summary-only suppressed it.
        sys.stdout.write(json.dumps(results, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_summary(results) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
