#!/usr/bin/env python3
"""APIA Layer-1 grounded eval-data generation (Stage 4 TEST / self-correction).

This module builds the DETERMINISTIC, off-model "ground truth" the Layer-2
self-correction loop grades a change against. Truth is derived from the REAL
repository and the REAL output of the REAL code, never from the model's
restatement of the request. The generators here are ordered by the user's stated
trustworthiness order (also encoded in ``.apia/config.yml`` ``eval_data.trust_order``):

  (a) CHARACTERIZATION harvesting - run the adapter's existing capture command,
      record its ACTUAL stdout and freeze it with a stable hash. This is a
      RECORDING of reality, so it is the most trustworthy signal.
  (b) GOLDEN-FILE / FIXTURE mining - language-neutral artifact comparison between
      freshly produced artifacts and a committed golden tree (bytes by default,
      with text and json modes). First-class and NOT language-specific.
  (c) INVARIANT / PROPERTY checks - built-in, example-free structural properties
      that must hold regardless of the exact values (row_count_matches_input,
      no_nans, roundtrip_identity).
  (d) DIFFERENTIAL old-vs-new - the artifact differential that EXTENDS the
      existing before/after regression delta. The test-COUNTS differential
      already lives in ``verify.compare_to_baseline`` and is NOT duplicated here;
      ``differential_artifacts`` adds the ARTIFACT differential on top.

HARD CONSTRAINT (confirmed with the user; see docs/ARCHITECTURE.md sections 1-3):
this module performs ZERO network and ZERO LLM calls. It is PURE LOCAL DATA
ASSEMBLY. It must NEVER import ``requests``, ``urllib``-for-network, ``openai``,
``anthropic`` or any other LLM/network SDK, and must never reach a remote/model
endpoint. The ONLY subprocess it ever launches is the adapter's own LOCAL
``characterization.capture_command``, run through the SAME shell-free local-run
pattern verify.py uses (``verify._run`` / ``verify._split_command``). Everything
else is in-process file reading and comparison.

    # assert: no network/LLM calls anywhere in this module -- the only
    # subprocess spawned is the adapter's local capture_command.

The dependency surface mirrors ingest.py/verify.py: Python standard library plus
optional PyYAML (only ever used indirectly via verify.load_adapter). This module
assembles data; it makes NO model calls and NO green/stop decisions itself.
"""

# NOTE: standard-library only. There is deliberately NO networking/LLM import
# here -- see the module docstring's HARD CONSTRAINT. The single subprocess is
# reused from verify._run (the adapter's local capture_command).
import glob
import hashlib
import json
import os
import sys

# Reuse verify.py's shared local-run primitives (``_run`` and
# ``_split_command``) so characterization harvesting spawns the adapter's local
# command through the EXACT same shell-free path the test phases use, rather than
# duplicating subprocess handling. The scripts dir is this file's directory, so
# make it importable regardless of the caller's cwd (mirrors how verify.py puts
# the scripts dir on sys.path to import ingest). This import is a LOCAL
# stdlib-only module and performs no network/LLM calls.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import verify  # noqa: E402  (reused for _run/_split_command local subprocess)
except ImportError:  # pragma: no cover - verify ships next to this module
    verify = None


def _sha256_bytes(data):
    """Return the hex sha256 digest of a bytes object."""
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text):
    """Return the hex sha256 digest of a string (utf-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_bytes(path):
    """Read a file as raw bytes, or return None when it is absent/unreadable."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _normalize_newlines(text):
    """Normalize CRLF/CR line endings to LF and strip a trailing newline.

    Used by the ``text`` compare mode so a produced artifact and its golden are
    considered equal when they differ ONLY in line-ending style or a single
    trailing newline (common and semantically irrelevant text noise).
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _canonical_json(value):
    """Return an order-insensitive canonical JSON string for deep comparison.

    Object keys are sorted recursively so that ``{"a":1,"b":2}`` and
    ``{"b":2,"a":1}`` compare equal; list order IS preserved (array order is
    semantically significant). Separators are fixed so whitespace never matters.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


# ---------------------------------------------------------------------------
# (b) GOLDEN-FILE / FIXTURE MINING (language-neutral, first-class)
# ---------------------------------------------------------------------------

def compare_artifact(produced_path, golden_path, mode="bytes"):
    """Compare a produced artifact against a committed golden, three ways.

    ``mode`` selects the comparison:
      * ``bytes`` (default, language-neutral): sha256 byte-identity. Works for
        any artifact -- *.csv/*.json/*.pptx/*.png and any binary file. This is
        the trust default and is NOT language-specific.
      * ``text``: compare after normalizing newlines (CRLF/CR -> LF) and a
        trailing newline, so only meaningful text differences count.
      * ``json``: parse both sides and deep-compare order-insensitively for
        object keys (array order preserved); whitespace/key-order noise ignored.

    Returns a structured dict::

        {match: bool, mode, diff_summary, produced_hash, golden_hash}

    ``produced_hash``/``golden_hash`` are the sha256 of the compared
    representation (raw bytes for bytes mode; the normalized text / canonical
    JSON encoded utf-8 for text/json modes), so equal artifacts always share a
    hash and differing ones do not. ``diff_summary`` is an empty string on a
    match and a short human-readable reason on a mismatch (missing file, parse
    error, or the first differing detail).
    """
    produced = _read_bytes(produced_path)
    golden = _read_bytes(golden_path)

    # Missing-file handling is uniform across modes: a non-existent produced or
    # golden artifact is never a match, and the reason is explicit.
    if produced is None or golden is None:
        missing = []
        if produced is None:
            missing.append("produced ({})".format(produced_path))
        if golden is None:
            missing.append("golden ({})".format(golden_path))
        return {
            "match": False,
            "mode": mode,
            "diff_summary": "missing artifact: " + ", ".join(missing),
            "produced_hash": None if produced is None else _sha256_bytes(produced),
            "golden_hash": None if golden is None else _sha256_bytes(golden),
        }

    if mode == "bytes":
        ph = _sha256_bytes(produced)
        gh = _sha256_bytes(golden)
        match = ph == gh
        return {
            "match": match,
            "mode": "bytes",
            "diff_summary": "" if match else (
                "byte mismatch: produced {} bytes (sha {}), golden {} bytes "
                "(sha {})".format(len(produced), ph[:12], len(golden), gh[:12])
            ),
            "produced_hash": ph,
            "golden_hash": gh,
        }

    if mode == "text":
        try:
            ptext = _normalize_newlines(produced.decode("utf-8"))
            gtext = _normalize_newlines(golden.decode("utf-8"))
        except UnicodeDecodeError as exc:
            return {
                "match": False,
                "mode": "text",
                "diff_summary": "not utf-8 text: {}".format(exc),
                "produced_hash": _sha256_bytes(produced),
                "golden_hash": _sha256_bytes(golden),
            }
        ph = _sha256_text(ptext)
        gh = _sha256_text(gtext)
        match = ptext == gtext
        return {
            "match": match,
            "mode": "text",
            "diff_summary": "" if match else _first_text_diff(ptext, gtext),
            "produced_hash": ph,
            "golden_hash": gh,
        }

    if mode == "json":
        try:
            pval = json.loads(produced.decode("utf-8"))
            gval = json.loads(golden.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return {
                "match": False,
                "mode": "json",
                "diff_summary": "invalid JSON: {}".format(exc),
                "produced_hash": _sha256_bytes(produced),
                "golden_hash": _sha256_bytes(golden),
            }
        pcanon = _canonical_json(pval)
        gcanon = _canonical_json(gval)
        ph = _sha256_text(pcanon)
        gh = _sha256_text(gcanon)
        match = pcanon == gcanon
        return {
            "match": match,
            "mode": "json",
            "diff_summary": "" if match else (
                "json structure differs (order-insensitive): produced {} vs "
                "golden {}".format(_truncate(pcanon), _truncate(gcanon))
            ),
            "produced_hash": ph,
            "golden_hash": gh,
        }

    # Unknown mode: fail closed with a clear reason rather than silently
    # defaulting (keeps the trust story explicit).
    return {
        "match": False,
        "mode": mode,
        "diff_summary": "unknown compare mode {!r} (expected bytes|text|json)".format(mode),
        "produced_hash": _sha256_bytes(produced),
        "golden_hash": _sha256_bytes(golden),
    }


def _truncate(text, limit=120):
    """Shorten a long string for a compact diff summary."""
    return text if len(text) <= limit else text[:limit] + "..."


def _first_text_diff(ptext, gtext):
    """Return a short description of the first differing line (text mode)."""
    plines = ptext.split("\n")
    glines = gtext.split("\n")
    for i, (pl, gl) in enumerate(zip(plines, glines), 1):
        if pl != gl:
            return "text differs at line {}: produced {!r} vs golden {!r}".format(
                i, _truncate(pl, 60), _truncate(gl, 60)
            )
    # No per-line difference within the common prefix -> a length difference.
    return "text differs in length: produced {} lines vs golden {} lines".format(
        len(plines), len(glines)
    )


def _golden_block(adapter):
    """Return the adapter's ``golden`` block as a dict (empty when absent)."""
    block = (adapter or {}).get("golden")
    return block if isinstance(block, dict) else {}


def _resolve_compare_mode(compare, pattern):
    """Resolve the compare mode for a given ``artifact_glob`` entry.

    ``compare`` may be EITHER:
      * a plain string (``bytes`` | ``text`` | ``json``) applied to EVERY glob
        (the original, backward-compatible shape), or
      * a mapping of ``glob -> mode`` so a block that mixes artifact kinds under
        one ``artifact_glob`` (e.g. ``out/**/*.json`` as ``json`` and
        ``out/**/*.csv`` as ``text``) resolves a correct per-glob mode instead of
        forcing one global mode onto an incompatible artifact.

    When ``compare`` is a mapping, the mode is looked up by the exact glob
    pattern string; a glob with no entry falls back to the mapping's optional
    ``default`` key, else the language-neutral ``bytes``. A string or absent
    ``compare`` ignores ``pattern`` and returns the single global mode (default
    ``bytes``).
    """
    if isinstance(compare, dict):
        if pattern in compare:
            return compare[pattern] or "bytes"
        return compare.get("default") or "bytes"
    return compare or "bytes"


def mine_golden(repo_path, adapter):
    """Mine golden-file comparisons from the adapter's ``golden`` block.

    Reads the adapter's optional ``golden`` section (artifact_glob / golden_dir /
    compare), resolves each produced artifact matched by ``artifact_glob`` under
    ``repo_path`` against its same-named committed golden under ``golden_dir``,
    and returns a list of per-artifact comparison dicts (each the
    ``compare_artifact`` result enriched with ``artifact`` and ``golden``
    relative paths).

    This is the language-neutral, first-class golden path: byte comparison is the
    trust default (``compare: bytes``), but the adapter may pin ``text`` or
    ``json``. ``compare`` is resolved PER GLOB: it may be a single global string
    (applied to every glob -- the original shape) OR a ``glob -> mode`` mapping so
    a block whose ``artifact_glob`` mixes artifact kinds (JSON + CSV) compares
    each kind under a mode that is valid for it (see ``_resolve_compare_mode``).
    An adapter with no ``golden`` block, no ``golden_dir``, or no matching
    produced artifacts simply returns an empty list (no golden signal).
    """
    block = _golden_block(adapter)
    globs = block.get("artifact_glob") or []
    golden_dir = block.get("golden_dir")
    compare = block.get("compare")

    results = []
    if not golden_dir or not globs:
        return results

    abs_golden_dir = os.path.join(repo_path, golden_dir)
    seen = set()
    for pattern in globs:
        mode = _resolve_compare_mode(compare, pattern)
        for produced in sorted(glob.glob(os.path.join(repo_path, pattern),
                                         recursive=True)):
            # Never treat a committed golden as its own produced artifact (the
            # glob can match inside golden_dir); the golden tree is the oracle.
            rel = os.path.relpath(produced, repo_path)
            if rel in seen:
                continue
            norm = os.path.normpath(rel)
            if norm == os.path.normpath(golden_dir) or norm.startswith(
                os.path.normpath(golden_dir) + os.sep
            ):
                continue
            seen.add(rel)
            golden_path = os.path.join(abs_golden_dir, os.path.basename(produced))
            result = compare_artifact(produced, golden_path, mode)
            result["artifact"] = rel
            result["golden"] = os.path.relpath(golden_path, repo_path)
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# (a) CHARACTERIZATION HARVESTING
# ---------------------------------------------------------------------------

def _characterization_command(adapter):
    """Return the adapter's ``characterization.capture_command`` or None."""
    block = (adapter or {}).get("characterization")
    if isinstance(block, dict):
        return block.get("capture_command")
    return None


def harvest_characterization(repo_path, adapter):
    """Run the adapter's capture_command and freeze its stdout as recorded truth.

    When the adapter declares ``characterization.capture_command`` (a LOCAL
    command that runs the existing code and prints DETERMINISTIC output), this
    runs ONLY that command via the shared shell-free local-run pattern
    (``verify._split_command`` + ``verify._run``, the same path the test phases
    use), captures its stdout, and freezes it with a stable sha256 hash. The same
    input always yields the same hash, so the frozen record is a RECORDING of
    reality, not a prediction.

    Returns ``None`` when no capture_command is declared (no characterization
    signal), otherwise::

        {command, returncode, captured_output, output_hash}

    The ONLY subprocess spawned by this module lives here.
    """
    command = _characterization_command(adapter)
    if not command:
        return None
    if verify is None:  # pragma: no cover - verify always ships alongside
        raise RuntimeError("verify module unavailable for local command run")

    argv = verify._split_command(command)
    returncode, stdout, _stderr = verify._run(argv, cwd=repo_path)
    captured = stdout or ""
    return {
        "command": " ".join(argv),
        "returncode": returncode,
        "captured_output": captured,
        "output_hash": _sha256_text(captured),
    }


# ---------------------------------------------------------------------------
# (c) INVARIANT / PROPERTY CHECKS
# ---------------------------------------------------------------------------
#
# Each built-in check is a deterministic, example-free property that must hold
# regardless of the specific request. A check takes the resolved produced
# artifact path and a ``context`` dict (optional supporting values such as the
# expected input row count) and returns {check_id, held: bool, detail}. No
# oracle/example is needed: these are properties, not expected values.

def _load_rows(path):
    """Load a tabular/JSON artifact into a list of row dicts/values.

    Supports a JSON array (list of objects or scalars), a JSON object with a
    top-level ``rows`` list, and a simple CSV (header + data rows). Returns None
    when the artifact cannot be read or parsed into rows.
    """
    raw = _read_bytes(path)
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped[:1] in ("[", "{"):
        try:
            value = json.loads(text)
        except ValueError:
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("rows"), list):
            return value["rows"]
        return None
    # CSV fallback: parse with the stdlib csv reader (header + data rows).
    import csv
    import io
    reader = csv.DictReader(io.StringIO(text))
    try:
        return list(reader)
    except csv.Error:
        return None


# Sentinel strings that count as a NaN / empty cell for ``no_nans``.
_NAN_SENTINELS = {"", "nan", "none", "null", "n/a", "na"}


def _is_nan_value(value):
    """Return True when a cell value is a NaN/empty sentinel."""
    if value is None:
        return True
    if isinstance(value, float):
        # NaN is the only float that is not equal to itself.
        return value != value
    if isinstance(value, str):
        return value.strip().lower() in _NAN_SENTINELS
    return False


def _check_row_count_matches_input(repo_path, produced_path, context):
    """Produced row count must equal the supplied ``input_row_count``.

    ``context['input_row_count']`` carries the number of input rows (the
    property has no meaning without it, so a missing count is reported as a
    not-held check with an explanatory detail rather than silently passing).
    """
    rows = _load_rows(produced_path)
    if rows is None:
        return False, "could not read produced rows from {}".format(produced_path)
    expected = context.get("input_row_count")
    if expected is None:
        return False, "no input_row_count supplied in context"
    actual = len(rows)
    held = actual == expected
    return held, ("" if held else
                  "row count {} != input row count {}".format(actual, expected))


def _check_no_nans(repo_path, produced_path, context):
    """No cell in the produced tabular/JSON artifact may be NaN/empty."""
    rows = _load_rows(produced_path)
    if rows is None:
        return False, "could not read produced rows from {}".format(produced_path)
    for i, row in enumerate(rows):
        values = row.values() if isinstance(row, dict) else [row]
        for value in values:
            if _is_nan_value(value):
                return False, "NaN/empty value in row {}: {!r}".format(i, value)
    return True, ""


def _check_roundtrip_identity(repo_path, produced_path, context):
    """serialize -> deserialize -> serialize must be byte-identical.

    Reads the produced JSON artifact, re-serializes it canonically, parses that
    back, and re-serializes again; the two canonical serializations must be
    byte-identical. This proves the artifact survives a load/dump roundtrip
    without loss (the property holds regardless of the specific values).
    """
    raw = _read_bytes(produced_path)
    if raw is None:
        return False, "could not read produced artifact {}".format(produced_path)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return False, "artifact is not roundtrippable JSON: {}".format(exc)
    first = _canonical_json(value)
    second = _canonical_json(json.loads(first))
    held = first == second
    return held, ("" if held else "roundtrip not identity-preserving")


# Invariant id -> check callable. Unknown ids in the adapter are ignored.
_INVARIANT_REGISTRY = {
    "row_count_matches_input": _check_row_count_matches_input,
    "no_nans": _check_no_nans,
    "roundtrip_identity": _check_roundtrip_identity,
}


def _invariant_block(adapter):
    """Return the adapter's ``invariants`` block as a dict (empty when absent)."""
    block = (adapter or {}).get("invariants")
    return block if isinstance(block, dict) else {}


def run_invariants(repo_path, adapter, context=None):
    """Run the adapter's declared invariant checks against the produced artifact.

    Reads ``invariants.checks`` from the adapter and runs each RECOGNIZED built-in
    check (``row_count_matches_input`` / ``no_nans`` / ``roundtrip_identity``)
    against the produced artifact named in ``context['produced_path']``. Unknown
    check ids are ignored (the schema contract). Returns a list of
    ``{check_id, held: bool, detail}`` dicts.

    ``context`` carries supporting values a check needs: ``produced_path`` (the
    artifact to inspect) and, for ``row_count_matches_input``, ``input_row_count``.
    These are properties, so no expected-value oracle is needed.
    """
    context = context or {}
    produced_path = context.get("produced_path")
    checks = _invariant_block(adapter).get("checks") or []

    results = []
    for check_id in checks:
        fn = _INVARIANT_REGISTRY.get(check_id)
        if fn is None:
            # Unknown id: ignored per the adapter schema contract.
            continue
        if not produced_path:
            results.append({
                "check_id": check_id,
                "held": False,
                "detail": "no produced_path supplied in context",
            })
            continue
        held, detail = fn(repo_path, produced_path, context)
        results.append({"check_id": check_id, "held": bool(held), "detail": detail})
    return results


# ---------------------------------------------------------------------------
# (d) DIFFERENTIAL old-vs-new (ARTIFACT differential)
# ---------------------------------------------------------------------------

def differential_artifacts(old_dir, new_dir, globs, mode="bytes"):
    """Compare the SAME inputs' produced artifacts old-vs-new, per artifact.

    Runs ``compare_artifact`` for every artifact matched by ``globs`` under both
    ``old_dir`` and ``new_dir`` and reports which stayed byte-identical and which
    changed, so a reviewer can assert that unchanged artifacts are unchanged and
    only the intended artifact differs. Returns::

        {unchanged: [rel, ...], changed: [{artifact, ...compare_artifact}], mode}

    DESIGN NOTE: this is the ARTIFACT differential for Layer-1. The test-COUNTS
    differential (passed/failed delta + removed/added nodes) already lives in
    ``verify.compare_to_baseline`` (persisted via save_baseline/load_baseline);
    this function deliberately does NOT reinvent it -- it extends the before/after
    story to the produced artifacts.
    """
    unchanged = []
    changed = []
    seen = set()
    for pattern in globs:
        for old_path in sorted(glob.glob(os.path.join(old_dir, pattern),
                                         recursive=True)):
            rel = os.path.relpath(old_path, old_dir)
            if rel in seen:
                continue
            seen.add(rel)
            new_path = os.path.join(new_dir, rel)
            result = compare_artifact(old_path, new_path, mode)
            if result["match"]:
                unchanged.append(rel)
            else:
                entry = {"artifact": rel}
                entry.update(result)
                changed.append(entry)
    # Also surface artifacts that exist ONLY in new_dir (added by the change).
    for pattern in globs:
        for new_path in sorted(glob.glob(os.path.join(new_dir, pattern),
                                         recursive=True)):
            rel = os.path.relpath(new_path, new_dir)
            if rel in seen:
                continue
            seen.add(rel)
            old_path = os.path.join(old_dir, rel)
            if not os.path.exists(old_path):
                result = compare_artifact(old_path, new_path, mode)
                entry = {"artifact": rel}
                entry.update(result)
                changed.append(entry)
    return {
        "unchanged": sorted(unchanged),
        "changed": sorted(changed, key=lambda e: e["artifact"]),
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Single assembly entry point
# ---------------------------------------------------------------------------

def assemble_eval_data(repo_path, adapter, options=None):
    """Gather whichever Layer-1 sources the adapter/config enable into one dict.

    This is the single entry a caller (and the FEAT-003 loop) consumes. It is
    PURE DATA ASSEMBLY -- it runs the deterministic generators and collects their
    structured results; it makes NO model calls and NO green/stop decision.

    ``options`` selects which sources to assemble and supplies their inputs:
      * ``characterization`` (bool, default True): harvest if the adapter
        declares a capture_command.
      * ``golden`` (bool, default True): mine golden comparisons from the adapter.
      * ``invariants`` (bool, default True): run invariant checks. Needs
        ``invariant_context`` (dict with ``produced_path`` and optionally
        ``input_row_count``) to be meaningful.
      * ``differential`` (dict, optional): ``{old_dir, new_dir, globs, mode}``;
        when present, runs the ARTIFACT differential.

    Each enabled-but-empty source yields ``None``/``[]`` rather than being
    omitted, so the returned shape is stable:

        {repo, adapter, characterization, golden, invariants, differential,
         trust_order}
    """
    options = options or {}
    repo_path = os.path.abspath(repo_path)

    data = {
        "repo": repo_path,
        "adapter": (adapter or {}).get("name"),
        "characterization": None,
        "golden": [],
        "invariants": [],
        "differential": None,
        # The trust order is fixed by the user's policy (also in config.yml); the
        # loop prefers a higher-trust signal when more than one is available.
        "trust_order": ["characterization", "golden", "invariant", "differential"],
    }

    if options.get("characterization", True):
        data["characterization"] = harvest_characterization(repo_path, adapter)

    if options.get("golden", True):
        data["golden"] = mine_golden(repo_path, adapter)

    if options.get("invariants", True):
        inv_ctx = options.get("invariant_context") or {}
        data["invariants"] = run_invariants(repo_path, adapter, inv_ctx)

    diff = options.get("differential")
    if isinstance(diff, dict):
        data["differential"] = differential_artifacts(
            diff.get("old_dir"),
            diff.get("new_dir"),
            diff.get("globs") or [],
            diff.get("mode") or "bytes",
        )

    return data
