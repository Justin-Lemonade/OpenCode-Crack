"""Control plane -> OpenCode Swarm coordination boundary.

This module is intentionally small: this package's control plane owns
durable organizational identity and task leases; OpenCode Swarm owns live
worker sessions, inter-agent delivery, and swarm execution state.

The coordinator translates registered AgentProfile records into a Swarm
configuration and mirrors Swarm events back into control.db. Agents receive
explicit role contracts so managers coordinate, workers implement, testers
verify, and monitors observe instead of all behaving like generic coders.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opencode_crack.config import SWARM_DB_PATH
from opencode_crack.prompts import get_prompt
from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_runtime import SwarmResult, SwarmRuntime


# Role contracts now live in the prompt registry (C-047) as
# swarm_role_{role}.v1 — this dict is a thin, backward-compatible
# wrapper built from get_prompt() calls so the one existing call site
# below and any external caller don't need to change, and so a future
# swarm_role_manager.v2 automatically flows through here without
# touching this module.
ROLE_INSTRUCTIONS = {
    role: get_prompt(f"swarm_role_{role}")
    for role in ("manager", "worker", "tester", "monitor")
}


@dataclass(frozen=True)
class SwarmLaunch:
    config_path: Path
    agent_ids: tuple[str, ...]
    task_id: str | None


def build_swarm_config(
    profiles: Iterable[AgentProfile],
    *,
    name: str = "ai-brain",
    task_id: str | None = None,
    task_prompt: str | None = None,
    max_rounds: int = 5,
    max_concurrent: int | None = None,
    budget_usd: float | None = None,
) -> dict:
    """Build the external Swarm config from this control plane's persistent roles."""
    profile_list = list(profiles)
    if not profile_list:
        raise ValueError("At least one registered agent is required")

    roster = [
        {
            "agent_id": p.agent_id,
            "role": p.role,
            "manager_id": p.manager_id,
        }
        for p in profile_list
    ]
    roster_text = json.dumps(roster, separators=(",", ":"))
    agents = []
    for profile in profile_list:
        task = _agent_prompt(profile, roster_text, task_id, task_prompt)
        agents.append(
            {
                "name": profile.agent_id,
                "task": task,
                "model": profile.model,
                "tools": _tools_for(profile),
            }
        )

    config = {
        "name": name,
        "agents": agents,
        "maxRounds": max_rounds,
    }
    if max_concurrent is not None:
        config["maxConcurrent"] = max_concurrent
    if budget_usd is not None:
        config["budgetUsd"] = budget_usd
    return config


def write_swarm_config(config: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def launch(
    profiles: Iterable[AgentProfile],
    *,
    config_path: Path,
    task_id: str | None = None,
    task_prompt: str | None = None,
    swarm_name: str = "ai-brain",
    max_rounds: int = 5,
    max_concurrent: int | None = None,
    budget_usd: float | None = None,
    runtime: SwarmRuntime | None = None,
    timeout_seconds: int = 3600,
) -> tuple[SwarmLaunch, SwarmResult]:
    """Create a role-aware swarm, run it, and mirror events into control.db."""
    profile_list = list(profiles)
    for profile in profile_list:
        control_db.register_agent(profile)
    config = build_swarm_config(
        profile_list,
        name=swarm_name,
        task_id=task_id,
        task_prompt=task_prompt,
        max_rounds=max_rounds,
        max_concurrent=max_concurrent,
        budget_usd=budget_usd,
    )
    write_swarm_config(config, config_path)
    runner = runtime or SwarmRuntime(db_path=SWARM_DB_PATH)
    result = runner.run(
        config_path,
        max_concurrent=max_concurrent,
        budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
    )
    runner.ingest_events(result)
    return SwarmLaunch(config_path, tuple(p.agent_id for p in profile_list), task_id), result


def _agent_prompt(
    profile: AgentProfile,
    roster_text: str,
    task_id: str | None,
    task_prompt: str | None,
) -> str:
    role_context = profile.role_context(profile.role)
    lines = [
        f"Persistent agent id: {profile.agent_id}",
        f"Role: {profile.role}",
        f"Manager: {profile.manager_id or 'none'}",
        ROLE_INSTRUCTIONS[profile.role],
        f"Role responsibilities: {json.dumps(role_context.get('responsibilities', []))}",
        f"Default evidence: {json.dumps(role_context.get('allowed_evidence_sources', []))}",
        f"Output contract: {role_context.get('default_output_format', '')}",
        f"Known roster: {roster_text}",
    ]
    if profile.personality:
        lines.append(f"Working style: {profile.personality}")
    if profile.notes:
        lines.append(f"Persistent notes: {profile.notes}")
    if task_id:
        lines.append(f"Task id: {task_id}")
    if task_prompt:
        lines.append(f"Current assignment:\n{task_prompt}")
    lines.append(
        "Coordination rule: communicate through swarm_send/swarm_inbox and "
        "shared Swarm memory for runtime coordination; control.db "
        "remains the durable organizational source of truth."
    )
    return "\n".join(lines)


def _tools_for(profile: AgentProfile) -> dict[str, bool]:
    """Translate this control plane's coarse permission names into Swarm tool flags."""
    requested = set(profile.tool_permissions)
    if not requested:
        requested = {"read"}
    tools = {"*": False, "read": True, "glob": True, "grep": True}
    for tool in ("edit", "write", "bash"):
        tools[tool] = tool in requested or "write" in requested or "*" in requested
    return tools
