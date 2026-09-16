"""Bounded autonomous orchestration primitives.

This closes the gap between the existing task board/control plane and
OpenCode Swarm without bypassing the review gate. A cycle may claim and
execute one D-tier task, but delegated completion remains ``review`` until a
reviewer approves it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Optional

from src.orchestrator import task_board
from src.runtime import control_db
from src.runtime.agent_profile import AgentProfile
from src.orchestrator.manager_loop import ManagerLoop, ManagerLoopResult

log = logging.getLogger(__name__)
DEFAULT_ATTEMPT_CAP = 3


@dataclass(frozen=True)
class RecoveryResult:
    recovered: int
    failed: int
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class CycleResult:
    task_id: Optional[str]
    lease_acquired: bool
    swarm_status: Optional[str]
    error: Optional[str]
    recovery: RecoveryResult = field(default_factory=lambda: RecoveryResult(0, 0, ()))


@dataclass(frozen=True)
class SchedulerResult:
    cycles_completed: int
    tasks_processed: tuple[str, ...]
    tasks_requeued: tuple[str, ...]
    tasks_escalated: tuple[str, ...]
    errors: tuple[str, ...]


def recover_stale_leases(*, db_path=None) -> RecoveryResult:
    """Release expired runtime leases and requeue matching board tasks."""
    stale = control_db.get_stale_leases(db_path=db_path) if db_path else control_db.get_stale_leases()
    recovered: list[str] = []
    failed = 0
    for lease in stale:
        task_id = lease["task_id"]
        try:
            if db_path:
                control_db.release_lease(task_id, db_path=db_path)
            else:
                control_db.release_lease(task_id)
            task = task_board.get_task(task_id)
            if task and task.state.status in task_board.ACTIVE_STATUSES:
                # Watchdog reclaim of possibly-another-owner's task: the
                # sanctioned stale-takeover path — explicit override, so the
                # activity log shows it as an override, not a normal release.
                task_board.release_task(task_id, notes="Watchdog reclaimed stale runtime lease; task requeued for retry.", override=True)
            recovered.append(task_id)
        except Exception:
            failed += 1
            log.exception("Failed to recover stale lease for %s", task_id)
    return RecoveryResult(len(recovered), failed, tuple(recovered))


def _heartbeat_loop(task_id: str, stop: Event, *, interval_seconds: int = 60, db_path=None) -> None:
    while not stop.wait(interval_seconds):
        try:
            if db_path:
                control_db.heartbeat_lease(task_id, db_path=db_path)
            else:
                control_db.heartbeat_lease(task_id)
        except Exception:
            log.exception("Lease heartbeat failed for %s", task_id)


def run_task(
    task_id: str,
    manager: AgentProfile,
    worker: AgentProfile,
    tester: AgentProfile,
    *,
    runtime: ManagerLoop | None = None,
    timeout_seconds: int = 3600,
    max_concurrent: int = 3,
    budget_usd: float | None = None,
    heartbeat_interval_seconds: int = 60,
) -> ManagerLoopResult:
    """Run one task while continuously renewing its control-plane lease."""
    loop = runtime or ManagerLoop()
    stop = Event()
    heartbeat = Thread(target=_heartbeat_loop, args=(task_id, stop),
                        kwargs={"interval_seconds": heartbeat_interval_seconds},
                        daemon=True, name=f"lease-heartbeat-{task_id}")
    heartbeat.start()
    try:
        return loop.run_once(task_id, manager, worker, tester,
                             timeout_seconds=timeout_seconds,
                             max_concurrent=max_concurrent,
                             budget_usd=budget_usd)
    finally:
        stop.set()
        heartbeat.join(timeout=max(1, heartbeat_interval_seconds))


def _attempt_count(task: task_board.Task) -> int:
    return sum(1 for line in (task.state.notes or "").splitlines() if "watchdog-attempt" in line)


def run_cycle(
    manager: AgentProfile,
    worker: AgentProfile,
    tester: AgentProfile,
    *,
    attempt_cap: int = DEFAULT_ATTEMPT_CAP,
    timeout_seconds: int = 3600,
    max_concurrent: int = 3,
    budget_usd: float | None = None,
) -> CycleResult:
    """Recover stale work, claim the next D-task, execute it, and requeue failures.

    Uses the richer ``control_db.reclaim_stale_leases`` path so retry_count,
    exponential backoff, and escalation are all honored by the scheduler loop.
    """
    recovery = control_db.reclaim_stale_leases()
    task = task_board.get_next_task()
    if task is None:
        return CycleResult(task_id=None, lease_acquired=False, swarm_status=None, error=None, recovery=_to_recovery(recovery))

    attempts = _attempt_count(task)
    if attempts >= attempt_cap:
        task_board.block_task(task.id, notes=f"watchdog-attempt cap ({attempt_cap}) reached; requires review.")
        return CycleResult(task_id=task.id, lease_acquired=False, swarm_status=None, error=None, recovery=_to_recovery(recovery))

    try:
        task_board.claim_task(task.id, worker.agent_id)
        task_board.start_task(task.id, worker.agent_id)
    except Exception as exc:
        return CycleResult(task_id=task.id, lease_acquired=False, swarm_status=None, error=str(exc), recovery=_to_recovery(recovery))

    try:
        result = run_task(task.id, manager, worker, tester,
                          timeout_seconds=timeout_seconds,
                          max_concurrent=max_concurrent,
                          budget_usd=budget_usd)
    except Exception as exc:
        return CycleResult(task_id=task.id, lease_acquired=True, swarm_status=None, error=str(exc), recovery=_to_recovery(recovery))

    if result.error:
        current = task_board.get_task(task.id)
        old_notes = current.state.notes if current else ""
        task_board.release_task(task.id, notes=(
            f"{old_notes}\nwatchdog-attempt {attempts + 1}: {result.error}"
        ).strip(), agent=worker.agent_id)

    return CycleResult(
        task_id=result.task_id,
        lease_acquired=True,
        swarm_status=result.swarm.status if result.swarm else None,
        error=result.error,
        recovery=_to_recovery(recovery),
    )


def _to_recovery(reclaimed: list[dict]) -> RecoveryResult:
    return RecoveryResult(
        recovered=sum(1 for r in reclaimed if r.get("action") == "requeued"),
        failed=0,
        task_ids=tuple(r["task_id"] for r in reclaimed),
    )


def _categorize_recovery(reclaimed: list[dict]) -> tuple[list[str], list[str]]:
    requeued: list[str] = []
    escalated: list[str] = []
    for r in reclaimed:
        if r.get("action") == "requeued":
            requeued.append(r["task_id"])
        elif r.get("action") == "escalated":
            escalated.append(r["task_id"])
    return requeued, escalated


def scheduler(manager: AgentProfile, worker: AgentProfile, tester: AgentProfile, *,
              interval_seconds: int = 300, max_cycles: int | None = None,
              stop_event: Event | None = None, **cycle_kwargs) -> SchedulerResult:
    """Run bounded autonomous cycles until stopped or ``max_cycles`` fires.

    Returns a :class:`SchedulerResult` summarizing every completed cycle so
    callers can assert on queue behavior without parsing logs.
    """
    stop = stop_event or Event()
    cycles = 0
    processed: list[str] = []
    requeued: list[str] = []
    escalated: list[str] = []
    errors: list[str] = []

    while not stop.is_set() and (max_cycles is None or cycles < max_cycles):
        try:
            cycle = run_cycle(manager, worker, tester, **cycle_kwargs)
            cycles += 1
            if cycle is None:
                break
            if cycle.task_id is None:
                break
            processed.append(cycle.task_id)
            requeued.extend(cycle.recovery.task_ids)
            if cycle.error:
                errors.append(cycle.error)
        except Exception:
            log.exception("Autonomous cycle failed")
            cycles += 1
            errors.append("cycle_exception")
        if stop.is_set():
            break
        stop.wait(interval_seconds)

    return SchedulerResult(
        cycles_completed=cycles,
        tasks_processed=tuple(processed),
        tasks_requeued=tuple(requeued),
        tasks_escalated=tuple(escalated),
        errors=tuple(errors),
    )
