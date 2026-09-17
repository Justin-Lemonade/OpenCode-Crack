from pathlib import Path
import subprocess

import pytest

from opencode_crack.orchestrator import worktree_guard
from opencode_crack.orchestrator.manager_loop import ManagerLoop
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_runtime import SwarmResult
from opencode_crack.runtime import control_db


class FakeRuntime:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.config_seen = None
        self.cwd_seen = None

    def run(self, config_path, **kwargs):
        self.config_seen = Path(config_path).read_text(encoding="utf-8")
        self.cwd_seen = kwargs.get("cwd")
        return SwarmResult(
            swarm_id="sw_test",
            status="completed",
            events=[{"type": "agent-settled", "agent": "worker"}],
            agents=[{"name": "worker", "status": "completed"}],
        )

    def ingest_events(self, result):
        return len(result.events)


def profiles():
    return (
        AgentProfile("manager", "manager", "openai/gpt-4o-mini", ["read"]),
        AgentProfile("worker", "worker", "openai/gpt-4o-mini", ["read", "edit", "write", "bash"]),
        AgentProfile("tester", "tester", "openai/gpt-4o-mini", ["read", "bash"]),
    )


@pytest.fixture
def isolated_repo(tmp_path):
    """A throwaway git repo -- D-290 requires ManagerLoop.repo_root to be
    explicit exactly so tests (and real callers) never default to
    resolving worktrees inside whatever directory the process happens to
    be running in. Discovered the hard way: before this fixture existed,
    running these tests from the actual AI-Brain checkout created a real
    `.swarm-worktrees-local/worker/D-999` worktree and
    `swarm/lane/worker/D-999` branch IN THE REAL REPO."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@a.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=repo, check=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_manager_loop_builds_three_role_swarm(monkeypatch, tmp_path, isolated_repo):
    # Avoid the real task parser/repository board: the loop only needs a task title.
    class Task:
        title = "bounded test task"

    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda task_id: Task())
    runtime = FakeRuntime(tmp_path / "control.db")
    loop = ManagerLoop(runtime=runtime, db_path=runtime.db_path, repo_root=isolated_repo)
    manager, worker, tester = profiles()

    result = loop.run_once("D-999", manager, worker, tester)

    assert result.lease_acquired is True
    assert result.swarm.status == "completed"
    assert result.event_count == 1
    assert '"name": "manager"' in runtime.config_seen
    assert '"name": "worker"' in runtime.config_seen
    assert '"name": "tester"' in runtime.config_seen
    assert '"maxRounds": 5' in runtime.config_seen
    assert not list(tmp_path.glob("ai_brain_swarm_*.json"))

    # D-290: the swarm must actually be launched with the isolated
    # worktree as its cwd, not merely have one "available" somewhere.
    assert runtime.cwd_seen is not None
    assert runtime.cwd_seen == result.worktree_path
    assert worktree_guard.is_isolated_worktree(cwd=runtime.cwd_seen)


def test_manager_loop_rejects_duplicate_lease(monkeypatch, tmp_path, isolated_repo):
    """A different agent cannot acquire a lease already held by another agent."""
    class Task:
        title = "bounded test task"

    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda task_id: Task())
    db_path = tmp_path / "control.db"
    control_db.init_db(db_path)
    control_db.register_agent(AgentProfile("worker", "worker", "model"), db_path)
    # Another agent (e.g., a stale/previous run) already holds the lease
    control_db.register_agent(AgentProfile("stale_worker", "worker", "model"), db_path)
    assert control_db.acquire_lease("D-999", "stale_worker", db_path=db_path)

    loop = ManagerLoop(runtime=FakeRuntime(db_path), db_path=db_path, repo_root=isolated_repo)
    manager, worker, tester = profiles()
    result = loop.run_once("D-999", manager, worker, tester)

    assert result.lease_acquired is False
    assert "already leased" in result.error
