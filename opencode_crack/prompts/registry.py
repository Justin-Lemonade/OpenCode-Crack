"""
Named prompt template registry (roadmap D-061, adapted for this
framework by C-076).

Central home for prompt strings, so future prompt versions can be
added without touching callers. Keys follow the ``"{name}.v{N}"``
convention. `get_prompt()` returns the latest version by default, or a
specific version — accepts both ``get_prompt("swarm_role_manager")``
(latest) and ``get_prompt("swarm_role_manager.v1")`` (explicit).

AI-Brain's original version of this registry also held
`gap_analysis.v1`, `clarifying_questions.v1`, and `stale_question.v1` —
prompts for that project's personal-knowledge gap-analysis feature
(`src/agent/gap_analysis.py`, `src/agent/rule_based_gaps.py`), which
aren't part of this framework and stay in AI-Brain. Only the swarm role
contracts (C-047), which are genuinely generic to any multi-agent swarm
this framework coordinates, are here.
"""

PROMPTS: dict[str, str] = {
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

    - ``get_prompt("swarm_role_manager")`` → the latest ``swarm_role_manager.vN``.
    - ``get_prompt("swarm_role_manager", version=1)`` → that specific version.
    - ``get_prompt("swarm_role_manager.v1")`` → same as passing version=1.

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
