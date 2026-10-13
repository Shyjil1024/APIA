#!/usr/bin/env python3
"""APIA off-model scanner engine (Phase-2 Reviewer).

This script is the scriptable, off-model backbone of the APIA Reviewer. It runs
the per-stack LOCAL scanner tools declared in a stack adapter's ``scanners:``
section, normalizes their heterogeneous output into ONE canonical findings
shape, applies configurable severity thresholds, and emits a model-consumable
prioritized findings report. It is the scanning counterpart to verify.py:
verify.py normalizes per-stack TEST output via a ``result_format -> parser``
dispatch; scan.py normalizes per-stack SCANNER output via a
``source_tool -> parser`` dispatch into one uniform findings shape.

HARD CONSTRAINT (identical to ingest.py / verify.py; see
docs/ARCHITECTURE.md sections 1-3): this module performs ZERO network and ZERO
LLM/model calls. It NEVER imports any HTTP client, networking SDK, or model SDK
(no requests / urllib networking / openai / anthropic / httpx / low-level HTTP /
raw sockets), and it NEVER reaches a remote endpoint. Model inference always
runs on the user's
existing Copilot session; this engine only runs the adapter's declared LOCAL
scanner tools and reports grounded findings. The ONLY subprocess it ever
launches is a local scanner tool declared in the adapter (bandit / pip-audit /
ruff / flake8 / radon / cppcheck / clang-tidy). Copilot only SUMMARIZES and
PRIORITIZES the grounded findings this engine produces; it never fabricates
them.

    # assert: no network/LLM calls anywhere in this module -- the only
    # subprocess spawned is a local adapter-declared scanner tool.

CANONICAL FINDING SHAPE (the stable INTERFACE other components consume):

    {
        "severity": one of SEVERITIES ("critical" > "high" > "medium" >
                    "low" > "info"),
        "category": one of CATEGORIES ("security" | "quality" | "perf";
                    "limitations" is reserved for model-facing synthesis),
        "file":     path to the offending file (repo-relative when resolvable),
        "line":     1-based line number (int) or None,
        "rule":     the tool's native rule/check id (e.g. "B602", "uninitvar"),
        "message":  human-readable description,
        "source_tool": the scanner that produced it (e.g. "bandit"),
    }

COMPOSABLE GATE: ``apply_thresholds(findings, thresholds)`` is the SINGLE shared
gate function. Both the standalone reviewer AND a future greenfield-generation
gate (and the future iterate-to-best eval-loop fusion) call it via the same
signature, so the gate is composable without rework. ``scan()`` returns a
JSON-serializable structured report whose ``gate`` block is that decision.

Usage:
    python3 .apia/scripts/scan.py <repo> [--adapter PATH]
            [--category security|quality|perf|all] [--json]
            [--fail-on-severity LEVEL] [--doctor]

Exit code reflects the gate decision (0 = gate passed).
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
import xml.etree.ElementTree as ET

# ingest.py ships the shared, reusable offline toolchain preflight (doctor() /
# format_doctor_report() / DOCTOR_TOOLS). Import it so --doctor and the report's
# tool-presence block reuse the SAME preflight instead of duplicating the PATH
# lookup. The scripts dir is this file's directory; make sure it is importable
# regardless of the caller's cwd (mirrors how the tests put SCRIPTS_DIR on
# sys.path and how verify.py imports ingest). This is a LOCAL stdlib-only module
# that performs no network/LLM calls (enforced by test_ingest's guard).
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import ingest  # noqa: E402  (reused for doctor()/format_doctor_report())
except ImportError:
    ingest = None


# --- Canonical vocabulary ---------------------------------------------------
#
# Severity is an ORDERED vocabulary (most to least severe). The order drives
# both threshold gating (fail_on_severity) and report sorting.
SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Categories a scanner finding is tagged with. ``limitations`` is RESERVED for
# the model-facing synthesis stage (Copilot naming what the scanners could NOT
# check); the off-model engine never emits it, but it is part of the stable
# vocabulary so downstream components can rely on it.
CATEGORIES = ("security", "quality", "perf", "limitations")


def severity_rank(severity):
    """Return a sortable rank for a severity (lower rank == more severe).

    Unknown severities sort last (after ``info``), never crash.
    """
    return _SEVERITY_RANK.get(severity, len(SEVERITIES))


def make_finding(severity, category, file, line, rule, message, source_tool):
    """Build a canonical finding dict (the stable INTERFACE).

    Normalizes severity to the fixed vocabulary (anything unknown -> ``info``)
    and coerces ``line`` to an int or None. Keeping this the single builder
    guarantees every normalizer emits the exact same shape.
    """
    sev = severity if severity in _SEVERITY_RANK else "info"
    try:
        line_no = int(line) if line is not None and str(line) != "" else None
    except (TypeError, ValueError):
        line_no = None
    return {
        "severity": sev,
        "category": category,
        "file": file,
        "line": line_no,
        "rule": rule,
        "message": message,
        "source_tool": source_tool,
    }


# ---------------------------------------------------------------------------
# Per-tool normalizers. Each takes the tool's (stdout, stderr) plus the
# repo_path (to make file paths repo-relative) and the scanner's declared
# category, and returns a list of canonical findings. They mirror verify.py's
# per-format parsers: every one normalizes a tool's NATIVE severity/level into
# the shared SEVERITIES vocabulary and tags category + source_tool. Empty /
# zero-finding output returns an empty list cleanly.
# ---------------------------------------------------------------------------


def _relpath(path, repo_path):
    """Return ``path`` relative to repo_path when possible, else as-is."""
    if not path:
        return path
    try:
        return os.path.relpath(path, repo_path)
    except (ValueError, TypeError):
        return path


def _loads_or_none(text):
    """Parse JSON leniently: find the first {...} or [...] block, else None.

    Some tools print a banner line before their JSON; this tolerates that by
    scanning for the first JSON opener. Returns None on any parse failure so a
    normalizer degrades to "no findings" rather than crashing.
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener in ("[", "{"):
        idx = text.find(opener)
        if idx == -1:
            continue
        closer = "]" if opener == "[" else "}"
        end = text.rfind(closer)
        if end > idx:
            try:
                return json.loads(text[idx:end + 1])
            except ValueError:
                continue
    return None


# bandit severity (LOW/MEDIUM/HIGH) -> canonical. bandit has no "critical".
_BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


def normalize_bandit(stdout, stderr, repo_path, category="security"):
    """Normalize ``bandit -f json`` output into canonical findings.

    Maps bandit's issue_severity (LOW/MEDIUM/HIGH) into the shared vocabulary
    and keys rule off the bandit test id (e.g. B602). Category defaults to
    security.
    """
    data = _loads_or_none(stdout)
    if not isinstance(data, dict):
        return []
    findings = []
    for item in data.get("results", []) or []:
        sev = _BANDIT_SEVERITY.get(
            str(item.get("issue_severity", "")).upper(), "low"
        )
        findings.append(make_finding(
            severity=sev,
            category=category,
            file=_relpath(item.get("filename"), repo_path),
            line=item.get("line_number"),
            rule=item.get("test_id") or item.get("test_name"),
            message=item.get("issue_text", ""),
            source_tool="bandit",
        ))
    return findings


def normalize_pip_audit(stdout, stderr, repo_path, category="security"):
    """Normalize ``pip-audit -f json`` output into canonical findings.

    pip-audit emits ``{"dependencies": [{"name","version","vulns":[...]}]}``.
    Each vuln becomes a HIGH security finding (a known-vulnerable dependency is
    always worth surfacing); rule is the advisory id (PYSEC/GHSA/CVE), file is
    the manifest conceptually (reported as the dependency name since pip-audit
    does not attribute a line).
    """
    data = _loads_or_none(stdout)
    deps = []
    if isinstance(data, dict):
        deps = data.get("dependencies", []) or []
    elif isinstance(data, list):
        deps = data
    findings = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns", []) or []:
            aliases = vuln.get("aliases") or []
            fixes = vuln.get("fix_versions") or []
            msg = "{}=={} is vulnerable: {}".format(
                name, version, vuln.get("description", "") or vuln.get("id", "")
            )
            if aliases:
                msg += " (aliases: {})".format(", ".join(aliases))
            if fixes:
                msg += " [fixed in: {}]".format(", ".join(fixes))
            findings.append(make_finding(
                severity="high",
                category=category,
                file="{}=={}".format(name, version),
                line=None,
                rule=vuln.get("id"),
                message=msg,
                source_tool="pip-audit",
            ))
    return findings


# ruff reports a single "severity" string ("error"/"warning"); map to canonical.
_RUFF_SEVERITY = {"error": "medium", "warning": "low"}


def normalize_ruff(stdout, stderr, repo_path, category="quality"):
    """Normalize ``ruff check --output-format json`` output into findings.

    ruff emits a JSON list of ``{code, message, location:{row,column},
    filename, severity}``. Lint findings are quality by default.
    """
    data = _loads_or_none(stdout)
    if not isinstance(data, list):
        return []
    findings = []
    for item in data:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") or {}
        sev = _RUFF_SEVERITY.get(str(item.get("severity", "")).lower(), "low")
        findings.append(make_finding(
            severity=sev,
            category=category,
            file=_relpath(item.get("filename"), repo_path),
            line=loc.get("row"),
            rule=item.get("code"),
            message=item.get("message", ""),
            source_tool="ruff",
        ))
    return findings


# flake8 default line format: "path:line:col: CODE message".
_FLAKE8_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+(?P<code>\S+)\s+(?P<msg>.*)$")


def normalize_flake8(stdout, stderr, repo_path, category="quality"):
    """Normalize flake8's default ``path:line:col: CODE msg`` output.

    flake8 has no severity levels; everything is a ``low`` quality finding keyed
    off its code (E/W/F...).
    """
    findings = []
    text = (stdout or "") + "\n" + (stderr or "")
    for line in text.splitlines():
        m = _FLAKE8_RE.match(line.strip())
        if not m:
            continue
        findings.append(make_finding(
            severity="low",
            category=category,
            file=_relpath(m.group("file"), repo_path),
            line=m.group("line"),
            rule=m.group("code"),
            message=m.group("msg"),
            source_tool="flake8",
        ))
    return findings


def normalize_radon(stdout, stderr, repo_path, category="perf"):
    """Normalize ``radon cc -j`` complexity JSON into findings.

    radon emits ``{filename: [{name, lineno, complexity, rank, ...}]}``. We only
    surface functions above a complexity floor (rank C or worse, i.e. cc > 10)
    so trivial functions are not noise; higher complexity maps to higher
    severity. Complexity is a perf/quality smell; category defaults to perf.
    """
    data = _loads_or_none(stdout)
    if not isinstance(data, dict):
        return []
    findings = []
    for filename, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            cc = block.get("complexity", 0) or 0
            rank = str(block.get("rank", "")).upper()
            # radon ranks: A(1-5) B(6-10) C(11-20) D(21-30) E(31-40) F(41+).
            # Surface C and worse (genuine complexity smells only).
            if rank in ("A", "B"):
                continue
            if rank in ("E", "F"):
                sev = "high"
            elif rank == "D":
                sev = "medium"
            else:  # C
                sev = "low"
            findings.append(make_finding(
                severity=sev,
                category=category,
                file=_relpath(filename, repo_path),
                line=block.get("lineno"),
                rule="radon-cc-{}".format(rank or "?"),
                message="{} '{}' has cyclomatic complexity {} (rank {})".format(
                    block.get("type", "block"), block.get("name", "?"), cc, rank
                ),
                source_tool="radon",
            ))
    return findings


# cppcheck XML v2 severity -> canonical. cppcheck severities: error, warning,
# style, performance, portability, information.
_CPPCHECK_SEVERITY = {
    "error": "high",
    "warning": "medium",
    "performance": "medium",
    "portability": "low",
    "style": "low",
    "information": "info",
}
# cppcheck categorization: performance -> perf; style/warning/portability ->
# quality; error -> security (memory/undefined behavior is a security concern).
_CPPCHECK_CATEGORY = {
    "error": "security",
    "warning": "quality",
    "style": "quality",
    "performance": "perf",
    "portability": "quality",
    "information": "quality",
}
_CPPCHECK_RESULTS_RE = re.compile(r"<results[\s\S]*?</results>")


def normalize_cppcheck(stdout, stderr, repo_path, category=None):
    """Normalize cppcheck XML v2 output (emitted on STDERR) into findings.

    cppcheck prints its XML (``<results>...</results>``) on stderr, sometimes
    interleaved with plain "Checking ..." progress lines, so we extract the
    ``<results>`` block before parsing. Each ``<error>`` maps its cppcheck
    severity into the shared vocabulary and its kind into a category (perf for
    performance checks, security for hard errors, quality otherwise). The
    scanner_def's declared category is advisory only; cppcheck's own severity
    kind decides the category per finding, so one cppcheck run can feed
    security + quality + perf. Informational meta errors (e.g. checkersReport)
    carry no location and are skipped.
    """
    text = (stderr or "") + "\n" + (stdout or "")
    match = _CPPCHECK_RESULTS_RE.search(text)
    if not match:
        return []
    try:
        root = ET.fromstring(match.group(0))
    except ET.ParseError:
        return []
    findings = []
    errors = root.find("errors")
    if errors is None:
        return []
    for err in errors.findall("error"):
        kind = str(err.get("severity", "")).lower()
        loc = err.find("location")
        if loc is None:
            # Meta/info entries (checkersReport, missingInclude summary) have no
            # location: skip so we never emit a locationless pseudo-finding.
            continue
        sev = _CPPCHECK_SEVERITY.get(kind, "low")
        cat = category or _CPPCHECK_CATEGORY.get(kind, "quality")
        findings.append(make_finding(
            severity=sev,
            category=cat,
            file=_relpath(loc.get("file"), repo_path),
            line=loc.get("line"),
            rule=err.get("id"),
            message=err.get("msg", ""),
            source_tool="cppcheck",
        ))
    return findings


# clang-tidy console: "file:line:col: warning|error: message [check-name]".
_CLANG_TIDY_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<level>warning|error):\s+(?P<msg>.*?)\s*(?:\[(?P<check>[^\]]+)\])?$"
)
_CLANG_TIDY_LEVEL = {"error": "high", "warning": "medium"}


def normalize_clang_tidy(stdout, stderr, repo_path, category="quality"):
    """Normalize clang-tidy console output into findings.

    clang-tidy prints ``file:line:col: warning|error: msg [check]`` lines
    (``note:`` continuation lines are ignored). The bracketed check name is the
    rule; error vs warning maps to high vs medium. Category defaults to quality.
    """
    findings = []
    text = (stdout or "") + "\n" + (stderr or "")
    for line in text.splitlines():
        m = _CLANG_TIDY_RE.match(line.strip())
        if not m:
            continue
        sev = _CLANG_TIDY_LEVEL.get(m.group("level"), "medium")
        findings.append(make_finding(
            severity=sev,
            category=category,
            file=_relpath(m.group("file"), repo_path),
            line=m.group("line"),
            rule=m.group("check") or m.group("level"),
            message=m.group("msg"),
            source_tool="clang-tidy",
        ))
    return findings


# source-tool (the scanner_def's declared `format`) -> normalizer. Mirrors
# verify._RESULT_PARSERS. The `format` key in the adapter selects the parser,
# decoupling the tool's command from how its output is read.
_SOURCE_TOOL_PARSERS = {
    "bandit": normalize_bandit,
    "pip_audit": normalize_pip_audit,
    "ruff": normalize_ruff,
    "flake8": normalize_flake8,
    "radon": normalize_radon,
    "cppcheck": normalize_cppcheck,
    "clang_tidy": normalize_clang_tidy,
}


def _parser_for(fmt):
    """Return the normalizer for a declared format, or None when unknown."""
    return _SOURCE_TOOL_PARSERS.get(fmt)


# ---------------------------------------------------------------------------
# Command assembly + the single subprocess chokepoint (mirrors verify.py).
# ---------------------------------------------------------------------------


def _split_command(command):
    """Split an adapter scanner command into argv, rewriting the interpreter.

    Identical in spirit to verify._split_command: a command spelled
    ``python -m bandit ...`` is rewritten to run under the SAME interpreter that
    launched this engine (``sys.executable``) so bandit/ruff/radon/pip-audit run
    inside the project's .venv. A native tool (``cppcheck``/``clang-tidy`` or an
    absolute path) is left UNCHANGED so the real binary runs as-is.
    """
    parts = shlex.split(command)
    if parts and parts[0] in ("python", "python3"):
        parts[0] = sys.executable
    return parts


def _run(argv, cwd):
    """Run a command locally and capture output. Pure local subprocess.

    This is the ONLY place the engine spawns a process, and it only ever runs a
    local scanner tool declared in the adapter -- never a network client. A
    missing tool raises FileNotFoundError, which run_scanner turns into a
    structured tool_unavailable marker (never a crash, never a fabricated
    finding).
    """
    proc = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,  # text mode
    )
    return proc.returncode, proc.stdout, proc.stderr


def _tail(text, max_lines=15):
    """Keep only the last few lines of output so the report stays compact."""
    if not text:
        return ""
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-max_lines:])


def _scanner_exe(scanner_def):
    """Return the executable name a scanner_def invokes (for presence checks).

    For a ``python -m <mod> ...`` command the real tool is the current
    interpreter, so we report the module (argv after ``-m``) as the logical
    tool name; otherwise argv[0] (the native tool, e.g. cppcheck).
    """
    parts = shlex.split(scanner_def.get("command", ""))
    if not parts:
        return None
    if parts[0] in ("python", "python3") and len(parts) >= 3 and parts[1] == "-m":
        return parts[2]
    return parts[0]


def _tool_present(scanner_def):
    """Return True when the scanner_def's tool is reachable.

    A ``python -m <mod>`` command runs under sys.executable (always present), so
    we additionally confirm the module imports; a native tool is checked on
    PATH. Pure local lookups, no execution of project code.
    """
    import shutil
    import importlib

    parts = shlex.split(scanner_def.get("command", ""))
    if not parts:
        return False
    if parts[0] in ("python", "python3") and len(parts) >= 3 and parts[1] == "-m":
        module = parts[2]
        try:
            importlib.import_module(module)
            return True
        except Exception:
            return False
    return shutil.which(parts[0]) is not None


def run_scanner(repo, category, scanner_def):
    """Assemble + run one scanner, returning (findings, meta).

    Assembles argv from the scanner_def's full-string ``command`` (shlex.split,
    python-interpreter rewrite like verify._split_command), spawns it via the
    single ``_run`` chokepoint, and routes output to the normalizer selected by
    the scanner_def's declared ``format``. A MISSING tool degrades gracefully:
    returns ``([], {tool_unavailable: True, ...})`` -- never a crash, never a
    fabricated finding -- consistent with the doctor philosophy.

    ``meta`` is a per-scanner record: {tool, format, command, category,
    returncode, tool_unavailable, output_tail}. ``category`` here is the
    scanner_def's declared category; cppcheck may override per-finding.
    """
    tool = scanner_def.get("tool", "?")
    fmt = scanner_def.get("format")
    command = scanner_def.get("command", "")
    meta = {
        "tool": tool,
        "format": fmt,
        "command": command,
        "category": category,
        "returncode": None,
        "tool_unavailable": False,
        "output_tail": "",
    }
    parser = _parser_for(fmt)
    if parser is None:
        meta["tool_unavailable"] = True
        meta["error"] = "no normalizer for format {!r}".format(fmt)
        return [], meta
    if not _tool_present(scanner_def):
        meta["tool_unavailable"] = True
        meta["error"] = "tool {!r} not reachable".format(tool)
        return [], meta
    argv = _split_command(command)
    try:
        returncode, stdout, stderr = _run(argv, cwd=repo)
    except (OSError, ValueError) as exc:
        # Defensive: a race between the presence check and spawn, or a bad argv.
        meta["tool_unavailable"] = True
        meta["error"] = "failed to run {!r}: {}".format(tool, exc)
        return [], meta
    meta["returncode"] = returncode
    meta["output_tail"] = _tail((stdout or "") + ("\n" + stderr if stderr else ""))
    # cppcheck SELF-CATEGORIZES: one --enable=all run emits security/quality/
    # perf findings and its own per-finding severity kind decides the category,
    # so we pass category=None (the normalizer ignores the declared category and
    # routes each finding by kind). Every other tool is single-category and uses
    # the declared category from the adapter.
    parser_category = None if fmt == "cppcheck" else category
    findings = parser(stdout, stderr, repo, parser_category)
    return findings, meta


# ---------------------------------------------------------------------------
# Thresholds + the composable gate.
# ---------------------------------------------------------------------------


def _counts_by_severity(findings):
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts


def _counts_by_category(findings):
    counts = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    return counts


def apply_thresholds(findings, thresholds):
    """Decide whether findings breach the configured gate, and WHY.

    This is the SINGLE shared gate function. Both the standalone reviewer and a
    future greenfield-generation gate (and the iterate-to-best fusion) call it
    with the SAME signature, so the gate is composable without rework.

    ``thresholds`` is a mapping that may declare:
      * ``fail_on_severity``: a severity level; any finding at that level or
        MORE severe fails the gate (e.g. ``high`` fails on high + critical).
      * ``max_findings``: a mapping of ``category -> max allowed count`` (e.g.
        ``{security: 0}`` fails when any security finding exists). A ``total``
        key, if present, caps the overall finding count.

    Returns a JSON-serializable gate dict::

        {
            "passed": bool,
            "fail_on_severity": <level or None>,
            "failing_severities": [severities at/above the threshold with >0],
            "failing_counts": {category: {limit, actual}, ...},
            "reasons": [human-readable strings],
        }

    An empty/None ``thresholds`` means "no gate configured" -> always passes.
    """
    thresholds = thresholds or {}
    sev_counts = _counts_by_severity(findings)
    cat_counts = _counts_by_category(findings)

    failing_severities = []
    reasons = []

    fail_on = thresholds.get("fail_on_severity")
    if fail_on:
        threshold_rank = severity_rank(fail_on)
        for sev in SEVERITIES:
            if severity_rank(sev) <= threshold_rank and sev_counts.get(sev, 0) > 0:
                failing_severities.append(sev)
        if failing_severities:
            reasons.append(
                "{} finding(s) at or above severity '{}' ({})".format(
                    sum(sev_counts[s] for s in failing_severities),
                    fail_on,
                    ", ".join(
                        "{}={}".format(s, sev_counts[s]) for s in failing_severities
                    ),
                )
            )

    failing_counts = {}
    max_findings = thresholds.get("max_findings") or {}
    for key, limit in max_findings.items():
        if key == "total":
            actual = len(findings)
        else:
            actual = cat_counts.get(key, 0)
        if actual > limit:
            failing_counts[key] = {"limit": limit, "actual": actual}
            reasons.append(
                "{} finding(s) exceed the max of {} for '{}'".format(
                    actual, limit, key
                )
            )

    passed = not failing_severities and not failing_counts
    return {
        "passed": passed,
        "fail_on_severity": fail_on,
        "failing_severities": failing_severities,
        "failing_counts": failing_counts,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Adapter / config loading + scan().
# ---------------------------------------------------------------------------


class StubAdapterError(RuntimeError):
    """Raised when the adapter declares no ``scanners:`` section.

    Mirrors verify.StubAdapterError: refusing to pretend an adapter can be
    scanned when it declares nothing to run is safer than silently reporting a
    clean bill of health.
    """


def default_adapter_path():
    """Return the Python adapter path shipped next to this script's package."""
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    return os.path.join(apia_dir, "adapters", "python.yml")


def load_adapter(adapter_path):
    """Load the stack adapter (YAML). PyYAML is the only optional dependency."""
    try:
        import yaml  # optional; only used to read the declarative adapter
    except ImportError:
        yaml = None
    if yaml is not None and adapter_path and os.path.isfile(adapter_path):
        with open(adapter_path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def _package_config_path():
    apia_dir = os.path.dirname(_SCRIPTS_DIR)
    return os.path.join(apia_dir, "config.yml")


def load_review_config(repo_path=None):
    """Return the ``review`` block from config.yml (repo-first), or {}.

    Resolution is REPO-FIRST (consistent with verify.load_cpp_overrides): the
    target repo's own ``.apia/config.yml`` wins, then the APIA package config is
    the fallback. The first config that declares a ``review`` block provides it.
    """
    try:
        import yaml
    except ImportError:
        return {}
    candidates = []
    if repo_path:
        candidates.append(os.path.join(repo_path, ".apia", "config.yml"))
    candidates.append(_package_config_path())
    for cfg in candidates:
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            continue
        block = data.get("review")
        if isinstance(block, dict):
            return block
    return {}


def _iter_scanner_defs(adapter, category="all"):
    """Yield (category, scanner_def) pairs from the adapter's scanners section.

    The ``scanners:`` section maps a category (security/quality/perf) to either
    a single scanner_def mapping or a LIST of them. ``category`` filters to one
    category or ``all``.
    """
    scanners = adapter.get("scanners") or {}
    for cat, defs in scanners.items():
        if category != "all" and cat != category:
            continue
        if isinstance(defs, dict):
            defs = [defs]
        for scanner_def in defs or []:
            if isinstance(scanner_def, dict):
                yield cat, scanner_def


def resolve_thresholds(review_config, options=None):
    """Return the effective thresholds mapping for a scan.

    Starts from ``review_config['thresholds']`` and lets an explicit
    ``options['fail_on_severity']`` (e.g. the CLI --fail-on-severity flag)
    override the ``fail_on_severity`` key. Returns a fresh dict (never mutates
    config).
    """
    thresholds = dict((review_config or {}).get("thresholds") or {})
    options = options or {}
    if options.get("fail_on_severity"):
        thresholds["fail_on_severity"] = options["fail_on_severity"]
    return thresholds


def _sort_findings(findings):
    """Sort findings by severity (most severe first) then category then file."""
    return sorted(
        findings,
        key=lambda f: (
            severity_rank(f["severity"]),
            f.get("category") or "",
            f.get("file") or "",
            f.get("line") or 0,
        ),
    )


def scan(repo, adapter, options=None):
    """Run all adapter scanners against ``repo`` and return a structured report.

    Iterates the adapter's ``scanners:`` section (optionally filtered to one
    category via ``options['category']``), aggregates all findings, applies the
    resolved thresholds, and returns a JSON-serializable report::

        {
            "repo": <basename>, "repo_path": <abs>, "stack": <adapter name>,
            "findings": [...sorted by severity then category],
            "counts_by_severity": {...}, "counts_by_category": {...},
            "threshold": {...effective thresholds...},
            "gate": {passed, failing_severities, failing_counts, reasons},
            "tools": <doctor()-style scanner presence>,
            "scanners": [...per-scanner meta...],
            "unavailable_tools": [...tool names that were missing...],
        }

    Refuses an adapter with no ``scanners:`` section (StubAdapterError). The
    ``gate`` block is the composable decision a standalone reviewer OR a future
    generation gate reads.
    """
    options = options or {}
    repo_path = os.path.abspath(repo)
    category = options.get("category", "all") or "all"

    scanner_defs = list(_iter_scanner_defs(adapter, category))
    if not adapter.get("scanners"):
        raise StubAdapterError(
            "adapter {!r} declares no 'scanners:' section. scan.py refuses to "
            "report a clean bill of health for an adapter with nothing to run. "
            "Add a scanners: section (see .apia/adapters/python.yml) or point "
            "--adapter at a complete adapter.".format(adapter.get("name", "unknown"))
        )

    all_findings = []
    scanner_metas = []
    unavailable = []
    for cat, scanner_def in scanner_defs:
        findings, meta = run_scanner(repo_path, cat, scanner_def)
        scanner_metas.append(meta)
        if meta.get("tool_unavailable"):
            unavailable.append(meta.get("tool"))
        all_findings.extend(findings)

    all_findings = _sort_findings(all_findings)

    review_config = load_review_config(repo_path)
    thresholds = resolve_thresholds(review_config, options)
    gate = apply_thresholds(all_findings, thresholds)

    tools_report = scanner_tools_report(adapter)

    return {
        "repo": os.path.basename(repo_path.rstrip(os.sep)),
        "repo_path": repo_path,
        "stack": adapter.get("name", "unknown"),
        "category": category,
        "findings": all_findings,
        "counts_by_severity": _counts_by_severity(all_findings),
        "counts_by_category": _counts_by_category(all_findings),
        "threshold": thresholds,
        "gate": gate,
        "tools": tools_report,
        "scanners": scanner_metas,
        "unavailable_tools": sorted(set(t for t in unavailable if t)),
    }


def scanner_tools_report(adapter):
    """Return a presence report for the adapter's scanner tools.

    Reuses the doctor PHILOSOPHY (presence as a non-gating advisory): for each
    declared scanner, report {tool, present, format, category}. Native tools are
    looked up on PATH; ``python -m`` tools are confirmed importable. This is the
    scan-time counterpart to ingest.doctor()'s tool table and surfaces the same
    "missing is a warning, never a hard failure" posture.
    """
    seen = {}
    for cat, scanner_def in _iter_scanner_defs(adapter, "all"):
        tool = scanner_def.get("tool", "?")
        if tool in seen:
            continue
        seen[tool] = {
            "tool": tool,
            "present": _tool_present(scanner_def),
            "format": scanner_def.get("format"),
            "category": cat,
        }
    present = [t for t in seen.values() if t["present"]]
    missing = [t for t in seen.values() if not t["present"]]
    return {
        "scanners": list(seen.values()),
        "present": [t["tool"] for t in present],
        "missing": [t["tool"] for t in missing],
    }


# ---------------------------------------------------------------------------
# Human-readable report.
# ---------------------------------------------------------------------------


def format_human(report):
    """Render a compact human-readable prioritized findings summary."""
    lines = []
    lines.append("APIA scan :: {} (stack: {})".format(report["repo"], report["stack"]))
    sev = report["counts_by_severity"]
    lines.append("  findings: {} total  [{}]".format(
        len(report["findings"]),
        ", ".join("{}={}".format(s, sev.get(s, 0)) for s in SEVERITIES if sev.get(s, 0))
        or "none",
    ))
    if report["unavailable_tools"]:
        lines.append("  unavailable tools (advisory, not a failure): {}".format(
            ", ".join(report["unavailable_tools"])))
    # Prioritized: findings already sorted most-severe-first.
    for f in report["findings"]:
        loc = "{}:{}".format(f.get("file") or "?", f.get("line") if f.get("line") else "")
        lines.append("  [{:<8}] {:<8} {} {} - {}".format(
            f["severity"], f["category"], f.get("rule") or "?", loc,
            (f.get("message") or "").splitlines()[0] if f.get("message") else "",
        ))
    gate = report["gate"]
    if gate["passed"]:
        lines.append("  => GATE PASSED")
    else:
        lines.append("  => GATE FAILED: {}".format("; ".join(gate["reasons"]) or "threshold breached"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "APIA off-model scanner engine (runs local scanners, normalizes "
            "findings, applies thresholds; no LLM/network calls)."
        )
    )
    parser.add_argument(
        "repo_path", nargs="?", default=None,
        help="Path to the repository to scan (optional for --doctor).",
    )
    parser.add_argument(
        "--adapter", default=None,
        help="Path to the stack adapter YAML (default: .apia/adapters/python.yml).",
    )
    parser.add_argument(
        "--category", default="all",
        choices=["security", "quality", "perf", "all"],
        help="Limit the scan to one finding category (default: all).",
    )
    parser.add_argument(
        "--fail-on-severity", dest="fail_on_severity", default=None,
        choices=list(SEVERITIES),
        help="Override the gate: fail when any finding is at this severity or "
             "more severe.",
    )
    parser.add_argument(
        "--doctor", action="store_true",
        help="Report scanner-tool presence (reusing the shared doctor() "
             "preflight) and exit. A missing scanner is a WARNING, never a "
             "hard failure.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the machine-readable JSON report (in addition to the human "
             "summary).",
    )
    args = parser.parse_args(argv)

    adapter_path = args.adapter or default_adapter_path()

    # --doctor: shared offline preflight for the scanner tools. Reuses
    # ingest.doctor() (which now carries the scanner/scanner_cpp categories) so
    # the preflight machinery is identical to verify.py's. Scanner tools are a
    # non-gating category: a missing scanner is reported but NEVER a hard
    # failure (exit 0), consistent with the doctor philosophy.
    if args.doctor:
        if ingest is None:
            parser.error("doctor preflight unavailable: could not import ingest.py")
        report = ingest.doctor(repo_path=(
            args.repo_path if args.repo_path and os.path.isdir(args.repo_path) else None
        ))
        print(ingest.format_doctor_report(report), end="")
        # Also show the adapter's own scanner table (which concrete scanners the
        # resolved adapter would run and whether they are reachable).
        adapter = load_adapter(adapter_path)
        if adapter.get("scanners"):
            tools = scanner_tools_report(adapter)
            print("\nAdapter scanners ({}):".format(adapter.get("name", "unknown")))
            for t in tools["scanners"]:
                mark = "ok " if t["present"] else "MISSING"
                print("  [{}] {:<12} ({}, {})".format(
                    mark, t["tool"], t["format"], t["category"]))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        # Scanner presence is advisory: --doctor always exits 0 here (scanner
        # tools are a non-gating category), while verify.py --doctor still gates
        # on required build/test tools.
        return 0

    if not args.repo_path:
        parser.error("repo_path is required (except with --doctor).")
    if not os.path.isdir(args.repo_path):
        parser.error("repo_path is not a directory: {}".format(args.repo_path))

    adapter = load_adapter(adapter_path)
    options = {
        "category": args.category,
        "fail_on_severity": args.fail_on_severity,
    }
    try:
        report = scan(args.repo_path, adapter, options)
    except StubAdapterError as exc:
        parser.error(str(exc))

    print(format_human(report))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    # Exit code reflects the gate decision (0 = gate passed).
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
