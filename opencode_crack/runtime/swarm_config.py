"""Pure serializer from validated AgentProfile objects to the minimal
OpenCode Swarm agent-configuration shape (roadmap D-143, broken out of
C-074).

Kept free of I/O, DB access, and imports of anything stateful so the
rendering can be unit tested without a swarm binary or control.db. The
runtime adapter (SwarmRuntime.run) consumes this shape as a config file.
"""
from __future__ import annotations

from typing import Iterable

from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.role_context import get_role_context

ROLE_TASK_TEMPLATES = {
    "manager": (
        "You are the manager. Coordinate this bounded task: {task}. "
        "Inspect worker output, send concise instructions, and require evidence before completion."
    ),
    "worker": (
        "Implement the bounded task: {task}. "
        "Report concrete files/tests/evidence to the manager. Do not redesign architecture."
    ),
    "tester": (
        "Independently test/review work for: {task}. "
        "Reject unsupported completion claims and report evidence to the manager."
    ),
    "monitor": (
        "Observe system and agent health for: {task}. "
        "Report anomalies only; do not modify state or interfere with active tasks."
    ),
}

_FALLBACK_TASK_TEMPLATE = "Work on this bounded task: {task}."


def render_agent_config(profile: AgentProfile, task_title: str) -> dict:
    """Render one validated profile as a minimal swarm agent entry.

    ``role``, ``name``, ``model``, the role-specific ``task``, and ``tools``
    (tool restrictions as an allow-map) are always present. Unset optional
    fields (``manager_id``, ``personality``, ``notes``) are omitted rather
    than emitted as nulls.
    """
    template = ROLE_TASK_TEMPLATES.get(profile.role, _FALLBACK_TASK_TEMPLATE)
    entry: dict = {
        "name": profile.agent_id,
        "role": profile.role,
        "model": profile.model,
        "task": template.format(task=task_title),
        "tools": {tool: True for tool in profile.tool_permissions},
    }
    if profile.manager_id:
        entry["manager_id"] = profile.manager_id
    if profile.personality:
        entry["personality"] = profile.personality
    if profile.notes:
        entry["notes"] = profile.notes
    context = get_role_context(profile.role)
    if context:
        entry["role_context"] = context
    return entry


def render_swarm_config(task_title: str, profiles: Iterable[AgentProfile],
                        *, max_rounds: int = 5) -> dict:
    """Render a full minimal swarm config from validated profiles."""
    profiles = list(profiles)
    if not profiles:
        raise ValueError("at least one agent profile is required")
    return {
        "name": f"ai-brain-{profiles[0].agent_id}",
        "model": profiles[0].model,
        "maxRounds": max_rounds,
        "agents": [render_agent_config(profile, task_title) for profile in profiles],
    }
