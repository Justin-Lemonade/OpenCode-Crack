"""Tests for src/orchestrator/worktree_guard.py.

These are REAL git-worktree tests -- each test creates a throwaway git
repo under tmp_path, uses actual `git worktree add`, and asserts on
actual `git status`/`git worktree list` output. No mocking of git: the
isolation guarantee under test is git's own, and opencode-swarm (not
available in this sandbox -- see delegated_tasks/D-288.md) builds on
exactly this same primitive, so this is a faithful test of the
underlying mechanism, not a simulation of it.
"""
import subprocess
from pathlib import Path

import pytest

from opencode_crack.orchestrator import worktree_guard as wg


def _git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed in {cwd}: {result.stderr}"
    return result.stdout


@pytest.fixture
def main_repo(tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "a@a.com"], cwd=repo)
    _git(["config", "user.name", "a"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "init"], cwd=repo)
    return repo


def _add_worktree(main_repo, subdir_name, branch_name):
    wt_path = main_repo.parent / "worktrees" / subdir_name
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-b", branch_name, str(wt_path)], cwd=main_repo)
    return wt_path


# --- is_isolated_worktree ---------------------------------------------

def test_main_checkout_is_not_isolated(main_repo):
    assert wg.is_isolated_worktree(cwd=main_repo) is False


def test_linked_worktree_is_isolated(main_repo):
    wt = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    assert wg.is_isolated_worktree(cwd=wt) is True


def test_not_a_repo_raises(tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    with pytest.raises(wg.NotAGitRepoError):
        wg.is_isolated_worktree(cwd=empty)


# --- list_worktrees ------------------------------------------------------

def test_list_worktrees_includes_main_first(main_repo):
    worktrees = wg.list_worktrees(cwd=main_repo)
    assert len(worktrees) == 1
    assert worktrees[0].is_main is True
    assert worktrees[0].path.resolve() == main_repo.resolve()


def test_list_worktrees_after_adding_lanes(main_repo):
    _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    _add_worktree(main_repo, "lane-b", "swarm/lane/s1/D-TEST-B")
    worktrees = wg.list_worktrees(cwd=main_repo)
    assert len(worktrees) == 3
    assert worktrees[0].is_main is True
    branches = {w.branch for w in worktrees}
    assert "swarm/lane/s1/D-TEST-A" in branches
    assert "swarm/lane/s1/D-TEST-B" in branches


# --- Test A: two independent agents ---------------------------------------

def test_two_lanes_different_paths_and_branches(main_repo):
    wt_a = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    wt_b = _add_worktree(main_repo, "lane-b", "swarm/lane/s1/D-TEST-B")
    assert wt_a.resolve() != wt_b.resolve()

    worktrees = {w.path.resolve(): w.branch for w in wg.list_worktrees(cwd=main_repo)}
    assert worktrees[wt_a.resolve()] == "swarm/lane/s1/D-TEST-A"
    assert worktrees[wt_b.resolve()] == "swarm/lane/s1/D-TEST-B"


def test_changes_in_lane_a_invisible_to_lane_b(main_repo):
    wt_a = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    wt_b = _add_worktree(main_repo, "lane-b", "swarm/lane/s1/D-TEST-B")

    (wt_a / "only_in_a.txt").write_text("agent A work\n", encoding="utf-8")
    (wt_b / "only_in_b.txt").write_text("agent B work\n", encoding="utf-8")

    result_a = wg.check_contamination(cwd=wt_a)
    result_b = wg.check_contamination(cwd=wt_b)

    assert result_a.unexpected_files == ["only_in_a.txt"]
    assert result_b.unexpected_files == ["only_in_b.txt"]
    assert "only_in_b.txt" not in result_a.unexpected_files
    assert "only_in_a.txt" not in result_b.unexpected_files


def test_both_lanes_can_commit_independently(main_repo):
    wt_a = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    wt_b = _add_worktree(main_repo, "lane-b", "swarm/lane/s1/D-TEST-B")

    (wt_a / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt_a)
    _git(["commit", "-q", "-m", "A's work"], cwd=wt_a)

    (wt_b / "b.txt").write_text("b\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt_b)
    _git(["commit", "-q", "-m", "B's work"], cwd=wt_b)

    assert wg.check_contamination(cwd=wt_a).clean is True
    assert wg.check_contamination(cwd=wt_b).clean is True


def test_dirty_shared_checkout_does_not_falsely_flag_isolated_lane(main_repo):
    # This is the exact bug from the task report: unrelated dirty state
    # in the MAIN checkout must not appear as contamination inside an
    # isolated lane.
    (main_repo / "unrelated_wip.py").write_text("# someone else's WIP\n", encoding="utf-8")
    wt_a = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")

    result_main = wg.check_contamination(cwd=main_repo)
    result_a = wg.check_contamination(cwd=wt_a)

    assert result_main.clean is False
    assert result_main.in_isolated_worktree is False
    assert result_a.clean is True
    assert result_a.in_isolated_worktree is True


# --- Test B: multiple concurrent lanes -------------------------------

def test_five_concurrent_lanes_no_collision(main_repo):
    lanes = [_add_worktree(main_repo, f"lane-{i}", f"swarm/lane/s1/D-TEST-{i}") for i in range(5)]
    paths = {p.resolve() for p in lanes}
    assert len(paths) == 5  # no path collision

    worktrees = wg.list_worktrees(cwd=main_repo)
    branches = [w.branch for w in worktrees if not w.is_main]
    assert len(branches) == len(set(branches)) == 5  # no branch collision

    for i, lane in enumerate(lanes):
        (lane / f"file_{i}.txt").write_text(f"lane {i}\n", encoding="utf-8")

    for i, lane in enumerate(lanes):
        result = wg.check_contamination(cwd=lane)
        assert result.unexpected_files == [f"file_{i}.txt"]  # no cross-contamination


# --- Test C: actual contamination inside an agent's own worktree -----

def test_real_contamination_inside_own_worktree_still_detected(main_repo):
    wt = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    # Simulate an unexpected modification appearing in the agent's OWN
    # worktree -- this must still trip the check; isolation must never
    # become blanket trust.
    (wt / "unexpected.txt").write_text("should not be silently ignored\n", encoding="utf-8")

    result = wg.check_contamination(cwd=wt)
    assert result.clean is False
    assert result.in_isolated_worktree is True
    assert "unexpected.txt" in result.unexpected_files
    assert "cannot be another lane's files" in result.note


# --- Test D: repeated provisioning ------------------------------------

def test_repeated_provisioning_no_collision_after_cleanup(main_repo):
    wt1 = _add_worktree(main_repo, "batch1-lane-a", "swarm/lane/s1/D-TEST-A")
    (wt1 / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt1)
    _git(["commit", "-q", "-m", "batch1 work"], cwd=wt1)
    _git(["checkout", "-q", "main"], cwd=main_repo)
    _git(["merge", "-q", "--no-ff", "swarm/lane/s1/D-TEST-A"], cwd=main_repo)

    msg = wg.safe_cleanup_worktree(wt1, main_repo_cwd=main_repo)
    assert "Removed" in msg
    assert not wt1.exists()

    # Second batch reusing a similar (but not identical) lane name must
    # not collide with the now-removed first batch's artifacts.
    wt2 = _add_worktree(main_repo, "batch2-lane-a", "swarm/lane/s2/D-TEST-A")
    assert wt2.exists()
    assert wg.check_contamination(cwd=wt2).clean is True


# --- safe_cleanup_worktree --------------------------------------------

def test_cleanup_refuses_unmerged_branch(main_repo):
    wt = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt)
    _git(["commit", "-q", "-m", "not yet merged"], cwd=wt)

    with pytest.raises(wg.UnsafeCleanupError, match="not merged"):
        wg.safe_cleanup_worktree(wt, main_repo_cwd=main_repo)
    assert wt.exists()  # nothing was removed


def test_cleanup_refuses_dirty_worktree(main_repo):
    wt = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    (wt / "uncommitted.txt").write_text("wip\n", encoding="utf-8")

    with pytest.raises(wg.UnsafeCleanupError, match="uncommitted changes"):
        wg.safe_cleanup_worktree(wt, main_repo_cwd=main_repo)
    assert wt.exists()


def test_cleanup_refuses_main_worktree(main_repo):
    with pytest.raises(wg.UnsafeCleanupError, match="main worktree"):
        wg.safe_cleanup_worktree(main_repo, main_repo_cwd=main_repo)


def test_cleanup_refuses_unknown_path(main_repo, tmp_path):
    unrelated = tmp_path / "not-a-worktree-of-this-repo"
    unrelated.mkdir()
    with pytest.raises(wg.UnsafeCleanupError, match="not a known worktree"):
        wg.safe_cleanup_worktree(unrelated, main_repo_cwd=main_repo)


def test_cleanup_succeeds_for_clean_merged_worktree(main_repo):
    wt = _add_worktree(main_repo, "lane-a", "swarm/lane/s1/D-TEST-A")
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt)
    _git(["commit", "-q", "-m", "done"], cwd=wt)
    _git(["checkout", "-q", "main"], cwd=main_repo)
    _git(["merge", "-q", "--no-ff", "swarm/lane/s1/D-TEST-A"], cwd=main_repo)

    msg = wg.safe_cleanup_worktree(wt, main_repo_cwd=main_repo)
    assert "clean and merged" in msg
    assert not wt.exists()
    remaining = wg.list_worktrees(cwd=main_repo)
    assert len(remaining) == 1  # only main left
