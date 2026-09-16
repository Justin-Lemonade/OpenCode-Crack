"""
Offline manager -> worker -> tester vertical-slice harness (roadmap D-167).

The first vertical slice (Phase D) is not yet provable end-to-end: the
future manager-loop implementation still needs model-free tests. This module
is a deterministic, fully-offline harness around the existing
runtime/control/handoff primitives:

    BoundedTask            one assignable, bounded task (a fixture, not a
                           task-board row)
    create_profiles()      manager, worker and tester AgentProfiles with
                           deterministic, scope-unique ids
    FakeSwarmRuntime       deterministic stand-in for SwarmRuntime — scripted
                           results, no subprocess, no network, no model calls
    VerticalSliceHarness   drives the slice and records every stage:
                           agent registration + session/lease assignment,
                           worker result (normalized SwarmRunRecord),
                           manager -> tester Handoff,
                           tester success/rejection LifecycleRecords
    SliceRecord            every identifier + evidence needed for assertions

Rules (mirroring the handoff / lifecycle-record / runtime-state-fixture
discipline from D-157 / D-147 / D-164):

  * deterministic given the same inputs — control.db writes require an
    explicit caller-supplied ``db_path`` (never the default CONTROL_DB_PATH);
  * fake runtime behavior only — nothing here launches a real OpenCode/Swarm
    process or calls a model;
  * out of scope per roadmap D-167: implementing the manager policy,
    task-board mutation, and real OpenCode execution.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Optional, Sequence

from src.runtime import control_db
from src.runtime.agent_profile import AgentProfile
from src.runtime.handoff import Handoff, TestRun, handoff_fields
from src.runtime.lease_binding import SessionLeaseBinding, bind_session_to_lease
from src.runtime.lifecycle_record import EvidenceItem, LifecycleRecord, lifecycle_fields
from src.runtime.swarm_config import render_swarm_config
from src.runtime.swarm_handoff import build_swarm_handoff
from src.runtime.swarm_normalize import SwarmRunRecord, normalize_swarm_result
from src.runtime.swarm_runtime import SwarmResult, SwarmRuntime

DEFAULT_SESSION_ID = "ses-slice-worker-1"
DEFAULT_TESTER_SESSION_ID = "ses-slice-tester-1"
DEFAULT_SWARM_ID = "sw_slice_harness"
DEFAULT_LEASE_MINUTES = 15

_READ_ONLY_TOOLS = ["search_memory", "query_database", "read_file", "read_document"]
_CODING_WRITE_TOOLS = [
    "search_memory", "query_database", "remember", "ingest_document",
    "read_file", "read_document", "write_file", "run_python",
]


@dataclass(frozen=True)
class BoundedTask:
    """One assignable, bounded task for the slice — a fixture, not a board row."""
    task_id: str
    title: str

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.title or not self.title.strip():
            raise ValueError("title must not be empty")


def create_profiles(scope: str = "slice") -> tuple[AgentProfile, AgentProfile, AgentProfile]:
    """Create validated manager/worker/tester profiles for one slice.

    Agent ids are deterministic per ``scope`` (``manager-<scope>``,
    ``worker-<scope>``, ``tester-<scope>``) so a slice is isolated in a
    shared control.db and its identifiers can be asserted directly. The
    worker and tester report to the manager (``manager_id`` set). Tool
    permissions mirror the example profiles in ``src/runtime/example_profiles.py``.
    """
    if not scope or not scope.strip():
        raise ValueError("scope must not be empty")
    manager = AgentProfile(
        agent_id=f"manager-{scope}",
        role="manager",
        model="anthropic:claude-sonnet-4-6",
        tool_permissions=list(_READ_ONLY_TOOLS),
        notes="Vertical-slice manager (D-167 harness).",
    )
    worker = AgentProfile(
        agent_id=f"worker-{scope}",
        role="worker",
        model="openrouter:anthropic/claude-haiku-4-5",
        tool_permissions=list(_CODING_WRITE_TOOLS),
        manager_id=manager.agent_id,
        notes="Vertical-slice coding worker (D-167 harness).",
    )
    tester = AgentProfile(
        agent_id=f"tester-{scope}",
        role="tester",
        model="anthropic:claude-haiku-4-5",
        tool_permissions=list(_READ_ONLY_TOOLS),
        manager_id=manager.agent_id,
        notes="Vertical-slice tester (D-167 harness).",
    )
    return manager, worker, tester


class FakeSwarmRuntime:
    """Deterministic stand-in for :class:`SwarmRuntime`: scripted results only.

    Satisfies the ``run`` / ``ingest_events`` surface the manager loop uses,
    but never spawns a subprocess, never touches the network, and never calls
    a model. ``run()`` records each invocation so tests can assert what the
    consumer sent; the scripted :class:`SwarmResult` is returned verbatim.
    ``ingest_events`` reuses the real adapter's idempotent ingestion logic
    (which touches only ``db_path``) so the harness and the runtime share one
    source of truth for control-plane event recording.
    """

    def __init__(
        self,
        *,
        status: str = "completed",
        swarm_id: str = DEFAULT_SWARM_ID,
        error: Optional[str] = None,
        session_id: str = DEFAULT_SESSION_ID,
        task_id: Optional[str] = None,
        manager_name: str = "manager",
        worker_name: str = "worker",
        tester_name: str = "tester",
        db_path: Optional[Path] = None,
    ) -> None:
        self.status = status
        self.swarm_id = swarm_id
        self.error = error
        self.session_id = session_id
        self.task_id = task_id
        self.manager_name = manager_name
        self.worker_name = worker_name
        self.tester_name = tester_name
        self.db_path = Path(db_path) if db_path else Path("unused-control.db")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.config_seen: Optional[str] = None

    def run(
        self,
        config_path: Path,
        *,
        max_concurrent: Optional[int] = None,
        budget_usd: Optional[float] = None,
        timeout_seconds: int = 3600,
        event_path: Optional[Path] = None,
    ) -> SwarmResult:
        config_path = Path(config_path)
        self.config_seen = config_path.read_text(encoding="utf-8")
        self.calls.append((
            str(config_path),
            {
                "max_concurrent": max_concurrent,
                "budget_usd": budget_usd,
                "timeout_seconds": timeout_seconds,
                "event_path": str(event_path) if event_path else None,
            },
        ))
        raw: dict[str, Any] = {
            "swarmId": self.swarm_id,
            "status": self.status,
            "agents": [
                {"name": self.manager_name, "status": "completed",
                 "model": "anthropic:claude-sonnet-4-6"},
                {"name": self.worker_name, "status": self.status,
                 "model": "openrouter:anthropic/claude-haiku-4-5"},
                {"name": self.tester_name, "status": "never_started",
                 "model": "anthropic:claude-haiku-4-5"},
            ],
        }
        if self.error:
            raw["error"] = self.error
        events = [
            {
                "id": "evt-slice-0001",
                "type": "agent-turn-done",
                "agent": self.worker_name,
                "sessionId": self.session_id,
                "taskId": self.task_id,
                "status": self.status,
            },
            {
                "id": "evt-slice-0002",
                "type": "agent-settled",
                "agent": self.worker_name,
                "sessionId": self.session_id,
                "taskId": self.task_id,
                "status": "settled",
            },
        ]
        return SwarmResult(self.swarm_id, self.status, raw["agents"], events, raw, self.error)

    def ingest_events(self, result: SwarmResult) -> int:
        """Reuse the real adapter's idempotent ingestion against this fake's db.

        ``SwarmRuntime.ingest_events`` reads ``self._control_db_path()``.
        This fake doesn't subclass SwarmRuntime, so it defines that
        method itself (below) rather than relying on inheritance —
        borrowing just the method via SwarmRuntime.ingest_events(self, ...)
        still resolves self._control_db_path() against whatever `self`
        was passed, per Python's normal method-resolution rules.
        """
        return SwarmRuntime.ingest_events(self, result)

    def _control_db_path(self) -> Path:
        """This fake's control-plane DB target for ingest_events(): the
        db_path it was constructed with, so ingestion stays isolated to
        the same tmp-path DB the harness/test uses, never the real
        default CONTROL_DB_PATH."""
        return self.db_path


@dataclass(frozen=True)
class WorkerResult:
    """The recorded worker-phase outcome of one slice run."""
    task_id: str
    session_id: str
    swarm_id: Optional[str]
    status: str
    record: SwarmRunRecord
    evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "swarm_id": self.swarm_id,
            "status": self.status,
            "evidence": [{"kind": e.kind, "value": e.value} for e in self.evidence],
        }


@dataclass(frozen=True)
class SliceRecord:
    """The recorded vertical slice: every identifier + evidence for assertions.

    Carries the whole slice in one deterministic value — task/agent/session/
    swarm identifiers, the worker result, the manager -> tester handoff, and
    both tester fixtures (success/approval and rejection) — so future
    manager-loop tests can assert against one object instead of re-deriving
    state from a live runtime.
    """
    task_id: str
    manager_id: str
    worker_id: str
    tester_id: str
    session_id: str
    swarm_id: Optional[str]
    lease_acquired: bool
    worker_status: str
    worker_result: WorkerResult
    handoff: Handoff
    tester_success: LifecycleRecord
    tester_rejection: LifecycleRecord

    def identifiers(self) -> dict[str, Optional[str]]:
        """Every identifier the slice exposes, keyed by name."""
        return {
            "task_id": self.task_id,
            "manager_id": self.manager_id,
            "worker_id": self.worker_id,
            "tester_id": self.tester_id,
            "session_id": self.session_id,
            "swarm_id": self.swarm_id,
        }

    def evidence(self) -> tuple[EvidenceItem, ...]:
        """All recorded evidence, in slice order (worker, tester, tester)."""
        return (
            tuple(self.worker_result.evidence)
            + tuple(self.tester_success.evidence)
            + tuple(self.tester_rejection.evidence)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identifiers(),
            "lease_acquired": self.lease_acquired,
            "worker_status": self.worker_status,
            "worker_result": self.worker_result.as_dict(),
            "handoff": handoff_fields(self.handoff),
            "tester_success": lifecycle_fields(self.tester_success),
            "tester_rejection": lifecycle_fields(self.tester_rejection),
        }

    def to_json(self) -> str:
        """Stable serialization — same slice, same bytes, every time."""
        return json.dumps(self.as_dict(), sort_keys=True, indent=2)


class VerticalSliceHarness:
    """Drive and record one offline manager -> worker -> tester slice.

    The harness owns the deterministic scaffold — profiles, one bounded
    task, a fake runtime, and an explicit control.db path — and records each
    stage of the slice as a structured value that future manager-loop tests
    can assert against. It never implements manager policy, never mutates the
    task board, and never launches real OpenCode/Swarm.
    """

    def __init__(
        self,
        task: BoundedTask,
        *,
        db_path: Path,
        profiles: Optional[tuple[AgentProfile, AgentProfile, AgentProfile]] = None,
        runtime: Optional[FakeSwarmRuntime] = None,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> None:
        self.task = task
        self.db_path = Path(db_path)
        self.manager, self.worker, self.tester = profiles or create_profiles(scope=task.task_id)
        self.session_id = session_id
        if runtime is None:
            runtime = FakeSwarmRuntime(
                task_id=task.task_id,
                session_id=session_id,
                manager_name=self.manager.agent_id,
                worker_name=self.worker.agent_id,
                tester_name=self.tester.agent_id,
            )
        runtime.task_id = task.task_id
        runtime.session_id = session_id
        runtime.db_path = self.db_path
        self.runtime = runtime

    def register(self) -> tuple[str, str, str]:
        """Register the three profiles in control.db; returns their ids."""
        control_db.init_db(self.db_path)
        for profile in (self.manager, self.worker, self.tester):
            control_db.register_agent(profile, self.db_path)
        return self.manager.agent_id, self.worker.agent_id, self.tester.agent_id

    def assign_task(self, duration_minutes: int = DEFAULT_LEASE_MINUTES) -> SessionLeaseBinding:
        """Record the worker's session and bind it to the task lease (D-162).

        Requires the profiles to be registered first (see ``register``).
        Returns the lease binding; ``owned_work`` is True only when the lease
        was actually acquired and the session was linked to the task.
        """
        control_db.record_session_start(
            self.session_id, self.worker.agent_id, task_id=self.task.task_id,
            model=self.worker.model, db_path=self.db_path,
        )
        return bind_session_to_lease(
            self.task.task_id, self.worker.agent_id, self.session_id,
            duration_minutes=duration_minutes, db_path=self.db_path,
        )

    def run_worker(self, *, status: Optional[str] = None) -> WorkerResult:
        """Simulate the worker phase against the fake runtime.

        Renders the swarm config through the pure serializer, runs the fake
        swarm, normalizes the result via ``normalize_swarm_result``, and
        ingests its events into control.db (agent-settled marks the session
        terminal). Returns the recorded worker result with evidence.
        """
        if status is not None:
            self.runtime.status = status
        config_path = self._write_swarm_config()
        try:
            result = self.runtime.run(config_path)
        finally:
            Path(config_path).unlink(missing_ok=True)
        record = normalize_swarm_result(result.raw)
        self.runtime.ingest_events(result)
        evidence = [
            EvidenceItem("swarm", f"swarm:{self.runtime.swarm_id}"),
            EvidenceItem("status", record.status),
        ]
        if record.error:
            evidence.append(EvidenceItem("error", record.error))
        return WorkerResult(
            task_id=self.task.task_id,
            session_id=self.session_id,
            swarm_id=record.swarm_id,
            status=record.status,
            record=record,
            evidence=tuple(evidence),
        )

    def manager_handoff(
        self,
        worker_result: WorkerResult,
        *,
        decision_requested: str = "Verify the worker result and approve",
        changed_paths: Sequence[str] = (),
        tests: Sequence[TestRun] = (),
        artifacts: Sequence[str] = (),
        known_risks: Sequence[str] = (),
    ) -> Handoff:
        """Record the manager -> tester handoff built from the worker result.

        Uses the D-163 pure adapter (``build_swarm_handoff``) so the runtime
        outcome maps deterministically and the swarm reference travels as a
        ``swarm:<id>`` artifact. The D-157 format identifies roles, not
        concrete agent instances, so sender/recipient are the literal
        ``manager`` / ``tester`` roles; the concrete ids live in the
        :class:`SliceRecord` identifiers.
        """
        return build_swarm_handoff(
            worker_result.record,
            task_id=self.task.task_id,
            sender="manager",
            recipient="tester",
            decision_requested=decision_requested,
            changed_paths=changed_paths,
            tests=tests,
            artifacts=artifacts,
            known_risks=known_risks,
        )

    def tester_verdicts(
        self,
        *,
        session_id: str = DEFAULT_TESTER_SESSION_ID,
        success_note: str = "All acceptance criteria met.",
        rejection_reason: str = "Evidence does not cover the changed paths.",
    ) -> tuple[LifecycleRecord, LifecycleRecord]:
        """The two tester fixtures: success (approved) and rejection."""
        success = LifecycleRecord(
            task_id=self.task.task_id,
            agent_id=self.tester.agent_id,
            session_id=session_id,
            status="approved",
            evidence=[
                EvidenceItem("artifact", f"reports/{self.task.task_id}_report.md"),
                EvidenceItem("test", "python -m pytest tests/"),
            ],
            notes=success_note,
        )
        rejection = LifecycleRecord(
            task_id=self.task.task_id,
            agent_id=self.tester.agent_id,
            session_id=session_id,
            status="rejected",
            evidence=[
                EvidenceItem("reason", rejection_reason),
                EvidenceItem("path", f"reports/{self.task.task_id}_report.md"),
            ],
        )
        return success, rejection

    def run_slice(
        self,
        *,
        decision_requested: str = "Verify the worker result and approve",
        changed_paths: Sequence[str] = (),
        tests: Sequence[TestRun] = (),
        artifacts: Sequence[str] = (),
        known_risks: Sequence[str] = (),
    ) -> SliceRecord:
        """Run the full offline slice and return the recorded :class:`SliceRecord`."""
        self.register()
        binding = self.assign_task()
        worker_result = self.run_worker()
        handoff = self.manager_handoff(
            worker_result,
            decision_requested=decision_requested,
            changed_paths=changed_paths,
            tests=tests,
            artifacts=artifacts,
            known_risks=known_risks,
        )
        success, rejection = self.tester_verdicts()
        record = SliceRecord(
            task_id=self.task.task_id,
            manager_id=self.manager.agent_id,
            worker_id=self.worker.agent_id,
            tester_id=self.tester.agent_id,
            session_id=worker_result.session_id,
            swarm_id=worker_result.swarm_id,
            lease_acquired=binding.owned_work,
            worker_status=worker_result.status,
            worker_result=worker_result,
            handoff=handoff,
            tester_success=success,
            tester_rejection=rejection,
        )
        self._record_control_events(record)
        return record

    def _record_control_events(self, record: SliceRecord) -> None:
        """Persist the slice's own bookkeeping into control.db events.

        The worker result and the manager -> tester handoff are the two
        structured records the slice produces; this makes them observable in
        the control plane without adding new event consumers anywhere.
        """
        control_db._append_event(
            "vertical_slice:worker_result",
            agent_id=self.worker.agent_id,
            session_id=record.session_id,
            task_id=self.task.task_id,
            payload={
                "swarm_id": record.swarm_id,
                "status": record.worker_status,
            },
            db_path=self.db_path,
        )
        control_db._append_event(
            "vertical_slice:handoff",
            agent_id=self.manager.agent_id,
            task_id=self.task.task_id,
            payload={
                "from": record.handoff.sender,
                "to": record.handoff.recipient,
                "outcome": record.handoff.outcome,
            },
            db_path=self.db_path,
        )

    def _write_swarm_config(self) -> Path:
        """Materialize only ephemeral runtime config; never Git-track it."""
        payload = render_swarm_config(self.task.title, [self.manager, self.worker, self.tester])
        with NamedTemporaryFile(
            "w", suffix=".json", prefix="ai_brain_slice_", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle, indent=2)
            return Path(handle.name)