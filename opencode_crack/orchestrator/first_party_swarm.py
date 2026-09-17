"""First-party agent swarm fallback research prototype (D-308).

Reuses existing authoritative mechanisms:
- task_board.get_next_task / claim_task / start_task / submit_for_review
- control_db leases (acquire_lease, heartbeat, release)
- worktree_guard.resolve_lane_worktree / validate_lane_worktree / check_contamination
- tools.permissions.evaluate

Does NOT replace ZaxbyHub/OpenCode Swarm; this is a bounded research seam
that demonstrates explicit manager→worker→tester handoffs using the same
gates as production. Prototype is local-only, no external Swarm binary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from opencode_crack.orchestrator import task_board
from opencode_crack.orchestrator.worktree_guard import (
    WorktreeProvisioningError,
    check_contamination,
    resolve_lane_worktree,
    validate_lane_worktree,
)
from opencode_crack.runtime import control_db
from opencode_crack.tools.permissions import evaluate
from opencode_crack.tools.registry import ToolSpec
from opencode_crack.orchestrator.task_board import Task

@dataclass(frozen=True)
class HandoffEvent:
    from_role: str
    to_role: str
    task_id: str
    success: bool
    detail: str = ""


@dataclass
class PrototypeResult:
    task_id: str
    handoffs: list[HandoffEvent] = field(default_factory=list)
    isolation_verified: bool = False
    permission_gate: str = "unknown"
    lease_held: bool = False
    submitted: bool = False
    error: Optional[str] = None


def _check_permission(agent_kind: str, tool_name: str, handler) -> str:
    spec = ToolSpec(name=tool_name, description="", schema={}, permissions=[], timeout_seconds=10, side_effect_level="write", supported_agents=["any"], handler=handler)
    # Adapt to evaluate signature: evaluate(spec, agent_kind)
    try:
        res = evaluate(spec, agent_kind=agent_kind)
        return str(res.decision)
    except Exception as e:
        return f"error:{e}"


def run_first_party_task(
    task_id: str,
    worker_agent_id: str = "fp-worker-01",
    tester_agent_id: str = "fp-tester-01",
    manager_agent_id: str = "fp-manager-01",
    session_id: str = "fp-sess-01",
    work_handler: Optional[Callable[[Task], str]] = None,
    use_worktree: bool = True,
) -> PrototypeResult:
    """Execute one task through explicit manager→worker→tester handoffs."""
    result = PrototypeResult(task_id=task_id)
    # 1. Acquire task via authoritative board
    task = task_board.get_task(task_id)
    if task is None:
        result.error = f"task {task_id} not found"
        return result
    # Ensure agents exist
    from opencode_crack.runtime.agent_profile import AgentProfile
    for aid, role in [(manager_agent_id, "manager"), (worker_agent_id, "worker"), (tester_agent_id, "tester")]:
        try:
            control_db.register_agent(AgentProfile(aid, role, "test-model"), db_path=control_db.CONTROL_DB_PATH)
        except Exception:
            pass

    # 2. Lease acquisition (authoritative)
    acquired = control_db.acquire_lease(task_id, worker_agent_id, session_id=session_id, db_path=control_db.CONTROL_DB_PATH)
    result.lease_held = acquired
    if not acquired:
        result.handoffs.append(HandoffEvent("manager", "worker", task_id, False, "lease_not_acquired"))
        result.error = "lease contention"
        return result
    result.handoffs.append(HandoffEvent("manager", "worker", task_id, True, "lease_acquired"))

    # 3. Worktree isolation (authoritative)
    lane = None
    if use_worktree:
        try:
            lane = resolve_lane_worktree(task_id, session_id)
            validate_lane_worktree(lane)
            result.isolation_verified = True
            result.handoffs.append(HandoffEvent("worker", "worker", task_id, True, f"worktree:{lane.path}"))
        except WorktreeProvisioningError as e:
            result.isolation_verified = False
            result.handoffs.append(HandoffEvent("worker", "worker", task_id, False, f"worktree_failed:{e}"))
            control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
            result.error = str(e)
            return result
        except Exception as e:
            result.error = f"worktree unexpected: {e}"
            control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
            return result
    else:
        # Still check contamination in shared checkout
        cont = check_contamination()
        result.isolation_verified = cont.clean
        if not cont.clean:
            result.handoffs.append(HandoffEvent("worker", "worker", task_id, False, f"contamination:{cont.unexpected_files}"))
            control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
            result.error = "contamination"
            return result

    # 4. Permission gate (authoritative)
    perm = _check_permission("worker", "write_file", handler=lambda p: None)
    result.permission_gate = perm
    if "DENY" in perm:
        result.handoffs.append(HandoffEvent("worker", "worker", task_id, False, "permission_denied"))
        control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
        result.error = "permission denied"
        return result

    # 5. Execute work via handler
    try:
        if work_handler:
            detail = work_handler(task)
        else:
            detail = "prototype work executed"
        result.handoffs.append(HandoffEvent("worker", "tester", task_id, True, detail))
    except Exception as e:
        result.handoffs.append(HandoffEvent("worker", "tester", task_id, False, f"worker_error:{e}"))
        control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
        result.error = str(e)
        return result

    # 6. Tester handoff (simulate review) — explicit handoff, permission check
    tester_perm = _check_permission("tester", "read_file", handler=lambda p: None)
    result.handoffs.append(HandoffEvent("tester", "manager", task_id, True, f"tester_perm:{tester_perm}"))

    # 7. Submit for review via authoritative board (if task was claimed)
    # Ensure task is claimed/started so submit succeeds; if not, claim now
    cur = task_board.get_task(task_id)
    if cur and cur.state.status in ("open", "claimed", "in_progress"):
        try:
            if cur.state.status == "open":
                task_board.claim_task(task_id, worker_agent_id)
                task_board.start_task(task_id, worker_agent_id)
            elif cur.state.status == "claimed":
                task_board.start_task(task_id, worker_agent_id)
            task_board.submit_for_review(task_id, notes=f"first-party prototype handoffs: {len(result.handoffs)}", report_path=None, agent=worker_agent_id)
            result.submitted = True
        except Exception as e:
            result.error = f"submit failed: {e}"
    else:
        result.submitted = False

    # 8. Release lease (always)
    control_db.release_lease(task_id, db_path=control_db.CONTROL_DB_PATH)
    return result
