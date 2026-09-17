from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from opencode_crack.orchestrator import autonomy
from opencode_crack.runtime.agent_profile import AgentProfile


def _profiles():
    return (
        AgentProfile("m", "manager", "manager-model"),
        AgentProfile("w", "worker", "worker-model"),
        AgentProfile("t", "tester", "tester-model"),
    )


def test_run_task_starts_and_stops_heartbeat():
    manager, worker, tester = _profiles()
    runtime = Mock()
    runtime.run_once.return_value = SimpleNamespace(task_id="D-001", error=None, swarm=None)

    with patch.object(autonomy, "_heartbeat_loop") as heartbeat:
        result = autonomy.run_task("D-001", manager, worker, tester, runtime=runtime,
                                   heartbeat_interval_seconds=1)

    assert result.task_id == "D-001"
    runtime.run_once.assert_called_once()
    heartbeat.assert_called_once()


def test_run_cycle_requeues_failed_task_with_bounded_attempt_marker():
    manager, worker, tester = _profiles()
    task = SimpleNamespace(id="D-001", state=SimpleNamespace(notes="watchdog-attempt 1: old"))
    failed = SimpleNamespace(task_id="D-001", error="worker failed", swarm=None)

    with patch.object(autonomy.control_db, "reclaim_stale_leases", return_value=[]), \
         patch.object(autonomy.task_board, "get_next_task", return_value=task), \
         patch.object(autonomy.task_board, "claim_task"), \
         patch.object(autonomy.task_board, "start_task"), \
         patch.object(autonomy, "run_task", return_value=failed), \
         patch.object(autonomy.task_board, "get_task", return_value=task), \
         patch.object(autonomy.task_board, "release_task") as release:
        result = autonomy.run_cycle(manager, worker, tester, attempt_cap=3)

    assert result.task_id == "D-001"
    assert result.error == "worker failed"
    release.assert_called_once()
    assert "watchdog-attempt 2" in release.call_args.kwargs["notes"]


def test_run_cycle_blocks_when_attempt_cap_reached():
    manager, worker, tester = _profiles()
    task = SimpleNamespace(
        id="D-001",
        state=SimpleNamespace(notes="watchdog-attempt 1\nwatchdog-attempt 2\nwatchdog-attempt 3"),
    )
    with patch.object(autonomy.control_db, "reclaim_stale_leases", return_value=[]), \
         patch.object(autonomy.task_board, "get_next_task", return_value=task), \
         patch.object(autonomy.task_board, "block_task") as block, \
         patch.object(autonomy, "run_task") as run:
        result = autonomy.run_cycle(manager, worker, tester, attempt_cap=3)
    assert result.task_id == "D-001"
    assert result.error is None
    block.assert_called_once()
    run.assert_not_called()


def test_run_cycle_uses_reclaim_stale_leases():
    manager, worker, tester = _profiles()
    task = SimpleNamespace(id="D-001", state=SimpleNamespace(notes=""))
    succeeded = SimpleNamespace(task_id="D-001", error=None, swarm=SimpleNamespace(status="completed"))

    with patch.object(autonomy.control_db, "reclaim_stale_leases", return_value=[]) as reclaim, \
         patch.object(autonomy.task_board, "get_next_task", return_value=task), \
         patch.object(autonomy.task_board, "claim_task"), \
         patch.object(autonomy.task_board, "start_task"), \
         patch.object(autonomy, "run_task", return_value=succeeded):
        result = autonomy.run_cycle(manager, worker, tester, attempt_cap=3)

    reclaim.assert_called_once()
    assert result.task_id == "D-001"
    assert result.error is None
