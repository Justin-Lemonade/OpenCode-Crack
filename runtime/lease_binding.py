"""
Bind an existing OpenCode session to the AI-Brain task lease model
(roadmap D-162, from `iteration improvement ideas status.md`).

A session/lease binding keeps task ID, agent ID, and session ID attributable
together in one place (the ``leases`` row carries ``session_id``, and the
``oc_sessions`` row is linked to ``task_id``), while reusing the existing
control-DB lease semantics — no lease-policy change, no task-board change,
no autonomous recovery.

Invariants:
  * acquisition failure (contention, or unknown session) means the binding
    is NOT treated as owned work — no lease is left behind;
  * release and terminal-failure paths both end with no live lease;
  * stale leases behave exactly as control_db already defines them (an
    un-released expired lease still blocks a new acquisition; release is
    the explicit reclaim path).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.runtime import control_db

DEFAULT_LEASE_MINUTES = control_db.DEFAULT_LEASE_MINUTES


@dataclass(frozen=True)
class SessionLeaseBinding:
    """Result of attempting to bind a session to a task lease."""
    task_id: str
    agent_id: str
    session_id: str
    acquired: bool
    reason: Optional[str] = None

    @property
    def owned_work(self) -> bool:
        """True only when the binding is a real, owned lease."""
        return self.acquired and self.reason is None


def bind_session_to_lease(
    task_id: str,
    agent_id: str,
    session_id: str,
    duration_minutes: int = DEFAULT_LEASE_MINUTES,
    db_path: Path = control_db.CONTROL_DB_PATH,
) -> SessionLeaseBinding:
    """Acquire a lease that records this session, and attribute the session
    to the task. Returns an acquired binding only when BOTH the lease was
    taken AND the session row exists and is linked to the task; otherwise no
    owned work and no lease is left behind.
    """
    if not control_db.acquire_lease(
        task_id, agent_id, session_id=session_id,
        duration_minutes=duration_minutes, db_path=db_path,
    ):
        return SessionLeaseBinding(task_id, agent_id, session_id,
                                   acquired=False, reason="lease_not_acquired")

    linked = control_db.link_session_to_task(session_id, task_id, agent_id,
                                             db_path=db_path)
    if not linked:
        control_db.release_lease(task_id, db_path=db_path)
        return SessionLeaseBinding(task_id, agent_id, session_id,
                                   acquired=False, reason="session_not_found")

    return SessionLeaseBinding(task_id, agent_id, session_id, acquired=True)


def release_session_binding(
    binding: SessionLeaseBinding,
    status: str = "finished",
    db_path: Path = control_db.CONTROL_DB_PATH,
) -> None:
    """Release the lease and close the session. Idempotent — safe to call
    on a failed (never acquired) binding or more than once. Leaves no live
    lease behind. ``status`` mirrors record_session_end semantics; use a
    non-finished status for the terminal-failure path.
    """
    control_db.release_lease(binding.task_id, db_path=db_path)
    control_db.record_session_end(binding.session_id, status=status,
                                  db_path=db_path)