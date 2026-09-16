"""First manager -> worker -> tester vertical slice for OpenCode Swarm."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.orchestrator import worktree_guard
from src.orchestrator.task_board import get_task
from src.runtime import control_db
from src.runtime.agent_profile import AgentProfile
from src.runtime.swarm_config import render_swarm_config
from src.runtime.swarm_runtime import SwarmResult, SwarmRuntime


@dataclass(frozen=True)
class ManagerLoopResult:
    task_id: str
    lease_acquired: bool
    swarm: SwarmResult | None
    event_count: int
    error: str | None = None
    worktree_path: Path | None = None


class ManagerLoop:
    """Coordinate one bounded task through OpenCode Swarm.

    The Python control plane owns task ownership and agent identity. The
    swarm owns execution and agent-to-agent messaging. This deliberately
    starts with one manager, one worker, and one tester rather than the
    eventual multi-manager organization.
    """

    def __init__(
        self,
        runtime: SwarmRuntime | None = None,
        db_path: Path | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.runtime = runtime or SwarmRuntime(db_path=db_path) if db_path else runtime or SwarmRuntime()
        self.db_path = Path(db_path) if db_path else self.runtime.db_path
        # D-290: the repo whose worktrees get resolved for code-mutating
        # execution. Explicit rather than implicit Path.cwd() so tests
        # (and any future caller) control exactly which repo isolation is
        # enforced against, instead of inheriting whatever directory the
        # process happens to be running from.
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()

    def run_once(
        self,
        task_id: str,
        manager: AgentProfile,
        worker: AgentProfile,
        tester: AgentProfile,
        *,
        timeout_seconds: int = 3600,
        max_concurrent: int = 3,
        budget_usd: float | None = None,
        require_isolation: bool = True,
    ) -> ManagerLoopResult:
        """Run one task through the manager/worker/tester swarm.

        ``require_isolation`` (default True, per D-290): when set, a
        validated isolated worktree is resolved for (task_id, worker's
        agent_id) BEFORE the lease is acquired or the swarm subprocess is
        launched, and the swarm runs with that worktree as its cwd. If
        provisioning or validation fails for any reason, this returns an
        error result WITHOUT acquiring the lease or launching anything --
        it never falls back to running from the shared checkout. Set to
        False only for callers that have already established their own
        isolation guarantee by some other means (e.g. a coordinator that
        pre-validated the worktree itself).
        """
        task = get_task(task_id)
        if task is None:
            return ManagerLoopResult(task_id, False, None, 0, f"Unknown task: {task_id}")
        if manager.role != "manager" or worker.role != "worker" or tester.role != "tester":
            return ManagerLoopResult(task_id, False, None, 0, "Profiles must be manager, worker, tester respectively")

        lane: worktree_guard.LaneWorktree | None = None
        if require_isolation:
            try:
                lane = worktree_guard.resolve_lane_worktree(
                    task_id, session_id=worker.agent_id, main_repo_cwd=self.repo_root,
                )
                worktree_guard.validate_lane_worktree(lane, main_repo_cwd=self.repo_root)
            except worktree_guard.WorktreeProvisioningError as exc:
                # Hard stop: no lease acquired, no swarm launched, no
                # fallback to the shared checkout. This is the actual
                # enforcement D-290 exists to add -- D-288/D-289 already
                # established the git primitive works; this is the point
                # where a validation failure blocks execution outright.
                return ManagerLoopResult(task_id, False, None, 0, f"Worktree isolation required but not established: {exc}")

        control_db.init_db(self.db_path)
        for profile in (manager, worker, tester):
            control_db.register_agent(profile, self.db_path)

        if not control_db.acquire_lease(task_id, worker.agent_id, duration_minutes=15, db_path=self.db_path):
            return ManagerLoopResult(task_id, False, None, 0, f"Task {task_id} is already leased", worktree_path=lane.path if lane else None)

        if lane is not None:
            # Lifecycle evidence (D-290 requirement): record the
            # task/lane/worktree/branch relationship BEFORE execution, so
            # it's on record even if the run itself fails or times out.
            control_db._append_event(
                "lane_provisioned", agent_id=worker.agent_id, task_id=task_id,
                payload={"worktree_path": str(lane.path), "branch": lane.branch, "session_id": lane.session_id},
                db_path=self.db_path,
            )

        config_path = self._write_swarm_config(task.title, manager, worker, tester)
        try:
            run_kwargs = {"max_concurrent": max_concurrent, "budget_usd": budget_usd, "timeout_seconds": timeout_seconds}
            if lane is not None:
                run_kwargs["cwd"] = lane.path
            result = self.runtime.run(config_path, **run_kwargs)
            event_count = self.runtime.ingest_events(result)
            final_event = "manager_loop_completed" if result.status == "completed" else "manager_loop_failed"
            control_db._append_event(final_event, agent_id=manager.agent_id, task_id=task_id,
                                     payload={"swarm_id": result.swarm_id, "status": result.status, "error": result.error},
                                     db_path=self.db_path)
            return ManagerLoopResult(task_id, True, result, event_count, result.error, worktree_path=lane.path if lane else None)
        finally:
            config_path.unlink(missing_ok=True)
            control_db.release_lease(task_id, self.db_path)

    def _write_swarm_config(self, task_title: str, manager: AgentProfile,
                            worker: AgentProfile, tester: AgentProfile) -> Path:
        """Materialize only ephemeral runtime configuration; never Git-track it.

        The config shape is produced by the pure serializer in
        ``src/runtime/swarm_config.py`` (D-143) so manager-loop rendering
        and the runtime adapter share one source of truth.
        """
        payload = render_swarm_config(task_title, [manager, worker, tester])
        with NamedTemporaryFile("w", suffix=".json", prefix="ai_brain_swarm_", delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            return Path(handle.name)
