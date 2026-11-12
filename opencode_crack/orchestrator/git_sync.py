"""Safe, explicit synchronization for the current Git branch.

Unlike a bare ``git pull --rebase``, this helper resolves one configured
upstream, fetches that upstream's remote, and rebases onto the resolved ref.
It refuses to run with a dirty worktree or an unfinished Git operation so
it never stashes, resets, checks out, or guesses a target branch.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class GitSyncError(RuntimeError):
    """Raised when branch synchronization cannot proceed safely."""


@dataclass(frozen=True)
class GitSyncResult:
    branch: str
    upstream: str


_IN_PROGRESS_GIT_PATHS = (
    "rebase-apply",
    "rebase-merge",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "sequencer",
    "BISECT_LOG",
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or result.stderr or "").strip()


def _require_success(result: subprocess.CompletedProcess[str], operation: str) -> str:
    if result.returncode != 0:
        detail = _output(result) or f"git exited with status {result.returncode}"
        raise GitSyncError(f"{operation} failed: {detail}")
    return (result.stdout or "").strip()


def _resolve_repo(repo_path: Optional[Path]) -> Path:
    requested = Path(repo_path) if repo_path is not None else Path(__file__).resolve().parents[2]
    try:
        requested = requested.resolve(strict=True)
    except OSError as exc:
        raise GitSyncError(f"Git repository path does not exist: {requested}") from exc
    if not requested.is_dir():
        raise GitSyncError(f"Git repository path is not a directory: {requested}")
    root = _require_success(_run_git(requested, "rev-parse", "--show-toplevel"), "locating repository root")
    return Path(root).resolve()


def _current_branch(repo: Path) -> str:
    result = _run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode != 0:
        raise GitSyncError("cannot sync a detached HEAD; check out the intended branch first")
    branch = result.stdout.strip()
    if not branch:
        raise GitSyncError("Git did not report the current branch; synchronization stopped")
    return branch


def _config_values(repo: Path, key: str) -> list[str]:
    result = _run_git(repo, "config", "--null", "--get-all", key)
    if result.returncode == 1 and not result.stdout:
        return []
    if result.returncode != 0:
        raise GitSyncError(f"could not read Git setting {key!r}: {_output(result)}")
    return [value for value in result.stdout.split("\0") if value]


def _one_upstream(repo: Path, branch: str) -> tuple[str, str, str]:
    prefix = f"branch.{branch}"
    remotes = _config_values(repo, f"{prefix}.remote")
    merges = _config_values(repo, f"{prefix}.merge")
    if len(remotes) != 1 or len(merges) != 1:
        raise GitSyncError(
            f"branch {branch!r} must have exactly one configured upstream "
            f"(found {len(remotes)} remote value(s), {len(merges)} merge value(s)); "
            "set one upstream explicitly, then retry"
        )

    upstream_result = _run_git(
        repo, "rev-parse", "--symbolic-full-name", "--verify", "@{upstream}"
    )
    if upstream_result.returncode != 0:
        raise GitSyncError(
            f"branch {branch!r} has no resolvable upstream: {_output(upstream_result)}"
        )
    refs = [line.strip() for line in upstream_result.stdout.splitlines() if line.strip()]
    if len(refs) != 1:
        raise GitSyncError(
            f"branch {branch!r} resolves to {len(refs)} upstream refs; "
            "configure exactly one upstream"
        )
    return remotes[0], merges[0], refs[0]


def _check_no_operation_in_progress(repo: Path) -> None:
    for git_path in _IN_PROGRESS_GIT_PATHS:
        result = _run_git(repo, "rev-parse", "--git-path", git_path)
        if result.returncode != 0:
            raise GitSyncError(f"could not inspect Git operation state: {_output(result)}")
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            raise GitSyncError(
                f"Git operation state {git_path!r} is present; resolve or abort it manually before syncing"
            )


def _check_clean_worktree(repo: Path) -> None:
    result = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise GitSyncError(f"could not inspect the working tree: {_output(result)}")
    if result.stdout.strip():
        raise GitSyncError(
            "working tree or index is not clean; synchronization stopped without "
            "stashing or changing files. `git status --short` reported:\n"
            f"{result.stdout.strip()}"
        )


def sync_current_branch(repo_path: Optional[Path] = None) -> GitSyncResult:
    """Fetch the current branch's unique upstream and explicitly rebase onto it.

    The function never chooses a default such as ``origin/main``. Callers that
    require a canonical target must use their explicitly documented target
    path instead of this current-upstream helper.
    """
    repo = _resolve_repo(repo_path)
    _check_no_operation_in_progress(repo)
    _check_clean_worktree(repo)

    branch = _current_branch(repo)
    remote, _merge_ref, upstream = _one_upstream(repo, branch)
    if remote != ".":
        fetch = _run_git(repo, "fetch", "--no-tags", remote)
        _require_success(fetch, f"fetching upstream remote {remote!r}")

    _check_no_operation_in_progress(repo)
    _check_clean_worktree(repo)
    if _current_branch(repo) != branch:
        raise GitSyncError("current branch changed during fetch; synchronization stopped")
    fetched_remote, fetched_merge, fetched_upstream = _one_upstream(repo, branch)
    if (fetched_remote, fetched_merge, fetched_upstream) != (remote, _merge_ref, upstream):
        raise GitSyncError("branch upstream changed during fetch; synchronization stopped")

    target = _require_success(
        _run_git(repo, "rev-parse", "--verify", f"{upstream}^{{commit}}"),
        f"resolving upstream {upstream!r}",
    )
    rebase = _run_git(repo, "rebase", target)
    _require_success(rebase, f"rebasing {branch!r} onto {upstream}")

    if _current_branch(repo) != branch:
        raise GitSyncError("current branch changed during rebase; inspect repository state before continuing")
    _check_no_operation_in_progress(repo)
    _check_clean_worktree(repo)
    return GitSyncResult(branch=branch, upstream=upstream)
