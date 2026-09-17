"""
Deterministic runtime-state fixtures for the manager loop (roadmap D-164,
broken out of C-074).

Mirrors `tests/swarm_fixtures.py` (D-142) and `tests/lifecycle_fixtures.py`
(D-147): fixtures are generated at test time, byte-for-byte reproducible,
and every fixture carries explicit task / agent / session identifiers plus
explicit evidence and an unambiguous status. They are consumed by future
manager-loop tests without any live OpenCode.

The six runtime states a manager loop must handle:

    build_worker_success()        worker finished, tests green
    build_worker_failure()        worker finished with a failing result
    build_timeout()               worker ran past its time budget
    build_unavailable_runtime()   the swarm runtime could not start
    build_reviewer_rejection()    reviewer rejected the submission
    build_stale_session()         session/lease expired while claimed

Each builder returns a ``RuntimeState`` whose ``status`` is one explicit,
non-ambiguous value (never "unknown", never empty), so tests can prove that
no two states share a status and every state has a defined terminal reading.
The values are illustrative, not real repo state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from opencode_crack.runtime.lifecycle_record import EvidenceItem, LifecycleRecord
from opencode_crack.runtime.swarm_normalize import AgentRecord, SwarmRunRecord

# Canonical states a manager loop must handle. The status below is the
# explicit, non-ambiguous reading for each.
CANONICAL_STATES = (
    "worker_success",
    "worker_failure",
    "timeout",
    "unavailable_runtime",
    "reviewer_rejection",
    "stale_session",
)


@dataclass(frozen=True)
class RuntimeState:
    """One runtime state with explicit identifiers, evidence, and status.

    ``source`` keeps the underlying normalized record (SwarmRunRecord,
    LifecycleRecord, or a lease/session snapshot) so manager-loop tests can
    consume the fixture without re-deriving anything from a live runtime.
    """
    state: str
    task_id: str
    agent_id: str
    session_id: str
    status: str
    evidence: tuple[EvidenceItem, ...]
    source: Any = None

    def __post_init__(self) -> None:
        if self.state not in CANONICAL_STATES:
            raise ValueError(
                f"state must be one of {CANONICAL_STATES}, got {self.state!r}"
            )
        if not self.status or self.status.strip() == "" or self.status == "unknown":
            raise ValueError(
                f"{self.state} must have an explicit non-ambiguous status, "
                f"got {self.status!r}"
            )
        if not self.task_id or not self.agent_id or not self.session_id:
            raise ValueError("task_id, agent_id and session_id must all be set")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "status": self.status,
            "evidence": [{"kind": e.kind, "value": e.value} for e in self.evidence],
        }

    def to_json(self) -> str:
        """Stable serialization — same input, same bytes, every time."""
        return json.dumps(self.as_dict(), sort_keys=True, indent=2)


# --- swarm-runtime-backed states -------------------------------------------------


def _swarm(status: str, error: Optional[str], agent: AgentRecord, swarm_id: str) -> SwarmRunRecord:
    return SwarmRunRecord(
        swarm_id=swarm_id, status=status, agents=(agent,), error=error
    )


def build_worker_success(task_id: str = "D-164", session_id: str = "ses-worker-1") -> RuntimeState:
    agent = AgentRecord(name="worker", status="completed", model="openai/gpt-4o-mini",
                        tokens=18400, cost=0.31)
    return RuntimeState(
        state="worker_success",
        task_id=task_id,
        agent_id="worker",
        session_id=session_id,
        status="completed",
        evidence=[
            EvidenceItem("test", "python -m pytest tests/test_swarm_fixtures.py"),
            EvidenceItem("swarm", "sw_fixture_worker_success"),
        ],
        source=_swarm("completed", None, agent, "sw_fixture_worker_success"),
    )


def build_worker_failure(task_id: str = "D-164", session_id: str = "ses-worker-1") -> RuntimeState:
    agent = AgentRecord(name="worker", status="failed", model="openai/gpt-4o-mini",
                        tokens=9400, cost=0.17,
                        error="worker exited after reaching max rounds (5) without a stable result")
    return RuntimeState(
        state="worker_failure",
        task_id=task_id,
        agent_id="worker",
        session_id=session_id,
        status="failed",
        evidence=[
            EvidenceItem("error", "worker exited after reaching max rounds (5) without a stable result"),
            EvidenceItem("swarm", "sw_fixture_worker_failure"),
        ],
        source=_swarm("failed", "worker exited after reaching max rounds (5) without a stable result",
                      agent, "sw_fixture_worker_failure"),
    )


def build_timeout(task_id: str = "D-164", session_id: str = "ses-worker-1") -> RuntimeState:
    agent = AgentRecord(name="worker", status="timed_out", model="openai/gpt-4o-mini",
                        tokens=31000, cost=0.52)
    return RuntimeState(
        state="timeout",
        task_id=task_id,
        agent_id="worker",
        session_id=session_id,
        status="timed_out",
        evidence=[
            EvidenceItem("timeout_seconds", "3600"),
            EvidenceItem("swarm", "sw_fixture_timeout"),
        ],
        source=_swarm("timed_out", "swarm run exceeded timeout_seconds=3600",
                      agent, "sw_fixture_timeout"),
    )


def build_unavailable_runtime(task_id: str = "D-164", session_id: str = "ses-worker-1") -> RuntimeState:
    agent = AgentRecord(name="worker", status="never_started", model="openai/gpt-4o-mini")
    return RuntimeState(
        state="unavailable_runtime",
        task_id=task_id,
        agent_id="worker",
        session_id=session_id,
        status="unavailable",
        evidence=[
            EvidenceItem("error", "runtime backend could not be reached"),
            EvidenceItem("swarm", "sw_fixture_unavailable"),
        ],
        source=_swarm("failed", "runtime backend could not be reached",
                      agent, "sw_fixture_unavailable"),
    )


# --- review-backed states ---------------------------------------------------------


def build_reviewer_rejection(task_id: str = "D-164", session_id: str = "ses-review-1") -> RuntimeState:
    record = LifecycleRecord(
        task_id=task_id,
        agent_id="reviewer",
        session_id=session_id,
        status="rejected",
        evidence=[
            EvidenceItem("reason", "Evidence does not reference the full diff"),
            EvidenceItem("path", "reports/D-164_report.md"),
        ],
    )
    return RuntimeState(
        state="reviewer_rejection",
        task_id=task_id,
        agent_id="reviewer",
        session_id=session_id,
        status="rejected",
        evidence=record.evidence,
        source=record,
    )


# --- lease/session-backed state ----------------------------------------------------


def build_stale_session(task_id: str = "D-164", session_id: str = "ses-worker-1") -> RuntimeState:
    """A lease that expired while still claimed — the session is no longer live."""
    return RuntimeState(
        state="stale_session",
        task_id=task_id,
        agent_id="worker",
        session_id=session_id,
        status="stale",
        evidence=[
            EvidenceItem("lease", "expired"),
            EvidenceItem("expires_at", "2026-08-18T12:00:00+00:00"),
        ],
        source={
            "task_id": task_id,
            "agent_id": "worker",
            "session_id": session_id,
            "expires_at": "2026-08-18T12:00:00+00:00",
        },
    )


BUILDERS = {
    "worker_success": build_worker_success,
    "worker_failure": build_worker_failure,
    "timeout": build_timeout,
    "unavailable_runtime": build_unavailable_runtime,
    "reviewer_rejection": build_reviewer_rejection,
    "stale_session": build_stale_session,
}


def build_all(task_id: str = "D-164") -> dict[str, RuntimeState]:
    """Every canonical state as a dict keyed by state name."""
    return {name: builder(task_id) for name, builder in BUILDERS.items()}