"""Tests for the authored Copilot customization artifacts (FEAT-003).

These parse the YAML frontmatter of every SKILL.md, *.agent.md, and *.prompt.md under
.github/ and assert the structural rules that make the artifacts actually load:

- Every SKILL.md frontmatter `name` equals its parent directory name and matches
  ^[a-z0-9-]{1,64}$ (or the skill silently fails to load in the Copilot host).
- `description` is present and <= 1024 chars.
- Frontmatter is valid YAML.
- Heavy skills (apia-ingest, apia-test-authoring, and the reviewer scan skills) use
  `context: fork`.
- Custom agents declare name/description/tools and handoffs with send: false.
- The read-only agents (apia-planner, apia-reviewer) declare no edit/terminal tools.
- No authored artifact instructs calling an external LLM API.

PyYAML is required (already installed in .venv).
"""

import os
import re

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
GITHUB_DIR = os.path.join(REPO_ROOT, ".github")
SKILLS_DIR = os.path.join(GITHUB_DIR, "skills")
AGENTS_DIR = os.path.join(GITHUB_DIR, "agents")
PROMPTS_DIR = os.path.join(GITHUB_DIR, "prompts")

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

FORK_SKILLS = {
    "apia-ingest",
    "apia-test-authoring",
    # Reviewer scan skills run heavy scanners -> forked context for token isolation.
    "apia-security-scan",
    "apia-quality-scan",
    "apia-perf-scan",
    "apia-limitations",
    # Phase-4 orchestration: graph planning is heavy reasoning -> forked context.
    "apia-orchestrate",
}
EXPECTED_SKILLS = {
    "apia-ingest",
    "apia-feature-loop",
    "apia-test-authoring",
    # FEAT-003 reviewer skills.
    "apia-security-scan",
    "apia-quality-scan",
    "apia-perf-scan",
    "apia-limitations",
    # Phase-4 orchestration skill.
    "apia-orchestrate",
}
EXPECTED_AGENTS = {
    "apia-planner",
    "apia-implementer",
    "apia-tester",
    "apia-reviewer",
    # Phase-4 read-only conductor agent.
    "apia-orchestrator",
}
EXPECTED_PROMPTS = {
    "apia-ingest",
    "apia-feature",
    "apia-clarify",
    "apia-test",
    "apia-apply",
    "apia-iterate",
    "apia-review",
    # Phase-4 orchestration prompt (single-agent, VS 2022 / JetBrains compatible).
    "apia-run",
}


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _frontmatter(path):
    """Return (parsed_frontmatter_dict, raw_text). Frontmatter must be valid YAML."""
    text = _read(path)
    match = FRONTMATTER_RE.match(text)
    assert match, "no YAML frontmatter block in {}".format(path)
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), "frontmatter is not a mapping in {}".format(path)
    return data, text


def _find(root, filename=None, suffix=None):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if filename is not None and fn == filename:
                found.append(os.path.join(dirpath, fn))
            elif suffix is not None and fn.endswith(suffix):
                found.append(os.path.join(dirpath, fn))
    return found


def _skill_files():
    return _find(SKILLS_DIR, filename="SKILL.md")


def _agent_files():
    return _find(AGENTS_DIR, suffix=".agent.md")


def _prompt_files():
    return _find(PROMPTS_DIR, suffix=".prompt.md")


# --- structural existence ---------------------------------------------------


def test_tier0_instructions_exist_and_short():
    path = os.path.join(GITHUB_DIR, "copilot-instructions.md")
    assert os.path.isfile(path), "Tier-0 copilot-instructions.md missing"
    text = _read(path)
    # Sent on every request -> must stay small. 6500 keeps real headroom over the
    # current content while still bounding the per-request token cost.
    assert len(text) < 6500, "copilot-instructions.md should stay small (token cost)"
    lowered = text.lower()
    for element in ("ingest", "clarify", "modify", "test", "apply", "gate",
                    "confidence_threshold", "token", "python", "external"):
        assert element in lowered, "Tier-0 file missing required element: {}".format(element)


def test_all_expected_skills_present():
    names = {os.path.basename(os.path.dirname(p)) for p in _skill_files()}
    assert EXPECTED_SKILLS <= names, "missing skills: {}".format(EXPECTED_SKILLS - names)


def test_all_expected_agents_present():
    names = {os.path.basename(p)[: -len(".agent.md")] for p in _agent_files()}
    assert EXPECTED_AGENTS <= names, "missing agents: {}".format(EXPECTED_AGENTS - names)


def test_all_expected_prompts_present():
    names = {os.path.basename(p)[: -len(".prompt.md")] for p in _prompt_files()}
    assert EXPECTED_PROMPTS <= names, "missing prompts: {}".format(EXPECTED_PROMPTS - names)


# --- SKILL.md rules ---------------------------------------------------------


def test_skill_name_matches_directory_and_pattern():
    skills = _skill_files()
    assert skills, "no SKILL.md files found"
    for path in skills:
        data, _ = _frontmatter(path)
        dir_name = os.path.basename(os.path.dirname(path))
        name = data.get("name")
        assert name == dir_name, (
            "SKILL.md name '{}' != directory '{}' ({})".format(name, dir_name, path)
        )
        assert NAME_RE.match(str(name)), (
            "SKILL.md name '{}' violates ^[a-z0-9-]{{1,64}}$ ({})".format(name, path)
        )


def test_skill_description_present_and_bounded():
    for path in _skill_files():
        data, _ = _frontmatter(path)
        desc = data.get("description")
        assert isinstance(desc, str) and desc.strip(), (
            "SKILL.md description missing ({})".format(path)
        )
        assert len(desc) <= 1024, (
            "SKILL.md description exceeds 1024 chars ({})".format(path)
        )


def test_fork_skills_use_context_fork():
    for path in _skill_files():
        data, _ = _frontmatter(path)
        name = data.get("name")
        if name in FORK_SKILLS:
            assert data.get("context") == "fork", (
                "skill '{}' must use context: fork ({})".format(name, path)
            )


# --- agent rules ------------------------------------------------------------


def test_agents_have_name_description_tools_and_handoffs():
    agents = _agent_files()
    assert agents, "no *.agent.md files found"
    for path in agents:
        data, _ = _frontmatter(path)
        name = data.get("name")
        assert NAME_RE.match(str(name)), (
            "agent name '{}' violates ^[a-z0-9-]{{1,64}}$ ({})".format(name, path)
        )
        assert isinstance(data.get("description"), str) and data["description"].strip()
        assert len(data["description"]) <= 1024
        tools = data.get("tools")
        assert isinstance(tools, list) and tools, (
            "agent must declare a non-empty tools array ({})".format(path)
        )
        handoffs = data.get("handoffs")
        assert isinstance(handoffs, list) and handoffs, (
            "agent must declare handoffs that materialize approval gates ({})".format(path)
        )
        for ho in handoffs:
            assert {"label", "agent", "prompt"} <= set(ho.keys()), (
                "handoff missing required keys ({})".format(path)
            )
            # Approval gate: do not auto-send; the human reviews first.
            assert ho.get("send") is False, (
                "handoff must set send: false so the human reviews ({})".format(path)
            )


def test_planner_is_read_only():
    path = os.path.join(AGENTS_DIR, "apia-planner.agent.md")
    data, _ = _frontmatter(path)
    tools = " ".join(data.get("tools", [])).lower()
    assert "edit" not in tools, "planner must be read-only (no edit tools)"


def test_reviewer_is_read_only():
    """FEAT-003: the reviewer only consumes scan.py output + reads code; it must have
    no edit/terminal tools so it can never write a change itself."""
    path = os.path.join(AGENTS_DIR, "apia-reviewer.agent.md")
    data, _ = _frontmatter(path)
    tools = " ".join(data.get("tools", [])).lower()
    assert "edit" not in tools, "reviewer must be read-only (no edit tools)"
    assert "terminal" not in tools, "reviewer must be read-only (no run/terminal tools)"


def test_orchestrator_is_read_only():
    """FEAT-002: the orchestrator is a read-only CONDUCTOR; it plans + sequences and
    hands off to the editing agents, so it must have no edit/terminal tools and can
    never write a change itself (mirrors test_reviewer_is_read_only)."""
    path = os.path.join(AGENTS_DIR, "apia-orchestrator.agent.md")
    data, _ = _frontmatter(path)
    tools = " ".join(data.get("tools", [])).lower()
    assert "edit" not in tools, "orchestrator must be read-only (no edit tools)"
    assert "terminal" not in tools, "orchestrator must be read-only (no run/terminal tools)"


def test_run_prompt_matches_read_only_orchestrator():
    """FEAT-002: if the run prompt declares an agent it must drive the read-only
    apia-orchestrator and must not grant it edit/terminal tools (mirrors the review
    prompt guard)."""
    path = os.path.join(PROMPTS_DIR, "apia-run.prompt.md")
    data, _ = _frontmatter(path)
    agent = data.get("agent")
    if agent is not None:
        assert agent == "apia-orchestrator", (
            "run prompt is expected to drive the read-only orchestrator"
        )
    joined = " ".join(str(t).lower() for t in data.get("tools", []))
    assert "edit" not in joined, (
        "run prompt must not grant edit tools to the read-only orchestrator"
    )
    assert "terminal" not in joined, (
        "run prompt must not grant run/terminal to the read-only orchestrator"
    )


def test_review_prompt_matches_read_only_reviewer():
    """The review prompt must drive the read-only apia-reviewer and must not grant it
    edit/terminal tools (mirrors the ingest-prompt guard for the planner)."""
    path = os.path.join(PROMPTS_DIR, "apia-review.prompt.md")
    data, _ = _frontmatter(path)
    assert data.get("agent") == "apia-reviewer", (
        "review prompt is expected to drive the read-only reviewer"
    )
    joined = " ".join(str(t).lower() for t in data.get("tools", []))
    assert "edit" not in joined, (
        "review prompt must not grant edit tools to the read-only reviewer"
    )
    assert "terminal" not in joined, (
        "review prompt must not grant run/terminal to the read-only reviewer"
    )


def test_ingest_prompt_does_not_loosen_read_only_planner():
    """Review issue 8: the ingest prompt must not grant edit/terminal to the
    read-only apia-planner. Prompt-level tools must stay within the planner's
    read-only posture (no edit/files, no run/terminal)."""
    path = os.path.join(PROMPTS_DIR, "apia-ingest.prompt.md")
    data, _ = _frontmatter(path)
    assert data.get("agent") == "apia-planner", (
        "ingest prompt is expected to drive the read-only planner"
    )
    tools = [str(t).lower() for t in data.get("tools", [])]
    joined = " ".join(tools)
    assert "edit" not in joined, (
        "ingest prompt must not grant edit tools to the read-only planner"
    )
    assert "terminal" not in joined, (
        "ingest prompt must not grant run/terminal to the read-only planner"
    )


# --- prompt rules -----------------------------------------------------------


def test_prompts_have_valid_frontmatter_with_description():
    prompts = _prompt_files()
    assert prompts, "no *.prompt.md files found"
    for path in prompts:
        data, _ = _frontmatter(path)
        assert isinstance(data.get("description"), str) and data["description"].strip(), (
            "prompt missing description ({})".format(path)
        )


# --- no external LLM --------------------------------------------------------


def test_no_authored_artifact_references_external_llm():
    forbidden = re.compile(
        r"openai|anthropic|chat/completions|api\.openai\.com|external llm|"
        r"claude\.ai|googleapis.*generativelanguage",
        re.IGNORECASE,
    )
    paths = _skill_files() + _agent_files() + _prompt_files()
    paths.append(os.path.join(GITHUB_DIR, "copilot-instructions.md"))
    for path in paths:
        text = _read(path)
        # The anti-constraint lines deliberately name the forbidden endpoints to
        # forbid them; allow a line that is clearly a prohibition ("never"/"not").
        for i, line in enumerate(text.splitlines(), 1):
            if forbidden.search(line):
                lowered = line.lower()
                assert ("never" in lowered or "not " in lowered or "no " in lowered), (
                    "possible external-LLM reference at {}:{}: {}".format(path, i, line)
                )
