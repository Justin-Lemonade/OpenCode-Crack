"""
Reconcile a completed SwarmResult with AI-Brain control-plane records
(roadmap D-161, from `iteration improvement ideas status.md`).

Maps each agent reported by a swarm run onto registered agents in control.db
without redesigning the control DB or creating agents automatically:

  * matched  — an agent whose name matches a registered agent_id gets its
               status reconciled (set_agent_status) and a
               `swarm_agent_reconciled` control-plane event recording
               status/model/error;
  * unmatched — any agent with no registered counterpart is reported in the
               result, never silently inserted (per the roadmap's "report
               unmatched rather than create agents automatically").

Idempotency: the reconciliation event carries a `source_event_id` of the
form `{swarm_id}:{agent_name}` and is only written when no event of the same
type already carries that id (same mechanism as swarm event ingestion,
D-144). Re-running reconciliation over the same SwarmResult produces the
same report and adds no duplicate events.

Pure against the DB surface already exposed by control_db — no schema
change, no Git state touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opencode_crack.runtime import control_db
from opencode_crack.runtime.swarm_normalize import normalize_agent
from opencode_crack.runtime.swarm_runtime import SwarmResult

RECONCILE_EVENT_TYPE = "swarm_agent_reconciled"


@dataclass(frozen=True)
class AgentReconcile:
    """One agent from the swarm result, matched or not."""
    agent: str | None
    status: str | None = None
    model: str | None = None
    error: str | None = None
    matched: bool = False
    registered_agent_id: str | None = None


@dataclass(frozen=True)
class ReconcileReport:
    """Outcome of reconciling one swarm result against registered agents."""
    swarm_id: str | None
    matched: list[AgentReconcile] = field(default_factory=list)
    unmatched: list[AgentReconcile] = field(default_factory=list)

    @property
    def registered_count(self) -> int:
        return len(self.matched)

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched)

    def as_dict(self) -> dict[str, Any]:
        def _entry(e: AgentReconcile) -> dict[str, Any]:
            return {
                "agent": e.agent,
                "status": e.status,
                "model": e.model,
                "error": e.error,
                "matched": e.matched,
                "registered_agent_id": e.registered_agent_id,
            }

        return {
            "swarm_id": self.swarm_id,
            "matched": [_entry(e) for e in self.matched],
            "unmatched": [_entry(e) for e in self.unmatched],
            "registered_count": self.registered_count,
            "unmatched_count": self.unmatched_count,
        }


def reconcile_swarm_result(
    result: SwarmResult,
    db_path: Path = control_db.CONTROL_DB_PATH,
) -> ReconcileReport:
    """Reconcile a swarm result's agents against registered agents.

    Matched agents have their status reconciled and a deduplicated
    reconciliation event written. Unknown agents are reported as unmatched
    and never inserted. Idempotent per (swarm_id, agent) — re-running over
    the same result writes no duplicate events and reports the same matches.
    """
    registered_ids = {a["agent_id"] for a in control_db.list_agents(db_path=db_path)}
    matched: list[AgentReconcile] = []
    unmatched: list[AgentReconcile] = []

    for raw_agent in result.agents:
        norm = normalize_agent(raw_agent)
        name = norm.name or norm.model or f"unnamed#{len(matched) + len(unmatched) + 1}"
        entry = AgentReconcile(
            agent=name,
            status=norm.status,
            model=norm.model,
            error=norm.error,
            matched=name in registered_ids,
            registered_agent_id=name if name in registered_ids else None,
        )
        if entry.matched:
            if norm.status:
                control_db.set_agent_status(name, norm.status, db_path=db_path)
            _record_reconcile_event(result, name, norm, db_path)
            matched.append(entry)
        else:
            unmatched.append(entry)

    return ReconcileReport(
        swarm_id=result.swarm_id,
        matched=matched,
        unmatched=unmatched,
    )


def _record_reconcile_event(
    result: SwarmResult,
    agent_id: str,
    norm: Any,
    db_path: Path,
) -> None:
    source_id = f"{result.swarm_id or 'swarm'}:{agent_id}"
    if control_db.event_exists(RECONCILE_EVENT_TYPE, source_id, db_path=db_path):
        return
    payload: dict[str, Any] = {"source_event_id": source_id}
    if result.swarm_id:
        payload["swarm_id"] = result.swarm_id
    if norm.status:
        payload["status"] = norm.status
    if norm.model:
        payload["model"] = norm.model
    if norm.error:
        payload["error"] = norm.error
    control_db._append_event(
        event_type=RECONCILE_EVENT_TYPE,
        agent_id=agent_id,
        task_id=None,
        payload=payload,
        db_path=db_path,
    )
