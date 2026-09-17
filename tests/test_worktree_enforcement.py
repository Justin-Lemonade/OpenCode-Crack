"""D-290 runtime/integration tests.

D-289 established that D-288's 17 git-primitive tests, while real, don't
prove anything about the actual orchestration path: nothing in the
codebase wired worktree_guard into dispatch. This file tests the
opposite question -- given ManagerLoop now resolves and validates an
isolated worktree before launching the swarm subprocess, is that
enforcement actually real, actually blocking on failure, and actually
unbypassable through the other execution entry points in this repo?

Uses a FakeRuntime (same pattern as test_manager_loop.py) for the swarm
subprocess itself -- there is no real `swarm` binary in this sandbox --
but every worktree operation is real `git worktree` against a throwaway
repo. This isolates the two things actually worth testing here
separately: "does ManagerLoop use worktree_guard correctly" (this file,
real git + fake subprocess) and "does worktree_guard's git logic work"
(test_worktree_guard.py, already real end-to-end).
"""
import subprocess
from pathlib import Path

import pytest

from opencode_crack.orchestrator import worktree_guard
from opencode_crack.orchestrator.manager_loop import ManagerLoop
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_runtime import SwarmResult
from opencode_crack.runtime import control_db


class FakeRuntime:
    """Records exactly what ManagerLoop passed to `run()`, without
    needing a real `swarm` binary -- lets these tests assert on the
    actual handoff (cwd, etc.) rather than trusting it happened."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.calls: list[dict] = []

    def run(self, config_path, **kwargs):
        self.calls.append({"config_path": Path(config_path), "cwd": kwargs.get("cwd")})
        return SwarmResult(swarm_id="sw_test", status="completed",
                            events=[{"type": "agent-settled", "agent": "worker"}])

    def ingest_events(self, result):
        return len(result.events)


def _git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed in {cwd}: {result.stderr}"
    return result.stdout


@pytest.fixture
def isolated_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "a@a.com"], cwd=repo)
    _git(["config", "user.name", "a"], cwd=repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "init"], cwd=repo)
    return repo


def profiles():
    return (
        AgentProfile("manager", "manager", "openai/gpt-4o-mini", ["read"]),
        AgentProfile("worker", "worker", "openai/gpt-4o-mini", ["read", "edit", "write", "bash"]),
        AgentProfile("tester", "tester", "openai/gpt-4o-mini", ["read", "bash"]),
    )


class FakeTask:
    title = "bounded test task"


def _loop(tmp_path, isolated_repo, monkeypatch, task_id="D-TEST"):
    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda tid: FakeTask())
    runtime = FakeRuntime(tmp_path / "control.db")
    loop = ManagerLoop(runtime=runtime, db_path=runtime.db_path, repo_root=isolated_repo)
    return loop, runtime


# --- 1 & 2: code-mutating task receives an isolated worktree before ------
# execution, and the launched runtime uses it as cwd -----------------------

def test_task_receives_isolated_worktree_before_execution(tmp_path, isolated_repo, monkeypatch):
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)

    assert result.lease_acquired is True
    assert result.error is None
    assert result.worktree_path is not None
    assert result.worktree_path.exists()
    assert worktree_guard.is_isolated_worktree(cwd=result.worktree_path)

    # The exact handoff: FakeRuntime.run() must have been called with
    # cwd == the resolved lane, not left as None (which would mean
    # SwarmRuntime.cwd's own default -- Path.cwd(), the shared checkout).
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["cwd"] == result.worktree_path


def test_worktree_branch_matches_task_and_session(tmp_path, isolated_repo, monkeypatch):
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)

    branch_result = subprocess.run(
        ["git", "-C", str(result.worktree_path), "branch", "--show-current"],
        capture_output=True, text=True,
    )
    assert branch_result.stdout.strip() == f"swarm/lane/{worker.agent_id}/D-TEST"


# --- 3: invalid/missing provisioning blocks execution ---------------------

def test_provisioning_failure_blocks_execution_entirely(tmp_path, isolated_repo, monkeypatch):
    """Pre-create a directory at the exact path a lane would resolve to,
    but make it NOT a git worktree -- resolve_lane_worktree must refuse,
    and run_once must return an error without acquiring a lease or
    invoking the runtime at all."""
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    bogus_path = isolated_repo / ".swarm-worktrees-local" / worker.agent_id / "D-TEST"
    bogus_path.mkdir(parents=True)
    (bogus_path / "not_a_worktree.txt").write_text("junk\n", encoding="utf-8")

    result = loop.run_once("D-TEST", manager, worker, tester)

    assert result.lease_acquired is False
    assert "not established" in result.error
    assert runtime.calls == []  # the swarm subprocess must never have been invoked
    # the lease must genuinely never have been acquired -- confirm a
    # different agent can still acquire it immediately after
    control_db.init_db(runtime.db_path)  # run_once never reached its own init_db/register_agent calls
    control_db.register_agent(AgentProfile("someone-else", "worker", "model"), runtime.db_path)
    assert control_db.acquire_lease("D-TEST", "someone-else", db_path=runtime.db_path)


def test_branch_mismatch_blocks_execution(tmp_path, isolated_repo, monkeypatch):
    """A worktree already exists at the resolved path, on the WRONG
    branch for this task/session -- must refuse rather than silently
    reusing a stale/mismatched lane."""
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    wrong_path = isolated_repo / ".swarm-worktrees-local" / worker.agent_id / "D-TEST"
    wrong_path.parent.mkdir(parents=True)
    _git(["worktree", "add", "-q", "-b", "some/other/branch", str(wrong_path)], cwd=isolated_repo)

    result = loop.run_once("D-TEST", manager, worker, tester)

    assert result.lease_acquired is False
    assert "not established" in result.error
    assert runtime.calls == []


def test_isolation_can_be_explicitly_disabled_for_pre_validated_callers(tmp_path, isolated_repo, monkeypatch):
    """require_isolation=False is the documented opt-out for a caller
    that already established its own isolation guarantee -- confirm it
    actually skips provisioning (and thus doesn't require a git repo at
    repo_root at all) rather than silently still enforcing it."""
    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda tid: FakeTask())
    runtime = FakeRuntime(tmp_path / "control.db")
    # repo_root deliberately points somewhere that ISN'T a git repo --
    # if isolation were still being enforced this would fail loudly.
    non_repo = tmp_path / "not-a-git-repo"
    non_repo.mkdir()
    loop = ManagerLoop(runtime=runtime, db_path=runtime.db_path, repo_root=non_repo)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester, require_isolation=False)

    assert result.lease_acquired is True
    assert result.error is None
    assert result.worktree_path is None
    assert runtime.calls[0]["cwd"] is None


# --- 4: own-worktree contamination is still detected -----------------------

def test_own_worktree_contamination_blocks_a_second_run(tmp_path, isolated_repo, monkeypatch):
    """After a lane exists (from a prior run), unexpected dirty state
    inside it must block a subsequent attempt to use it -- isolation
    must never become blanket trust of a pre-existing lane directory."""
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    first = loop.run_once("D-TEST", manager, worker, tester)
    assert first.error is None
    control_db.release_lease("D-TEST", runtime.db_path)

    # Simulate contamination discovered inside the (still-existing) lane
    # before a second attempt.
    (first.worktree_path / "unexpected.txt").write_text("uh oh\n", encoding="utf-8")

    second = loop.run_once("D-TEST", manager, worker, tester)
    assert second.lease_acquired is False
    assert "not clean" in second.error or "not established" in second.error
    assert len(runtime.calls) == 1  # no second execution happened


# --- 5: two simultaneous lanes remain isolated ------------------------------

def test_two_simultaneous_lanes_isolated(tmp_path, isolated_repo, monkeypatch):
    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda tid: FakeTask())
    runtime_a = FakeRuntime(tmp_path / "control_a.db")
    runtime_b = FakeRuntime(tmp_path / "control_b.db")
    loop_a = ManagerLoop(runtime=runtime_a, db_path=runtime_a.db_path, repo_root=isolated_repo)
    loop_b = ManagerLoop(runtime=runtime_b, db_path=runtime_b.db_path, repo_root=isolated_repo)

    manager, worker_a, tester = profiles()
    worker_b = AgentProfile("worker-b", "worker", "openai/gpt-4o-mini", ["read", "edit", "write", "bash"])

    result_a = loop_a.run_once("D-LANE-A", manager, worker_a, tester)
    result_b = loop_b.run_once("D-LANE-B", manager, worker_b, tester)

    assert result_a.worktree_path != result_b.worktree_path
    assert result_a.worktree_path.exists() and result_b.worktree_path.exists()

    (result_a.worktree_path / "a_only.txt").write_text("a\n", encoding="utf-8")
    (result_b.worktree_path / "b_only.txt").write_text("b\n", encoding="utf-8")

    contamination_a = worktree_guard.check_contamination(cwd=result_a.worktree_path)
    contamination_b = worktree_guard.check_contamination(cwd=result_b.worktree_path)
    assert contamination_a.unexpected_files == ["a_only.txt"]
    assert contamination_b.unexpected_files == ["b_only.txt"]


# --- 6: dirty shared checkout does not falsely contaminate an isolated lane -

def test_dirty_shared_checkout_does_not_block_isolated_lane(tmp_path, isolated_repo, monkeypatch):
    (isolated_repo / "unrelated_wip.py").write_text("# someone else's mess\n", encoding="utf-8")

    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)

    assert result.error is None
    assert result.lease_acquired is True
    assert len(runtime.calls) == 1


# --- 7: task/lane/worktree/branch identity remains traceable ---------------

def test_lifecycle_evidence_records_task_lane_worktree_branch(tmp_path, isolated_repo, monkeypatch):
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)

    with control_db._connect(runtime.db_path) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE task_id=? AND event_type=?",
            ("D-TEST", "lane_provisioned"),
        ).fetchall()

    assert len(rows) == 1
    import json as _json
    payload = _json.loads(rows[0]["payload"])
    assert payload["worktree_path"] == str(result.worktree_path)
    assert payload["branch"] == f"swarm/lane/{worker.agent_id}/D-TEST"
    assert payload["session_id"] == worker.agent_id


# --- 8: cleanup cannot remove active or unmerged work -----------------------

def test_cleanup_cannot_remove_the_lane_this_task_just_used(tmp_path, isolated_repo, monkeypatch):
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)

    # A freshly branched, untouched lane is trivially "merged" (its tip
    # IS main's tip) -- that's correct git semantics, not something to
    # guard against. The real risk is a lane that has actual unmerged
    # work in it, which is the normal state during/after execution:
    # simulate the worker having committed real progress in its lane.
    (result.worktree_path / "work_in_progress.py").write_text("# real progress\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=result.worktree_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=result.worktree_path, check=True)

    with pytest.raises(worktree_guard.UnsafeCleanupError, match="not merged"):
        worktree_guard.safe_cleanup_worktree(result.worktree_path, main_repo_cwd=isolated_repo)
    assert result.worktree_path.exists()


# --- 9: alternate/retry/recovery paths cannot bypass the guard -------------

def test_retry_after_crash_reuses_the_same_validated_lane(tmp_path, isolated_repo, monkeypatch):
    """A second ManagerLoop instance (simulating a process restart after
    a crash) for the SAME task/session must resolve to the SAME lane and
    still pass validation, rather than either failing permanently or
    silently creating a second, divergent worktree."""
    monkeypatch.setattr("opencode_crack.orchestrator.manager_loop.get_task", lambda tid: FakeTask())
    manager, worker, tester = profiles()

    runtime_1 = FakeRuntime(tmp_path / "control.db")
    loop_1 = ManagerLoop(runtime=runtime_1, db_path=runtime_1.db_path, repo_root=isolated_repo)
    result_1 = loop_1.run_once("D-TEST", manager, worker, tester)
    control_db.release_lease("D-TEST", runtime_1.db_path)

    # Simulate a fresh process: new ManagerLoop, new FakeRuntime instance.
    runtime_2 = FakeRuntime(tmp_path / "control.db")
    loop_2 = ManagerLoop(runtime=runtime_2, db_path=runtime_2.db_path, repo_root=isolated_repo)
    result_2 = loop_2.run_once("D-TEST", manager, worker, tester)

    assert result_2.error is None
    assert result_2.worktree_path == result_1.worktree_path  # same lane, not a duplicate
    worktrees = worktree_guard.list_worktrees(cwd=isolated_repo)
    lane_paths = [w.path for w in worktrees if not w.is_main]
    assert len(lane_paths) == 1  # no divergent second worktree was created


def test_start_server_requires_explicit_cwd():
    """D-290: the second execution entry point (AgentRuntime.start_server,
    independent of ManagerLoop/SwarmRuntime) must not have a silent
    fallback to the shared checkout. Confirmed by signature -- calling it
    with no cwd must be a TypeError, not a runtime default."""
    from opencode_crack.runtime.agent_runtime import AgentRuntime
    with pytest.raises(TypeError):
        AgentRuntime.start_server()


# --- CN-003 disposition: fixed here, since it's directly relevant to -------
# validate_lane_worktree's own robustness --------------------------------

def test_validate_lane_worktree_handles_deleted_worktree_directory(tmp_path, isolated_repo, monkeypatch):
    loop, runtime = _loop(tmp_path, isolated_repo, monkeypatch)
    manager, worker, tester = profiles()

    result = loop.run_once("D-TEST", manager, worker, tester)
    control_db.release_lease("D-TEST", runtime.db_path)

    import shutil
    shutil.rmtree(result.worktree_path)

    # CN-003 (D-289 finding): this must raise the documented error type,
    # not a raw FileNotFoundError from subprocess.
    lane = worktree_guard.LaneWorktree(
        task_id="D-TEST", session_id=worker.agent_id,
        path=result.worktree_path, branch=f"swarm/lane/{worker.agent_id}/D-TEST",
    )
    with pytest.raises(worktree_guard.WorktreeProvisioningError):
        worktree_guard.validate_lane_worktree(lane, main_repo_cwd=isolated_repo)

    # And run_once itself must surface this as a normal blocked-execution
    # result, not crash.
    third = loop.run_once("D-TEST", manager, worker, tester)
    assert third.lease_acquired is False
    assert "not established" in third.error
