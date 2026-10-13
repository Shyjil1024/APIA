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


def build_failure_report(summary, eval_data=None):
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

    FAIL-LOUD: this function NEVER swallows a failure. If called on a summary with
    no failing phase AND no Layer-1 failure, it raises ValueError rather than
    returning an empty "all clear" report (an empty failure report would hide a
    real failure). Callers that want to test for green must check the verify
    summary / eval data directly, not call this.

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

    # Fail-loud: refuse to produce an empty "nothing wrong" failure report.
    any_failure = (
        failing_phases or golden_failures or invariant_violations
        or differential_regressions
    )
    regression_delta = summary.get("regression_delta")
    regressed = bool(regression_delta and regression_delta.get("regressed"))
    if not any_failure and not regressed:
        raise ValueError(
            "build_failure_report called on a summary with no failing phase and "
            "no Layer-1 failure; a failure report must describe a real failure "
            "(fail-loud). Check verify summary['ok'] before building a report."
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
    return report


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

    def to_dict(self):
        return {"iteration": self.iteration}


def evaluate(repo_path, adapter, options=None, state=None):
    """Drive ONE evaluate step: run a cycle, enforce the HARD cap, decide.

    An orchestrator (or the headless proof) calls this REPEATEDLY, once per model
    fix attempt, threading the same ``state`` (a ``LoopState``) through so the
    iteration counter persists. On each call it:

      1. (anti-circularity gate) if ``options['acceptance']`` describes NEW
         behavior that is NOT yet confirmed and the config gate is on, REFUSES to
         run the oracle and returns ``{status: 'needs_confirmation', ...}`` -- the
         loop must not grade a change against unconfirmed new-behavior criteria;
      2. increments the iteration counter and runs ``run_iteration_cycle``;
      3. if the cycle is GREEN, returns ``{status: 'green', ...}`` immediately
         (no further iterations);
      4. if the cycle NEEDS A FIX and the counter has reached the HARD
         ``max_iterations`` cap, returns
         ``{status: 'stop', reason: 'max_iterations_reached', ask: <question>,
            failure_report, ...}`` -- it NEVER silently applies a broken change
         and NEVER loops forever;
      5. otherwise returns ``{status: 'needs_fix', ...}`` carrying the structured
         failure report so the LIVE model can propose a fix before the next call.

    The counter is guaranteed never to exceed the cap: once ``iteration`` reaches
    ``max_iterations`` a red cycle yields ``stop`` instead of inviting another
    attempt. The actual fix between calls is a LIVE Copilot action, never done by
    this script.

    ``options`` extends run_iteration_cycle's options with:
      * ``max_iterations`` (int, optional): overrides the config cap (used by the
        CLI ``--max-iterations`` so a proof run can drive the cap deterministically);
      * ``acceptance`` (dict, optional): the propose_acceptance_criteria result,
        possibly confirmed, used by the anti-circularity gate.

    Returns a JSON-serializable dict (see statuses above). The ``state`` argument
    is a LoopState; when None, a fresh one is created and returned under
    ``state`` so a first call is self-contained.
    """
    options = options or {}
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

    # (2) Spend one iteration and run the deterministic cycle.
    state.iteration += 1
    cycle_options = dict(options)
    cycle_options["iteration"] = state.iteration
    cycle_options["remaining_iterations"] = max(cap - state.iteration, 0)
    cycle = run_iteration_cycle(repo_path, adapter, cycle_options)

    # (3) GREEN: stop immediately, no further iterations.
    if cycle["status"] == "green":
        return {
            "status": "green",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": max(cap - state.iteration, 0),
            "summary": cycle["summary"],
            "eval_data": cycle["eval_data"],
            "budget_advisory": cycle["budget_advisory"],
            "state": state.to_dict(),
        }

    # (4) RED at the HARD cap: STOP and ask a specific question. Never silently
    # apply a broken change, never loop forever.
    if state.iteration >= cap:
        fr = cycle["failure_report"]
        failing = [p.get("phase") for p in (fr.get("failing_phases") or [])]
        return {
            "status": "stop",
            "reason": "max_iterations_reached",
            "iteration": state.iteration,
            "max_iterations": cap,
            "remaining_iterations": 0,
            "failure_report": fr,
            "budget_advisory": cycle["budget_advisory"],
            "state": state.to_dict(),
            "ask": (
                "Reached the hard max_iterations cap ({}) without going green. "
                "Last failing: {}. The loop will NOT auto-apply a broken change. "
                "How would you like to proceed -- adjust the approach, relax a "
                "constraint, or stop here for manual review?".format(
                    cap, failing or "Layer-1 checks")
            ),
        }

    # (5) RED with budget remaining: hand back the structured failure for the
    # LIVE model to fix before the next evaluate() call.
    return {
        "status": "needs_fix",
        "iteration": state.iteration,
        "max_iterations": cap,
        "remaining_iterations": max(cap - state.iteration, 0),
        "failure_report": cycle["failure_report"],
        "budget_advisory": cycle["budget_advisory"],
        "state": state.to_dict(),
    }
