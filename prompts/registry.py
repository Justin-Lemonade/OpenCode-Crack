"""
Named prompt template registry (roadmap D-061).

Central home for prompt strings that used to live inline in agent
modules. Keys follow the ``"{name}.v{N}"`` convention so future prompt
versions (C-047) can be added without touching callers. `get_prompt()`
returns the latest version by default, or a specific version — and
accepts both ``get_prompt("gap_analysis")`` (latest) and
``get_prompt("gap_analysis.v1")`` (explicit, same string as the roadmap
example shows).

C-047 also migrated `swarm_coordinator.py`'s `ROLE_INSTRUCTIONS` dict in
here (`swarm_role_{manager,worker,tester,monitor}.v1`) — that module
kept its own `ROLE_INSTRUCTIONS` name as a thin wrapper built from
`get_prompt()` calls, same pattern `gap_analysis`/`rule_based_gaps`
already used, so no caller needed to change.
"""
import json

PROMPTS: dict[str, str] = {
    "gap_analysis.v1": (
        "You are reviewing a personal knowledge profile to "
        "find gaps, ambiguities, or contradictions. Given the facts below, generate "
        "3-5 short, specific clarifying questions that would most improve this "
        "profile. Return ONLY a JSON array of strings, nothing else.\n"
        "\n"
        "Known facts:\n"
        "{facts}"
    ),
    # Structured clarifying questions used by the rule-based fallback.
    # Stored as JSON because the registry holds prompt strings; callers
    # parse it back into the dict they iterate over.
    "clarifying_questions.v1": json.dumps(
        {
            "career": [
                "What's your current job or role?",
                "What are you working toward professionally?",
            ],
            "preferences": [
                "Any strong likes/dislikes I should know when giving recommendations?",
            ],
            "health": [
                "Anything about your health or routines that should shape advice I give you?",
            ],
            "hobby": [
                "What do you do for fun outside of work?",
            ],
            "relationships": [
                "Anyone important in your life I should know about (without needing private details)?",
            ],
            "goals": [
                "What's a goal you're actively working on right now?",
            ],
        },
        indent=2,
    ),
    "stale_question.v1": "Is this still accurate — {key}: {value}?",
    # Swarm agent role contracts (C-047), migrated from
    # src/runtime/swarm_coordinator.py's ROLE_INSTRUCTIONS dict, which
    # buried these across an unrelated coordination module with no
    # version pinning or dedicated test contract.
    "swarm_role_manager.v1": (
        "You are the manager. Coordinate the assigned bounded work; do not "
        "silently implement worker tasks yourself. Use swarm_send to assign "
        "specific work to workers and request evidence. Review worker results "
        "before declaring the task complete. Escalate architecture decisions."
    ),
    "swarm_role_worker.v1": (
        "You are a worker. Implement only the bounded assignment you receive. "
        "Use swarm_send to report blockers or completed evidence to your manager. "
        "Do not redesign shared architecture without manager approval."
    ),
    "swarm_role_tester.v1": (
        "You are the tester/reviewer. Independently verify the submitted work "
        "against acceptance criteria. Do not treat a worker's completion claim "
        "as evidence. Send a pass/fail verdict with concrete test or inspection "
        "evidence to the manager. Do not fix the implementation yourself."
    ),
    "swarm_role_monitor.v1": (
        "You are the monitor. Observe agent health, messages, leases, and "
        "execution events. Report anomalies and stale work; do not mutate "
        "project state or interfere with active work."
    ),
}


def _latest_key(name: str) -> str:
    """Highest ``{name}.v{N}`` key for a base name, or None if none."""
    candidates = {
        key: int(key.rsplit(".v", 1)[1])
        for key in PROMPTS
        if key.startswith(f"{name}.v") and key.rsplit(".v", 1)[1].isdigit()
    }
    if not candidates:
        return None
    return max(candidates, key=candidates.get)


def get_prompt(name: str, version: int | None = None) -> str:
    """Return a named prompt template.

    - ``get_prompt("gap_analysis")`` → the latest ``gap_analysis.vN``.
    - ``get_prompt("gap_analysis", version=1)`` → that specific version.
    - ``get_prompt("gap_analysis.v1")`` → same as passing version=1.

    Raises `KeyError` for an unknown name or version.
    """
    if version is not None:
        key = f"{name}.v{version}"
    elif ".v" in name and name.rsplit(".v", 1)[1].isdigit():
        key = name
    else:
        key = _latest_key(name)

    if key is None or key not in PROMPTS:
        known = sorted(PROMPTS)
        raise KeyError(
            f"No such prompt: {name!r} (version={version}). "
            f"Known prompts: {known}"
        )
    return PROMPTS[key]