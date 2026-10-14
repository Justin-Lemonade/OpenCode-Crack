"""Swarm stop / resume support built on agent role memory.

A swarm can stop for many reasons: a timeout, a quota wall, a crash, the
operator closing the window. The swarm run itself is recoverable through
``swarm run --resume <swarm_id>``; what was lost before this module is the
agents' own working knowledge and which run to resume.

After every swarm run (including a timed-out or failed one) each agent that
has a memory file gets:

  * a replaced ``Last swarm run`` section: swarm id, per-agent status, run
    status, task id and the exact resume hint;
  * one ledger line.

The agent's own sections (Current state, Lessons, ...) are never touched
here, so a human or the agent keeps full authority over them. Agents without
a memory file are skipped: memory is opt-in per agent (``agentmem init``).

Nothing here raises into the swarm path. A memory problem must never turn a
finished run into a failure.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from src.learning import agent_memory
from opencode_crack.runtime.swarm_normalize import normalize_agent
from opencode_crack.runtime.swarm_runtime import SwarmResult

logger = logging.getLogger(__name__)

SECTION = "Last swarm run"
_ERROR_LIMIT = 500
_FIELD_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)


def _fields(body: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _FIELD_RE.finditer(body)}


def _render(agent_id: str, result: SwarmResult, agent_status: Optional[str],
            task_id: Optional[str], with_error: bool) -> str:
    lines = [
        f"swarm_id: {result.swarm_id or 'unknown'}",
        f"run_status: {result.status}",
        f"agent_status: {agent_status or result.status}",
    ]
    if task_id:
        lines.append(f"task_id: {task_id}")
    if with_error and result.error:
        lines.append(f"error: {' '.join(result.error.split())[:_ERROR_LIMIT]}")
    lines.append(f"recorded: {agent_memory._now()}")
    lines.append(f"resume: python -m src.main agentmem resume {agent_id}")
    return "\n".join(lines)


def checkpoint_swarm(
    result: SwarmResult,
    agent_ids: Iterable[str],
    *,
    task_id: Optional[str] = None,
    root: Optional[Path] = None,
) -> list[str]:
    """Record the run in each opted-in agent's memory. Returns the ids written.

    Never raises. If the error text trips the secret screen it is dropped and
    the rest of the checkpoint is still saved.
    """
    per_agent = {}
    for raw in result.agents or []:
        norm = normalize_agent(raw)
        if norm.name:
            per_agent[norm.name] = norm
    written: list[str] = []
    for agent_id in agent_ids:
        try:
            if not agent_memory.memory_path(agent_id, root).is_file():
                continue
            norm = per_agent.get(agent_id)
            status = norm.status if norm else None
            for with_error in (True, False):
                try:
                    body = _render(agent_id, result, status, task_id, with_error)
                    agent_memory.set_section(agent_id, SECTION, body, root)
                    break
                except agent_memory.MemorySecretError:
                    if not with_error:
                        raise
            agent_memory.append_ledger(
                agent_id, "swarm",
                f"swarm_id={result.swarm_id or 'unknown'} run_status={result.status}"
                + (f" task={task_id}" if task_id else ""),
                root,
            )
            written.append(agent_id)
        except Exception as exc:  # noqa: BLE001 - must not break the swarm path
            logger.warning("swarm checkpoint skipped for %s: %s", agent_id, type(exc).__name__)
    return written


def last_run(agent_id: str, root: Optional[Path] = None) -> dict[str, str]:
    """Fields of the agent's ``Last swarm run`` section ({} when none)."""
    return _fields(agent_memory.get_section(agent_id, SECTION, root))


def find_resume_id(
    agent_ids: Iterable[str], root: Optional[Path] = None,
) -> Optional[str]:
    """Swarm id of the most recent unfinished run recorded by these agents.

    None when nothing was recorded or the latest recorded run completed
    (nothing to resume). ``recorded`` timestamps are ISO-8601 UTC, so plain
    string comparison orders them correctly.
    """
    latest: Optional[dict[str, str]] = None
    for agent_id in agent_ids:
        run = last_run(agent_id, root)
        if not run.get("swarm_id") or run["swarm_id"] == "unknown":
            continue
        if latest is None or run.get("recorded", "") > latest.get("recorded", ""):
            latest = run
    if latest is None or latest.get("run_status") == "completed":
        return None
    return latest["swarm_id"]


def resume_brief(agent_id: str, root: Optional[Path] = None) -> str:
    """Human-readable resume summary for one agent (used by the CLI)."""
    run = last_run(agent_id, root)
    state = agent_memory.get_section(agent_id, agent_memory.STATE_SECTION, root)
    if not run and not state:
        return f"No swarm checkpoint or state recorded for {agent_id}."
    parts = [f"Resume brief for {agent_id}"]
    if run:
        parts += ["", "Last swarm run:", *(f"  {k}: {v}" for k, v in run.items())]
    if state:
        parts += ["", "Current state:", *("  " + ln for ln in state.splitlines())]
    warning = agent_memory.staleness_note(agent_id, root)
    if warning:
        parts += ["", warning]
    return "\n".join(parts)
