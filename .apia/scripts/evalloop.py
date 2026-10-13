#!/usr/bin/env python3
"""APIA Layer-2 self-correction loop MACHINERY (deterministic, off-model).

This module is the DETERMINISTIC controller for the Layer-2 iterative
self-correction loop. Layer 1 (``evaldata.py``) assembles grounded eval data;
Layer 2 (this module) runs the deterministic verifier against a change, turns a
failure into a STRUCTURED report a model can consume, enforces the HARD
``max_iterations`` cap and the soft ``token_budget.iterate`` advisory, and
decides green / stop / ask. Nothing here calls a model.

CRITICAL SEPARATION OF CONCERNS (read this before extending the module):

  The full loop is:
    1. assemble repo-derived eval data (Layer 1);
    2. for NEW behavior, propose acceptance criteria and surface them for ONE
       human confirmation (the anti-circularity gate) before the loop trusts
       them;
    3. GENERATE the change (Copilot) -- a LIVE model action, NOT this script;
    4. run the deterministic verifier (this script);
    5. on failure feed the EXACT structured failure report back to Copilot which
       proposes a FIX (a LIVE model action, NOT this script) and re-run;
    6. repeat until green OR the hard ``max_iterations`` cap, then STOP and ask a
       specific question.

  Steps 3 and 5 (the model generates/fixes code) and the human confirmation in
  step 2 are LIVE Copilot-SESSION actions. They are expressed in the
  prompts/skills/Tier-0 protocol (a later feature), NOT performed by this Python
  script. The script ONLY builds the deterministic machinery: assemble eval data,
  run the verifier, emit a structured failure a model can consume, enforce the
  cap + token-budget guard, and decide green/stop/ask. Nobody should ever expect
  this script to call a model or to confirm acceptance criteria on the user's
  behalf.

HARD CONSTRAINT (confirmed with the user; see docs/ARCHITECTURE.md sections 1-3):
this module performs ZERO network and ZERO LLM calls. It must NEVER import
``requests``, ``urllib``-for-network, ``openai``, ``anthropic`` or any other
LLM/network SDK, and must never reach a remote/model endpoint. The ONLY
subprocess it ever launches is the adapter's own LOCAL build/test/capture
command, and it only does so INDIRECTLY through ``verify`` / ``evaldata`` (which
run through the shared shell-free ``verify._run`` path). This module itself
spawns nothing; it is pure in-process orchestration over verify + evaldata.

    # assert: no network/LLM calls anywhere in this module -- the only
    # subprocess is the adapter's local command, spawned via verify/evaldata.

The dependency surface mirrors ingest.py/verify.py/evaldata.py: Python standard
library plus optional PyYAML (only ever used indirectly via verify.load_adapter
and the config reader). This module decides green/stop/ask; it makes NO model
calls.
"""

# NOTE: standard-library only. There is deliberately NO networking/LLM import
# here -- see the module docstring's HARD CONSTRAINT. The scripts dir is this
# file's directory; make it importable regardless of the caller's cwd (mirrors
# how verify.py puts the scripts dir on sys.path to import ingest, and how
# evaldata imports verify). These imports are LOCAL stdlib-only modules.
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    import verify  # noqa: E402  (reused for resolve_plan/run_phase/verify)
except ImportError:  # pragma: no cover - verify ships next to this module
    verify = None
try:
    import evaldata  # noqa: E402  (reused for assemble_eval_data / Layer-1)
except ImportError:  # pragma: no cover - evaldata ships next to this module
    evaldata = None
try:
    # scan.py is the SINGLE shared reviewer gate. We REUSE its apply_thresholds /
    # make_finding / load_review_config / resolve_thresholds -- we never
    # reimplement threshold logic. scan.py is itself a LOCAL stdlib-only module
    # that performs no network/LLM calls (enforced by its own guard test), so
    # importing it keeps evalloop's no-network/no-LLM invariant intact.
    import scan  # noqa: E402  (reused for the composable reviewer gate)
except ImportError:  # pragma: no cover - scan ships next to this module
    scan = None


# Fallback defaults when config.yml is unreadable. These mirror the committed
# .apia/config.yml so the loop still enforces a HARD cap even without PyYAML.
_DEFAULT_MAX_ITERATIONS = 3
_DEFAULT_ITERATE_BUDGET = 4000


# ---------------------------------------------------------------------------
# Config resolution (REPO-FIRST, mirroring verify._config_paths_for)
# ---------------------------------------------------------------------------

def _load_config(repo_path=None):
    """Return the resolved .apia/config.yml dict (REPO-FIRST), or {}.

    Resolution order mirrors verify._config_paths_for / ingest's
    confidence_threshold: the TARGET repo's own ``.apia/config.yml`` is consulted
    first, then the APIA PACKAGE config shipped next to this script. The first
    file that parses provides the config. Returns {} when PyYAML is unavailable
    or no config parses (the caller falls back to the hard-coded defaults so the
    cap is still enforced).
    """
    try:
        import yaml
    except ImportError:
        return {}
    paths = []
    if verify is not None:
        paths = verify._config_paths_for(repo_path)
    else:  # pragma: no cover - verify always ships alongside
        if repo_path:
            paths.append(os.path.join(repo_path, ".apia", "config.yml"))
        apia_dir = os.path.dirname(_SCRIPTS_DIR)
        paths.append(os.path.join(apia_dir, "config.yml"))
        paths = [p for p in paths if os.path.isfile(p)]
    for cfg in paths:
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_max_iterations(repo_path=None, override=None):
    """Return the HARD max_iterations cap (REPO-FIRST config, then default).

    An explicit ``override`` (e.g. the CLI ``--max-iterations``) wins so a proof
    run can drive the cap deterministically; otherwise the repo-first config
    value is used, falling back to _DEFAULT_MAX_ITERATIONS. The result is always
    a positive int so the loop is ALWAYS bounded -- a bad/zero/negative config
    value can never turn the cap off.
    """
    if override is not None:
        try:
            value = int(override)
        except (TypeError, ValueError):
            value = _DEFAULT_MAX_ITERATIONS
        return max(1, value)
    cfg = _load_config(repo_path)
    value = cfg.get("max_iterations", _DEFAULT_MAX_ITERATIONS)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_ITERATIONS
    return max(1, value)


def resolve_iterate_budget(repo_path=None):
    """Return the soft per-iteration token budget (token_budget.iterate).

    REPO-FIRST config resolution, falling back to _DEFAULT_ITERATE_BUDGET. This
    is an ADVISORY ceiling (consistent with how config.yml frames token_budget):
    exceeding it attaches a soft warning to the cycle result, never a hard stop.
    The HARD stop is max_iterations.
    """
    cfg = _load_config(repo_path)
    budget = (cfg.get("token_budget") or {}).get("iterate", _DEFAULT_ITERATE_BUDGET)
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = _DEFAULT_ITERATE_BUDGET
    return max(0, budget)


def resolve_acceptance_criteria_gate(repo_path=None):
    """Return the eval_data.acceptance_criteria_gate flag (default True).

    When true (the anti-circularity safeguard), acceptance criteria describing
    NEW behavior must be human-confirmed before evaluate() will treat them as an
    oracle. Defaults to True (fail safe) when the config is missing/unreadable.
    """
    cfg = _load_config(repo_path)
    gate = (cfg.get("eval_data") or {}).get("acceptance_criteria_gate", True)
    return bool(gate)


# ---------------------------------------------------------------------------
# Reviewer-gate resolution (REUSES scan.apply_thresholds; no threshold logic
# is reimplemented here) + the iterate-to-best knobs.
# ---------------------------------------------------------------------------

# Fallback iterate-to-best policy, mirroring the committed .apia/config.yml, so
# the fused decision still has a safe, deterministic "best" definition even when
# PyYAML/config is unavailable. ``best_so_far_tie_break`` picks between two
# iterations that rank equally on residual severity + failing-phase count:
#   * "fewest_findings": prefer the attempt with fewer total residual findings;
#   * "latest": prefer the later attempt (more fix context applied);
#   * "earliest": prefer the earlier attempt.
_DEFAULT_ITERATE_TO_BEST = {
    "best_so_far_tie_break": "fewest_findings",
}
_BEST_SO_FAR_TIE_BREAKS = ("fewest_findings", "latest", "earliest")


def resolve_review_thresholds(repo_path=None):
    """Return the effective review.thresholds mapping (REPO-FIRST), or {}.

    This DELEGATES to scan.load_review_config + scan.resolve_thresholds -- the
    SAME REPO-FIRST (repo config wins, package fallback) resolution the
    standalone reviewer and the generation gate already use. No threshold logic
    is reimplemented. Returns {} (an empty gate -> always passes) when scan or
    PyYAML is unavailable, so the fused decision degrades to today's tests-green
    behavior rather than crashing.
    """
    if scan is None:  # pragma: no cover - scan ships alongside
        return {}
    try:
        review_config = scan.load_review_config(repo_path)
        return scan.resolve_thresholds(review_config)
    except Exception:  # pragma: no cover - defensive; config read never crashes
        return {}


def resolve_iterate_to_best(repo_path=None):
    """Return the iterate-to-best knob block (eval_data.iterate_to_best).

    REPO-FIRST config resolution (same pattern as resolve_max_iterations). Reads
    the OPTIONAL ``eval_data.iterate_to_best`` sub-block from config.yml and
    merges it over the built-in defaults so each knob has a safe value even when
    the block (or PyYAML/config) is absent. Returns a fresh dict; never mutates
    config.
    """
    merged = dict(_DEFAULT_ITERATE_TO_BEST)
    cfg = _load_config(repo_path)
    block = (cfg.get("eval_data") or {}).get("iterate_to_best")
    if isinstance(block, dict):
        for key, val in block.items():
            merged[key] = val
    # Validate the tie-break policy; fall back to the default on anything odd so
    # best-so-far ranking is always deterministic.
    if merged.get("best_so_far_tie_break") not in _BEST_SO_FAR_TIE_BREAKS:
        merged["best_so_far_tie_break"] = \
            _DEFAULT_ITERATE_TO_BEST["best_so_far_tie_break"]
    return merged


# ---------------------------------------------------------------------------
# Token-budget advisory
# ---------------------------------------------------------------------------

def _estimate_tokens(payload):
    """Rough, deterministic token estimate for a JSON-serializable payload.

    Uses the compact JSON serialization length divided by 4 (the common
    ~4-chars-per-token heuristic). This is deliberately simple and local: it only
    needs to be a stable, monotonic proxy so the advisory fires when a
    failure-report/eval-data payload grows large, nudging the loop to stay
    token-cheap. It is NOT a model call and makes no network request.
    """
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return max(1, len(text) // 4)


def _budget_advisory(payload, budget):
    """Return a per-iteration token-budget advisory dict for ``payload``.

    Shape::

        {budget, estimated_tokens, within_budget: bool, warning: str|None}

    ``within_budget`` is True when the estimate is within the soft ceiling; when
    it is exceeded, ``warning`` carries a human-readable soft warning (never a
    hard failure -- the hard stop is max_iterations).
    """
    estimated = _estimate_tokens(payload)
    within = budget <= 0 or estimated <= budget
    warning = None
    if not within:
        warning = (
            "assembled iteration payload ~{} tokens exceeds the soft "
            "token_budget.iterate ceiling of {}; trim the grounding fed to the "
            "next fix attempt to stay token-cheap (advisory, not a hard stop)."
            .format(estimated, budget)
        )
    return {
        "budget": budget,
        "estimated_tokens": estimated,
        "within_budget": bool(within),
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Structured failure report (fail-loud, model-consumable, JSON-serializable)
# ---------------------------------------------------------------------------

def _layer1_failures(eval_data):
    """Extract the Layer-1 failures from an assembled eval-data dict.

    Returns ``(golden_failures, invariant_violations, differential_regressions)``
    where each is a compact list of JSON-serializable dicts carrying only what a
    model needs to propose a fix. ``eval_data`` may be None (no Layer-1 signal),
    in which case all three are empty.
    """
    golden_failures = []
    invariant_violations = []
    differential_regressions = []
    if not eval_data:
        return golden_failures, invariant_violations, differential_regressions

    # GOLDEN: surface only the artifacts that did NOT match, with the diff
    # summary (compact; never the full artifact bytes).
    for g in eval_data.get("golden") or []:
        if not g.get("match", True):
            golden_failures.append({
                "artifact": g.get("artifact"),
                "golden": g.get("golden"),
                "mode": g.get("mode"),
                "diff_summary": g.get("diff_summary"),
            })

    # INVARIANTS: surface only the checks that did NOT hold, with the detail.
    for inv in eval_data.get("invariants") or []:
        if not inv.get("held", True):
            invariant_violations.append({
                "check_id": inv.get("check_id"),
                "detail": inv.get("detail"),
            })

    # DIFFERENTIAL (artifact): surface the artifacts that CHANGED (a regression
    # unless the change intended them). The counts differential lives in the
    # verify summary's regression_delta and is folded in separately below.
    diff = eval_data.get("differential")
    if isinstance(diff, dict):
        for changed in diff.get("changed") or []:
            differential_regressions.append({
                "artifact": changed.get("artifact"),
                "mode": changed.get("mode"),
                "diff_summary": changed.get("diff_summary"),
            })
    return golden_failures, invariant_violations, differential_regressions


def build_failure_report(summary, eval_data=None, scan_gate=None, findings=None):
    """Turn a FAILED verify() summary + Layer-1 results into a structured report.

    Produces a COMPACT, JSON-serializable dict a model can consume to propose a
    fix WITHOUT re-running anything. It gathers:

      * ``failing_phases``: each failed phase with its command, returncode,
        parsed counts, and the already-captured stdout/stderr tails;
      * ``counts``: the per-phase parsed counts (passed/failed/error/...);
      * ``regression_delta``: the test-COUNTS differential from the verify
        summary (removed nodes / passed delta), when present;
      * ``golden_failures`` / ``invariant_violations`` /
        ``differential_regressions``: the Layer-1 failures (with diff summaries).

    REVIEWER-GATE FUSION (iterate-to-best): when a reviewer gate FAILED, pass its
    ``scan_gate`` block (the EXACT ``scan.apply_thresholds`` output -- passed /
    fail_on_severity / failing_severities / failing_counts / reasons) and the
    EXACT normalized ``findings`` (the ``scan.make_finding`` shape: severity,
    category, file, line, rule, message, source_tool). They are folded into the
    SAME report under two NEW keys -- ``reviewer_gate`` and ``scanner_findings``
    -- so the model fixing the change sees the same grounded findings the gate
    judged. All existing keys are preserved. A reviewer-gate failure counts as a
    REAL failure here, so an all-green verify + a failing gate still produces a
    report (never an empty "all clear").

    FAIL-LOUD: this function NEVER swallows a failure. If called on a summary with
    no failing phase AND no Layer-1 failure AND no failing reviewer gate, it
    raises ValueError rather than returning an empty "all clear" report (an empty
    failure report would hide a real failure). Callers that want to test for green
    must check the verify summary / eval data / gate directly, not call this.

    This builds the report ONLY; the FIX is a live model step that happens
    between cycles in a Copilot session, never inside this script.
    """
    summary = summary or {}
    phases = summary.get("phases") or []

    failing_phases = []
    for p in phases:
        if not p.get("passed", False):
            failing_phases.append({
                "phase": p.get("phase"),
                "command": p.get("command"),
                "returncode": p.get("returncode"),
                "counts": p.get("counts"),
                "stdout_tail": p.get("stdout_tail", ""),
                "stderr_tail": p.get("stderr_tail", ""),
            })

    golden_failures, invariant_violations, differential_regressions = \
        _layer1_failures(eval_data)

    # A reviewer gate counts as a REAL failure ONLY when it ran AND did not pass.
    # A None scan_gate means "no reviewer gate in play" (backward compatible);
    # a passing gate is not a failure.
    gate_failed = bool(scan_gate is not None and not scan_gate.get("passed", True))

    # Fail-loud: refuse to produce an empty "nothing wrong" failure report.
    any_failure = (
        failing_phases or golden_failures or invariant_violations
        or differential_regressions
    )
    regression_delta = summary.get("regression_delta")
    regressed = bool(regression_delta and regression_delta.get("regressed"))
    if not any_failure and not regressed and not gate_failed:
        raise ValueError(
            "build_failure_report called on a summary with no failing phase, no "
            "Layer-1 failure and no failing reviewer gate; a failure report must "
            "describe a real failure (fail-loud). Check verify summary['ok'] and "
            "the reviewer gate before building a report."
        )

    report = {
        "repo": summary.get("repo"),
        "adapter": summary.get("adapter"),
        "ok": False,
        "failing_phases": failing_phases,
        "counts": {p.get("phase"): p.get("counts") for p in phases},
        "regression_delta": regression_delta,
        "golden_failures": golden_failures,
        "invariant_violations": invariant_violations,
        "differential_regressions": differential_regressions,
    }
    # Reviewer-gate fusion: fold the EXACT gate block + the EXACT normalized
    # scanner findings into the SAME report under NEW keys. Only present when a
    # reviewer gate actually ran (scan_gate is not None), so the backward-compat
    # report shape is unchanged when no gate is in play.
    if scan_gate is not None:
        report["reviewer_gate"] = scan_gate
        report["scanner_findings"] = list(findings or [])
    return report


# ---------------------------------------------------------------------------
# Best-so-far scoring (deterministic; ranks an iteration by residual severity).
# ---------------------------------------------------------------------------

# Severity weights for scoring residual scanner findings (higher == worse). A
# missing severity scores 0. This mirrors scan.SEVERITIES ordering so a single
# critical finding always outranks any number of lows.
_SEVERITY_WEIGHT = {
    "critical": 10000,
    "high": 1000,
    "medium": 100,
    "low": 10,
    "info": 1,
}


def score_iteration(summary=None, findings=None):
    """Return a deterministic residual-severity SCORE for one iteration.

    LOWER is BETTER (fewer / lower-severity residuals). The score sums:
      * each residual scanner finding weighted by severity (a critical dwarfs
        any number of lows), and
      * the count of failing verify phases (a broken build/test is a hard
        residual), heavily weighted so a green-verify attempt always beats a
        red-verify one at the same finding level.

    Returns a dict carrying the scalar ``score`` plus the components used to
    tie-break and to render the residual summary::

        {score, finding_score, failing_phase_count, finding_count,
         severity_counts}

    Pure arithmetic over already-captured data; no re-run, no model, no network.
    """
    summary = summary or {}
    findings = findings or []

    severity_counts = {}
    finding_score = 0
    for f in findings:
        sev = f.get("severity", "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        finding_score += _SEVERITY_WEIGHT.get(sev, 1)

    phases = summary.get("phases") or []
    failing_phase_count = sum(1 for p in phases if not p.get("passed", False))

    # A failing verify phase is a hard residual: weight it above the heaviest
    # single finding so any green-verify attempt ranks ahead of a red one.
    score = finding_score + failing_phase_count * (_SEVERITY_WEIGHT["critical"] * 10)
    return {
        "score": score,
        "finding_score": finding_score,
        "failing_phase_count": failing_phase_count,
        "finding_count": len(findings),
        "severity_counts": severity_counts,
    }


def _is_better(candidate, incumbent, tie_break):
    """Return True when ``candidate`` is a strictly better best-so-far than
    ``incumbent`` under the configured ``tie_break`` policy.

    Both arguments are the per-iteration record carried in best-so-far (each has
    ``score`` and ``iteration`` plus the score components). A None incumbent is
    always beaten (first attempt). Primary key is the lower ``score``; ties are
    resolved by the policy (fewest findings / latest / earliest).
    """
    if incumbent is None:
        return True
    cand_score = candidate.get("score", {}).get("score", 0)
    inc_score = incumbent.get("score", {}).get("score", 0)
    if cand_score != inc_score:
        return cand_score < inc_score
    # Tie on the headline score: apply the configured policy.
    if tie_break == "fewest_findings":
        cand_n = candidate.get("score", {}).get("finding_count", 0)
        inc_n = incumbent.get("score", {}).get("finding_count", 0)
        if cand_n != inc_n:
            return cand_n < inc_n
        # Still tied -> keep the EARLIER attempt (stable, deterministic).
        return False
    if tie_break == "latest":
        return candidate.get("iteration", 0) > incumbent.get("iteration", 0)
    if tie_break == "earliest":
        return candidate.get("iteration", 0) < incumbent.get("iteration", 0)
    return False


def _residual_summary(best):
    """Render a precise, specific residual summary string from a best-so-far record.

    Describes WHICH iteration is best and WHAT residuals remain (failing verify
    phases + residual findings by severity) so a stop-and-ask presents the BEST
    attempt with concrete numbers, never a vague "it failed".
    """
    if not best:
        return "no iteration recorded"
    score = best.get("score") or {}
    parts = ["best attempt was iteration {}".format(best.get("iteration"))]
    fp = score.get("failing_phase_count", 0)
    if fp:
        parts.append("{} failing verify phase(s)".format(fp))
    sev_counts = score.get("severity_counts") or {}
    if sev_counts:
        # Render most-severe first for a readable residual summary.
        order = ("critical", "high", "medium", "low", "info")
        rendered = ["{}={}".format(s, sev_counts[s])
                    for s in order if sev_counts.get(s)]
        if rendered:
            parts.append("residual findings ({})".format(", ".join(rendered)))
    if not fp and not sev_counts:
        parts.append("no residual verify/scanner failures (gate-only or "
                     "Layer-1 residual)")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Layer-1 green/fail evaluation (deterministic, no model)
# ---------------------------------------------------------------------------

def _layer1_all_green(eval_data):
    """Return True when every PRESENT Layer-1 signal in ``eval_data`` is green.

    A missing signal (empty list / None) is not a failure -- it simply offers no
    constraint. Characterization is a RECORDING (it never "fails" by itself), so
    it is not graded here; golden/invariant/differential are the gradable Layer-1
    signals. Returns True when eval_data is None (no Layer-1 signal at all).
    """
    g, inv, diff = _layer1_failures(eval_data)
    return not (g or inv or diff)


# ---------------------------------------------------------------------------
# One deterministic verifier pass
# ---------------------------------------------------------------------------

def run_iteration_cycle(repo_path, adapter, options=None):
    """Run ONE deterministic verifier pass and report green / needs_fix.

    This is the deterministic CONTROLLER for a single loop turn. It:

      1. assembles Layer-1 eval data (``evaldata.assemble_eval_data``) when an
         ``adapter`` dict is supplied and Layer-1 is enabled;
      2. runs the existing verify() phases (build/baseline/regression/new) via
         ``verify.verify`` -- reusing resolve_plan/run_phase, NOT duplicating any
         phase logic;
      3. if every verify phase passed AND every present Layer-1 signal is green,
         returns ``{status: 'green', ...}``;
      4. otherwise builds the STRUCTURED failure report and returns
         ``{status: 'needs_fix', failure_report: <structured>, ...}``.

    The controller performs the DETERMINISTIC machinery ONLY. The actual code fix
    is a LIVE model step that happens BETWEEN cycles in a Copilot session; this
    function never calls a model and never edits code.

    ``options`` keys:
      * ``adapter_path`` / ``new_tests`` / ``baseline_only`` / ``baseline_file``
        / ``build_only`` / ``dry_run``: forwarded to verify.verify.
      * ``layer1`` (bool, default True): assemble Layer-1 eval data.
      * ``eval_options``: options dict forwarded to assemble_eval_data (e.g.
        ``invariant_context`` / ``differential``).
      * ``iteration`` (int): the current iteration number, echoed into the
        result (evaluate() supplies it).
      * ``remaining_iterations`` (int): echoed into the result for the model's
        awareness of how many attempts remain.

    Returns a JSON-serializable dict::

        {status: 'green'|'needs_fix', iteration, remaining_iterations,
         summary, eval_data, budget_advisory,
         failure_report (only when needs_fix)}
    """
    if verify is None:  # pragma: no cover - verify always ships alongside
        raise RuntimeError("verify module unavailable for the iteration cycle")
    options = options or {}
    repo_path = os.path.abspath(repo_path)

    # (1) Layer-1 eval data (grounded truth), when enabled and an adapter is
    # supplied. assemble_eval_data is PURE DATA ASSEMBLY; it makes no decision.
    #
    # IMPORTANT: a Layer-1 source only produces a MEANINGFUL signal when it has
    # the inputs it needs. Invariants need a ``produced_path`` to inspect; golden
    # mining needs a produced artifact matching the adapter glob; the differential
    # needs old/new dirs. When those inputs are absent the source has NO signal
    # for this cycle and must NOT be run as if it were a failure (an invariant
    # with nothing to inspect is "not applicable", not "violated"). So we enable a
    # source for grading only when its inputs are present, keeping the loop
    # grounded: it grades a change against REAL observable behavior, never against
    # a check it could not actually run.
    eval_data = None
    if options.get("layer1", True) and adapter and evaldata is not None:
        eval_options = dict(options.get("eval_options") or {})
        # Invariants are only graded when an invariant_context with a produced
        # artifact path was supplied; otherwise disable them for this cycle.
        inv_ctx = eval_options.get("invariant_context") or {}
        if not inv_ctx.get("produced_path"):
            eval_options.setdefault("invariants", False)
        # Golden is only graded when the adapter declares a golden tree AND that
        # tree actually exists in the repo. Without a committed golden tree there
        # is no golden oracle, so a produced artifact matching the glob (e.g. an
        # unrelated build-tree *.json) must NOT be graded as a "missing golden"
        # failure. mine_golden already skips when there is no block/dir; this
        # guards the common case where the glob matches but no golden tree exists.
        golden_block = (adapter or {}).get("golden") or {}
        golden_dir = golden_block.get("golden_dir")
        has_golden_tree = bool(
            golden_dir and os.path.isdir(os.path.join(repo_path, golden_dir))
        )
        if not has_golden_tree:
            eval_options.setdefault("golden", False)
        eval_data = evaldata.assemble_eval_data(repo_path, adapter, eval_options)

    # (2) Deterministic verifier phases (reuse verify.verify end to end).
    summary = verify.verify(
        repo_path,
        adapter_path=options.get("adapter_path"),
        new_tests=options.get("new_tests"),
        baseline_only=options.get("baseline_only", False),
        baseline_file=options.get("baseline_file"),
        build_only=options.get("build_only", False),
        dry_run=options.get("dry_run", False),
    )

    iteration = options.get("iteration")
    remaining = options.get("remaining_iterations")

    # (3)/(4) Decide green vs needs_fix. Green requires BOTH the verify phases
    # (summary['ok']) and every present Layer-1 signal.
    verify_green = bool(summary.get("ok"))
    layer1_green = _layer1_all_green(eval_data)

    result = {
        "iteration": iteration,
        "remaining_iterations": remaining,
        "summary": summary,
        "eval_data": eval_data,
    }

    if verify_green and layer1_green:
        result["status"] = "green"
        result["failure_report"] = None
    else:
        result["status"] = "needs_fix"
        result["failure_report"] = build_failure_report(summary, eval_data)

    # Token-budget advisory: estimate the size of the payload a model would carry
    # into the next fix attempt (the failure report + the grounding eval data).
    budget = resolve_iterate_budget(repo_path)
    advisory_payload = {
        "failure_report": result.get("failure_report"),
        "eval_data": eval_data,
    }
    result["budget_advisory"] = _budget_advisory(advisory_payload, budget)
    return result


# ---------------------------------------------------------------------------
# Acceptance-criteria confirmation gate (anti-circularity)
# ---------------------------------------------------------------------------

def propose_acceptance_criteria(change_spec, eval_data=None):
    """Assemble APIA's best-grounded proposed acceptance criteria for NEW behavior.

    For NEW behavior (behavior the code does NOT yet exhibit), the loop must not
    grade a change against the agent's own restatement of the request -- that is
    circular ("pass by agreeing with myself"). This function assembles the
    best-grounded candidate criteria, derived from Layer-1 facts WHERE POSSIBLE
    (e.g. the invariant ids the adapter already declares, the golden artifacts it
    already compares), plus any criteria the caller states in ``change_spec``.

    It returns them flagged ``needs_human_confirmation: True`` so the loop will
    NOT trust them until a human confirms. The SCRIPT only SURFACES and HOLDS the
    criteria; the human confirmation and the model's own proposal are the
    LIVE-SESSION parts (documented and wired in the prompts/skills/Tier-0 layer).
    This function NEVER confirms on the user's behalf.

    ``change_spec`` is a dict describing the requested change; recognized keys:
      * ``is_new_behavior`` (bool): whether the change introduces NEW behavior
        (defaults True; the gate is about new behavior).
      * ``criteria`` (list[str]): human/author-stated acceptance criteria to
        carry through for confirmation.
      * ``summary`` (str): a short description, echoed for context.

    Returns::

        {needs_human_confirmation: bool, is_new_behavior: bool, confirmed: False,
         criteria: [{source, statement, grounded_in}], summary}

    ``confirmed`` is ALWAYS False here: confirmation is a live human action, not
    something this script can set. evaluate() refuses to treat the criteria as an
    oracle until a caller passes an explicitly-confirmed structure (see
    ``evaluate`` and ``acceptance_criteria_confirmed``).
    """
    change_spec = change_spec or {}
    is_new = change_spec.get("is_new_behavior", True)

    criteria = []
    # Author/human-stated criteria carried through for confirmation.
    for stmt in change_spec.get("criteria") or []:
        criteria.append({
            "source": "stated",
            "statement": stmt,
            "grounded_in": None,
        })

    # Best-grounded candidates derived from Layer-1 facts the adapter declares.
    if eval_data:
        for inv in eval_data.get("invariants") or []:
            cid = inv.get("check_id")
            if cid:
                criteria.append({
                    "source": "invariant",
                    "statement": "the invariant '{}' must hold on the produced "
                                 "artifact".format(cid),
                    "grounded_in": "adapter invariant check",
                })
        for g in eval_data.get("golden") or []:
            art = g.get("artifact")
            if art:
                criteria.append({
                    "source": "golden",
                    "statement": "the produced artifact '{}' must match its "
                                 "committed golden".format(art),
                    "grounded_in": "golden-file comparison",
                })

    return {
        # New behavior ALWAYS needs human confirmation (anti-circularity). For a
        # non-new-behavior change the loop already has grounded oracles (existing
        # tests / characterization), so confirmation is not required there.
        "needs_human_confirmation": bool(is_new),
        "is_new_behavior": bool(is_new),
        # confirmed is a LIVE human action; the script can never set it True.
        "confirmed": False,
        "criteria": criteria,
        "summary": change_spec.get("summary"),
    }


def acceptance_criteria_confirmed(acceptance):
    """Return True only when ``acceptance`` is an explicitly-confirmed oracle.

    This is the gate predicate evaluate() consults. ``acceptance`` is either None
    (no new-behavior criteria in play -> nothing to gate) or the dict produced by
    propose_acceptance_criteria, possibly with ``confirmed: True`` set by a caller
    AFTER a live human confirmation. The function returns True when the criteria
    are safe to treat as an oracle:

      * None acceptance -> True (no new-behavior oracle to gate);
      * acceptance that does not describe new behavior -> True (grounded oracles
        already exist);
      * acceptance describing new behavior -> True ONLY when ``confirmed`` is
        explicitly True (the live human confirmation happened).

    The SCRIPT never flips ``confirmed`` itself; a caller sets it after the
    live-session human confirmation.
    """
    if not acceptance:
        return True
    if not acceptance.get("is_new_behavior", False):
        return True
    return bool(acceptance.get("confirmed", False))


# ---------------------------------------------------------------------------
# evaluate(): stateful driver with the HARD cap + gate + budget advisory
# ---------------------------------------------------------------------------

class LoopState(object):
    """Tiny mutable iteration counter the orchestrator/proof threads through.

    evaluate() is called repeatedly (once per model fix attempt). The caller owns
    one LoopState and passes it back in each call so the counter persists across
    calls WITHOUT this module holding any global/hidden state. The counter can
    NEVER exceed the resolved max_iterations cap -- that is the hard stop.
    """

    def __init__(self):
        self.iteration = 0
        # best_so_far holds the BEST iteration record seen across attempts (the
        # fewest/lowest-severity residuals), so if the loop cannot fully converge
        # it can report and offer the BEST attempt rather than the last. None
        # until the first quality-gated cycle runs. Existing callers that never
        # touch it keep working unchanged.
        self.best_so_far = None

    def to_dict(self):
        return {"iteration": self.iteration, "best_so_far": self.best_so_far}


def _resolve_gate_findings(repo_path, adapter, options):
    """Return (findings, scan_gate, thresholds) for the reviewer gate, or Nones.

    ITERATE-TO-BEST reviewer gate. The reviewer gate is in play ONLY when
    scanner findings are available for THIS cycle. The findings come from the
    LIVE Copilot session's scan output (or a scripted worked-example) supplied in
    ``options['findings']``. When no findings are supplied the gate degrades to
    "not in play" (None) and the fused decision falls back to today's
    tests-green behavior (backward compatible), because this module performs ZERO
    network/LLM/scan subprocess work of its own -- it only APPLIES thresholds to
    findings the live session already produced.

    When findings ARE supplied, the gate REUSES scan.apply_thresholds against the
    SAME review.thresholds (REPO-FIRST, package fallback) that the standalone
    reviewer and the generation gate use. ``options['thresholds']`` may override
    the resolved thresholds (e.g. a proof driving a specific bar).

    Returns ``(None, None, None)`` when no reviewer gate is in play.
    """
    if "findings" not in options:
        return None, None, None
    findings = list(options.get("findings") or [])
    thresholds = options.get("thresholds")
    if thresholds is None:
        thresholds = resolve_review_thresholds(repo_path)
    if scan is None:  # pragma: no cover - scan ships alongside
        return findings, {"passed": True, "reasons": [],
                          "failing_severities": [], "failing_counts": {},
                          "fail_on_severity": None}, thresholds
    scan_gate = scan.apply_thresholds(findings, thresholds)
    return findings, scan_gate, thresholds


def evaluate_quality_gated(repo_path, adapter, options=None, state=None):
    """The SINGLE shared fused quality-gated acceptance decision (iterate-to-best).

    This is the ONE function the three callers (verify.py --iterate,
    scaffold.decide_generation, orchestrate.decide_task) delegate to so that
    "best" means the SAME thing everywhere. It fuses:

      (a) the deterministic verify+Layer-1 cycle (``run_iteration_cycle``:
          tests/build + regression guard + new tests + Layer-1 signals), AND
      (b) the Phase-2 REVIEWER gate -- ``scan.apply_thresholds`` applied to the
          EXACT normalized scanner findings supplied in ``options['findings']``
          against the SAME ``review.thresholds`` the standalone reviewer uses.

    ACCEPTANCE ("best reached") = verify green AND Layer-1 green AND the reviewer
    gate PASSED. Only then is the status ``green``. On a FAILING reviewer gate
    (even with an all-green verify) the EXACT findings + the gate block are
    folded into the EXISTING ``build_failure_report`` (new keys ``reviewer_gate``
    / ``scanner_findings``; existing keys preserved; fail-loud contract intact)
    and the status is ``needs_fix`` so the LIVE Copilot session can fix them.

    BEST-SO-FAR: every cycle is scored by residual severity + failing-phase count
    (``score_iteration``) and the BEST attempt is tracked through
    ``state.best_so_far`` (tie-broken by the configured policy). On hitting the
    HARD ``max_iterations`` cap the decision STOPS and asks, carrying the BEST
    iteration reached (not necessarily the last) plus a specific residual summary
    -- it NEVER loops forever and NEVER silently accepts a sub-threshold result.

    When NO reviewer gate is in play (no ``options['findings']``), the fused
    decision DEGRADES GRACEFULLY to the current tests-green behavior, so existing
    callers/tests are unaffected (``evaluate`` delegates straight here).

    Statuses (identical vocabulary to the historical ``evaluate``):
    ``green`` | ``needs_fix`` | ``stop`` | ``needs_confirmation``.

    ``options`` extends run_iteration_cycle's options with:
      * ``max_iterations`` (int): overrides the config cap (CLI --max-iterations);
      * ``acceptance`` (dict): the propose_acceptance_criteria result for the
        anti-circularity gate;
      * ``findings`` (list): the LIVE scanner findings (scan.make_finding shape)
        the reviewer gate judges. Its PRESENCE turns the reviewer gate on;
      * ``thresholds`` (dict): override the resolved review.thresholds.

    This module performs ZERO network/LLM calls and spawns no subprocess of its
    own; the per-iteration code FIX is a LIVE Copilot action between calls.
    """
    options = options or {}
    # repo_path may be None when a caller (e.g. scaffold.decide_generation on a
    # not-yet-materialized target) only needs the package-config fallback; the
    # config resolvers already tolerate None, so only abspath a real path.
    if repo_path is not None:
        repo_path = os.path.abspath(repo_path)
    if state is None:
        state = LoopState()

    cap = resolve_max_iterations(repo_path, override=options.get("max_iterations"))

    # (1) ANTI-CIRCULARITY GATE: for NEW behavior, refuse to treat the acceptance
    # criteria as an oracle until a human has confirmed them. The SCRIPT only
    # holds the criteria; the confirmation is a live-session human action.
    acceptance = options.get("acceptance")
    gate_on = resolve_acceptance_criteria_gate(repo_path)
    if gate_on and acceptance and not acceptance_criteria_confirmed(acceptance):
        return {
            "status": "needs_confirmation",
            "reason": "acceptance_criteria_unconfirmed",
            "iteration": state.iteration,
            "max_iterations": cap,
            "acceptance": acceptance,
            "ask": (
                "These acceptance criteria describe NEW behavior the code does "
                "not yet exhibit. Confirm them before APIA grades the change "
                "against them (anti-circularity gate): {}".format(
                    [c.get("statement") for c in acceptance.get("criteria") or []]
                )
            ),
        }

    # (2) Spend one iteration and obtain the deterministic verify+Layer-1 cycle.
    # A caller that ALREADY ran the deterministic cycle in a live session (e.g.
    # scaffold.decide_generation, which receives a pre-computed generation
    # verify_result) may supply it via ``options['cycle']`` so this shared
    # function does NOT re-run verify -- it still owns the fused acceptance / cap
    # / best-so-far / failure-report decision so "best" means the same thing. The
    # cycle dict must carry the run_iteration_cycle shape (status, summary,
    # eval_data, failure_report, budget_advisory).
    state.iteration += 1
    cycle = options.get("cycle")
    if cycle is None:
        cycle_options = dict(options)
        cycle_options["iteration"] = state.iteration
        cycle_options["remaining_iterations"] = max(cap - state.iteration, 0)
        cycle = run_iteration_cycle(repo_path, adapter, cycle_options)

    # (3) REVIEWER GATE: apply the SAME review.thresholds to the EXACT scanner
    # findings (when supplied). No findings -> gate not in play -> degrade to the
    # tests-green behavior.
    findings, scan_gate, thresholds = _resolve_gate_findings(
        repo_path, adapter, options)
    gate_in_play = scan_gate is not None
    gate_passed = (not gate_in_play) or bool(scan_gate.get("passed", True))

    verify_green = cycle["status"] == "green"

    # (4) BEST-SO-FAR: score THIS iteration by residual severity + failing-phase
    # count and keep the best attempt across iterations (tie-broken by policy).
    tie_break = resolve_iterate_to_best(repo_path).get("best_so_far_tie_break")
    this_record = {
        "iteration": state.iteration,
        "score": score_iteration(cycle.get("summary"), findings),
        "verify_green": verify_green,
        "gate_passed": gate_passed,
        "gate_in_play": gate_in_play,
    }
    if _is_better(this_record, state.best_so_far, tie_break):
        state.best_so_far = this_record

    # Build the fused failure report when NOT accepted. When the reviewer gate is
    # in play, fold the EXACT gate block + findings into the EXISTING report
    # shape (a green-verify + failing-gate still yields a report because
    # scan_gate is failing). When the gate is NOT in play, reuse the report the
    # cycle already built so this stays backward-compatible with the historical
    # tests-green path (the cycle's run_iteration_cycle already assembled it).
    def _fused_report():
        prebuilt = cycle.get("failure_report")
        if gate_in_play:
            # If the cycle already carries a structured failure report (a caller
            # that ran the deterministic cycle in a live session, e.g.
            # scaffold.decide_generation), extend THAT report with the reviewer
            # gate + findings rather than rebuilding from a phase-less summary
            # (which would trip the fail-loud guard). Otherwise build fresh.
            if prebuilt is not None:
                fused = dict(prebuilt)
                fused["reviewer_gate"] = scan_gate
                fused["scanner_findings"] = list(findings or [])
                return fused
            return build_failure_report(
                cycle.get("summary"), cycle.get("eval_data"),
                scan_gate=scan_gate, findings=findings,
            )
        return prebuilt

    # (5) ACCEPTED ("best reached"): verify green AND Layer-1 green AND gate pass.
    if verify_green and gate_passed:
        return {
            "status": "green",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": max(cap - state.iteration, 0),
            "summary": cycle["summary"],
            "eval_data": cycle["eval_data"],
            "reviewer_gate": scan_gate,
            "budget_advisory": cycle["budget_advisory"],
            "best_so_far": state.best_so_far,
            "state": state.to_dict(),
        }

    # (6) NOT ACCEPTED at the HARD cap: STOP and ask, carrying the BEST attempt.
    if state.iteration >= cap:
        fr = _fused_report()
        failing = [p.get("phase") for p in (fr.get("failing_phases") or [])]
        gate_reasons = (scan_gate or {}).get("reasons") if gate_in_play else None
        return {
            "status": "stop",
            "reason": "max_iterations_reached",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": 0,
            "failure_report": fr,
            "reviewer_gate": scan_gate,
            "budget_advisory": cycle["budget_advisory"],
            "best_so_far": state.best_so_far,
            "state": state.to_dict(),
            "ask": (
                "Reached the hard max_iterations cap ({}) without meeting the "
                "quality bar (green tests + reviewer gate). The BEST attempt so "
                "far: {}. Last failing verify phases: {}; reviewer-gate reasons: "
                "{}. The loop will NOT auto-apply a sub-threshold change. How "
                "would you like to proceed -- adjust the approach, relax a "
                "threshold, or stop here for manual review?".format(
                    cap, _residual_summary(state.best_so_far),
                    failing or "none", gate_reasons or "n/a")
            ),
        }

    # (7) NOT ACCEPTED with budget remaining: hand back the fused structured
    # failure for the LIVE model to fix before the next call.
    return {
        "status": "needs_fix",
        "iteration": state.iteration,
        "max_iterations": cap,
        "remaining_iterations": max(cap - state.iteration, 0),
        "failure_report": _fused_report(),
        "reviewer_gate": scan_gate,
        "budget_advisory": cycle["budget_advisory"],
        "best_so_far": state.best_so_far,
        "state": state.to_dict(),
    }


def evaluate(repo_path, adapter, options=None, state=None):
    """Drive ONE evaluate step: run a cycle, enforce the HARD cap, decide.

    This is now a THIN delegate to the shared fused decision
    ``evaluate_quality_gated`` so the standalone loop, the generation gate and
    the orchestrator all share ONE definition of "best". When no reviewer gate is
    in play (no ``options['findings']``), the fused decision degrades to the
    historical tests-green behavior, so existing callers/tests are unchanged.

    An orchestrator (or the headless proof) calls this REPEATEDLY, once per model
    fix attempt, threading the same ``state`` (a ``LoopState``) through so the
    iteration counter persists. On each call it:

      1. (anti-circularity gate) if ``options['acceptance']`` describes NEW
         behavior that is NOT yet confirmed and the config gate is on, REFUSES to
         run the oracle and returns ``{status: 'needs_confirmation', ...}``;
      2. increments the iteration counter and runs ``run_iteration_cycle``;
      3. if the cycle is GREEN (and any reviewer gate passed), returns
         ``{status: 'green', ...}`` immediately;
      4. if NOT accepted and the counter has reached the HARD ``max_iterations``
         cap, returns ``{status: 'stop', reason: 'max_iterations_reached',
         ask, failure_report, best_so_far, ...}`` -- never silently applying a
         broken change, never looping forever, carrying the BEST attempt;
      5. otherwise returns ``{status: 'needs_fix', ...}`` carrying the structured
         failure report so the LIVE model can propose a fix before the next call.

    The counter is guaranteed never to exceed the cap. The actual fix between
    calls is a LIVE Copilot action, never done by this script.

    ``options`` extends run_iteration_cycle's options with ``max_iterations``,
    ``acceptance`` and (optionally) the reviewer-gate ``findings`` / ``thresholds``
    (see ``evaluate_quality_gated``). Returns a JSON-serializable dict; the
    ``state`` argument is a LoopState created fresh (and returned under ``state``)
    when None.
    """
    return evaluate_quality_gated(repo_path, adapter, options=options,
                                  state=state)
