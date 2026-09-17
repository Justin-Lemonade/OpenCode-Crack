"""Worktree isolation guard: the git-native primitive underneath the
`opencode-swarm` plugin's worktree isolation feature.

Why this exists (D-288, see delegated_tasks/D-288.md): agents kept
hitting false "shared-worktree contamination" stops when multiple
concurrent lanes were active. The root cause the task investigation
(D-266-D-270, all Windows-only via opencode-swarm) narrowed to a
protocol-ordering defect: `find_work.md`'s clean-tree check ran without
distinguishing "I'm in my own isolated worktree" (safe to ignore dirty
state elsewhere) from "I'm in the shared checkout" (dirty state from
anyone else is real contamination, stop).

`opencode-swarm` (a third-party OpenCode plugin, validated only on
Windows in D-268's report -- PARTIAL SUCCESS, no automatic cleanup, full
coder/reviewer/test_engineer flow not validated) is not available in
this sandbox and can't be. But the isolation *mechanism* it relies on is
plain `git worktree` -- a git-core feature, fully available and testable
here. This module implements the primitive at that layer:

- detect whether the current process is running inside a linked
  (isolated) worktree vs. the main/shared checkout
- run the clean-tree check scoped correctly either way
- enumerate sibling worktrees for a coordinator
- a cleanup function with real safety checks (merged + clean only)

`find_work.md` step 0.75 now instructs agents to use this reasoning
(see the fix in that file) even when the actual worktree was provisioned
by opencode-swarm rather than by this module directly -- the git-level
guarantees are identical either way, since opencode-swarm calls
`git worktree add` under the hood (confirmed by D-268's report: branches
named `swarm/lane/<session>/<task>` under `.swarm-worktrees-local/`).
"""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _run_git(args: list, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@dataclass
class WorktreeInfo:
    path: Path
    branch: Optional[str]
    is_main: bool
    is_bare: bool = False
    is_locked: bool = False


@dataclass
class ContaminationResult:
    clean: bool
    in_isolated_worktree: bool
    unexpected_files: list = field(default_factory=list)
    note: str = ""


class NotAGitRepoError(RuntimeError):
    pass


def _require_repo_root(cwd: Optional[Path]) -> Path:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Not inside a git repository (cwd={cwd or Path.cwd()}): {result.stderr.strip()}"
        )
    return Path(result.stdout.strip())


def is_isolated_worktree(cwd: Optional[Path] = None) -> bool:
    """True if the current working directory is inside a *linked* git
    worktree (created via `git worktree add`), False if it's the
    main/original checkout.

    Distinguishing mechanism: for the main checkout, `git rev-parse
    --git-dir` resolves to `.git` (a directory). For a linked worktree,
    it resolves to a path under `<main-git-dir>/worktrees/<name>` (a
    directory containing a `gitdir` file pointing back). Checking for
    `/worktrees/` in the resolved absolute git-dir path is the standard,
    documented way to tell them apart (see `git-worktree(1)`) and does
    not depend on any opencode-swarm-specific naming convention.
    """
    result = _run_git(["rev-parse", "--absolute-git-dir"], cwd=cwd)
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Not inside a git repository (cwd={cwd or Path.cwd()}): {result.stderr.strip()}"
        )
    git_dir = result.stdout.strip()
    # POSIX and Windows paths both use "worktrees" as the literal segment
    # name; normalize backslashes so this works on either.
    return "worktrees" in Path(git_dir.replace("\\", "/")).parts


def list_worktrees(cwd: Optional[Path] = None) -> list:
    """Enumerate every worktree (main + linked) attached to this repo,
    via `git worktree list --porcelain` -- the authoritative source,
    not a guess based on directory naming conventions like
    `.swarm-worktrees-local/<session>/<task>`."""
    result = _run_git(["worktree", "list", "--porcelain"], cwd=cwd)
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Not inside a git repository (cwd={cwd or Path.cwd()}): {result.stderr.strip()}"
        )

    worktrees = []
    current: dict = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):].strip()}
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip().removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["branch"] = None
        elif line == "locked":
            current["locked"] = True
    if current:
        worktrees.append(current)

    result_list = []
    for i, w in enumerate(worktrees):
        result_list.append(WorktreeInfo(
            path=Path(w["path"]),
            branch=w.get("branch"),
            is_main=(i == 0),  # git always lists the main worktree first
            is_bare=w.get("bare", False),
            is_locked=w.get("locked", False),
        ))
    return result_list


def check_contamination(cwd: Optional[Path] = None) -> ContaminationResult:
    """The corrected clean-tree check: scoped to the CURRENT worktree
    only (which `git status` already does natively -- git worktrees are
    isolated from each other for status purposes by design), plus an
    explicit signal for whether the caller is even in an isolated
    worktree at all.

    This is the fix for the false-positive root cause: the historical
    bug wasn't that git's dirty-check was too broad, it's that agents
    were sometimes running the check from the *shared/main* checkout
    (either because isolation provisioning silently failed, or because
    `find_work` ran before provisioning completed -- see D-288's report,
    'Root Cause'). In the shared checkout, another lane's uncommitted
    files legitimately show up as dirty, and that IS real contamination
    risk, so the check must still stop there. Inside a genuine linked
    worktree, git-level isolation means unrelated lanes' files simply
    can't appear here at all -- so a non-empty result inside an isolated
    worktree is unambiguously the agent's own doing (or Test C's
    intentional-contamination case), never another lane's.
    """
    target = Path(cwd) if cwd is not None else Path.cwd()
    if not target.exists():
        # D-289/CN-003: a worktree git still has metadata for but whose
        # directory was deleted (crash mid-cleanup, manual `rm -rf`
        # instead of `git worktree remove`) must fail the same documented
        # way as every other validation failure here, not crash with a
        # raw subprocess FileNotFoundError.
        raise NotAGitRepoError(f"Worktree path does not exist on disk: {target}")

    isolated = is_isolated_worktree(cwd=cwd)
    result = _run_git(["status", "--porcelain"], cwd=cwd)
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Not inside a git repository (cwd={cwd or Path.cwd()}): {result.stderr.strip()}"
        )
    files = [line[3:] for line in result.stdout.splitlines() if line.strip()]

    if not files:
        return ContaminationResult(clean=True, in_isolated_worktree=isolated)

    if isolated:
        note = (
            "Dirty state found INSIDE your own isolated worktree. Since "
            "worktrees are git-isolated from each other, this cannot be "
            "another lane's files -- it's either your own uncommitted "
            "progress or genuine contamination of this worktree specifically. "
            "Do not attribute it to 'another agent's lane'; investigate directly."
        )
    else:
        note = (
            "You are in the SHARED/main checkout, not an isolated worktree. "
            "Dirty state here may belong to another concurrent agent using the "
            "same checkout. Per find_work.md 0.75: do not modify, stash, "
            "reset, clean, checkout, or commit it -- stop and report."
        )

    return ContaminationResult(clean=False, in_isolated_worktree=isolated, unexpected_files=files, note=note)


class WorktreeProvisioningError(RuntimeError):
    """Raised when a code-mutating task cannot be given a validated
    isolated worktree. Callers (ManagerLoop, per D-290) must treat this
    as a hard stop -- never fall back to running from the shared
    checkout because provisioning failed."""
    pass


class CrossLaneMutationError(RuntimeError):
    """Raised when a board publish is about to commit staged changes
    that do not belong to it (D-321/CN-007).

    The shared checkout is used by concurrent lanes. ``git commit``
    without a pathspec commits the *entire index*, so a publish that
    runs while another lane has files staged would silently sweep those
    files into its own commit -- from the other lane's perspective its
    work "vanishes" (the D-314 incident: staged files gone, worktree
    clean). Raising here fails closed: nothing is committed, nothing is
    pushed, and the foreign staged state is left untouched for its owner
    to commit. The caller must stop and report, never unstage/reset the
    foreign files (find_work.md 0.75).
    """
    pass


def _normalize_repo_path(path: str | Path) -> str:
    """Normalize a repo-relative path for index comparison: forward
    slashes, no leading ``./``, OS case-folding (Windows checkouts are
    case-insensitive)."""
    import os

    text = Path(path).as_posix()
    if text.startswith("./"):
        text = text[2:]
    return os.path.normcase(text)


def unexpected_staged_paths(
    expected_paths: list[str | Path],
    cwd: Optional[Path] = None,
) -> list[str]:
    """List staged paths that are NOT in ``expected_paths``.

    Read-only: runs ``git diff --cached --name-only -z`` and compares
    against the normalized expected set. Returns the unexpected staged
    paths (repo-relative, POSIX form), or ``[]`` when the index contains
    only expected paths (or nothing at all).

    Raises NotAGitRepoError outside a git repository.
    """
    result = _run_git(["diff", "--cached", "--name-only", "-z"], cwd=cwd)
    if result.returncode != 0:
        raise NotAGitRepoError(
            f"Could not read staged changes (cwd={cwd or Path.cwd()}): {result.stderr.strip()}"
        )
    expected = {_normalize_repo_path(p) for p in expected_paths}
    staged = [p for p in result.stdout.split("\0") if p]
    return [p for p in staged if _normalize_repo_path(p) not in expected]


def assert_expected_staged_only(
    expected_paths: list[str | Path],
    cwd: Optional[Path] = None,
) -> None:
    """Fail closed if the index holds anyone else's staged changes.

    Raises CrossLaneMutationError listing the foreign paths. Mutates
    nothing: the index is left exactly as found so the owning lane can
    still commit its own work.
    """
    unexpected = unexpected_staged_paths(expected_paths, cwd=cwd)
    if unexpected:
        raise CrossLaneMutationError(
            "Refusing to commit: the index contains staged changes that do "
            f"not belong to this publish: {sorted(unexpected)}. Another "
            "concurrent lane staged these files -- committing now would sweep "
            "them into this commit and make them vanish from the owning "
            "lane's perspective (CN-007/D-314 pattern). Stop and report; do "
            "not unstage, reset, stash, or commit them."
        )


@dataclass(frozen=True)
class LaneWorktree:
    task_id: str
    session_id: str
    path: Path
    branch: str


def _lane_branch_name(session_id: str, task_id: str) -> str:
    # Mirrors opencode-swarm's own naming convention exactly (confirmed
    # in reports/D-268_report.md: branches named
    # `swarm/lane/<session>/<task>`) rather than inventing a parallel
    # scheme -- so a lane resolved here and one provisioned by the
    # external plugin for the same (session, task) land on the same
    # branch name and can be recognized as the same lane.
    return f"swarm/lane/{session_id}/{task_id}"


def _lane_path(main_repo_cwd: Path, session_id: str, task_id: str, worktree_base: Optional[Path]) -> Path:
    # Mirrors opencode-swarm's `.swarm-worktrees-local/<session>/<task>`
    # directory convention (.opencode/opencode-swarm.json's
    # `worktree_dir`), same reasoning as the branch name above.
    base = worktree_base or (main_repo_cwd / ".swarm-worktrees-local")
    return base / session_id / task_id


def resolve_lane_worktree(
    task_id: str,
    session_id: str,
    main_repo_cwd: Optional[Path] = None,
    worktree_base: Optional[Path] = None,
) -> LaneWorktree:
    """Get-or-create the isolated worktree for (task_id, session_id).

    This is the enforcement entry point for D-290: a code-mutating
    execution path calls this (and then `validate_lane_worktree`) instead
    of using `Path.cwd()`/the shared checkout directly. Idempotent: if the
    lane already exists (e.g. this is a retry after a crash) and is
    consistent, it's reused rather than recreated; if the path exists but
    is NOT the git worktree this task/session expects, this raises rather
    than silently reusing or overwriting unrelated directory content.

    Raises WorktreeProvisioningError on any inconsistency -- never
    returns a lane that hasn't been confirmed usable.
    """
    main_repo_cwd = Path(main_repo_cwd) if main_repo_cwd else _require_repo_root(None)
    branch = _lane_branch_name(session_id, task_id)
    path = _lane_path(main_repo_cwd, session_id, task_id, worktree_base)

    existing = {w.path.resolve(): w for w in list_worktrees(cwd=main_repo_cwd)}
    if path.resolve() in existing:
        info = existing[path.resolve()]
        if info.branch != branch:
            raise WorktreeProvisioningError(
                f"{path} is already a worktree, but on branch {info.branch!r}, "
                f"not the expected {branch!r} for task {task_id!r}/session "
                f"{session_id!r}. Refusing to reuse a lane that doesn't match "
                "the task/session/branch it's supposed to belong to."
            )
        return LaneWorktree(task_id=task_id, session_id=session_id, path=path, branch=branch)

    if path.exists():
        raise WorktreeProvisioningError(
            f"{path} exists on disk but is not a registered git worktree of "
            f"{main_repo_cwd} -- refusing to provision over an unrelated "
            "directory. Remove or relocate it manually before retrying."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(["worktree", "add", "-b", branch, str(path)], cwd=main_repo_cwd)
    if result.returncode != 0:
        raise WorktreeProvisioningError(
            f"git worktree add failed for task {task_id!r}, session "
            f"{session_id!r} at {path}: {result.stderr.strip()}"
        )
    return LaneWorktree(task_id=task_id, session_id=session_id, path=path, branch=branch)


def validate_lane_worktree(lane: LaneWorktree, main_repo_cwd: Optional[Path] = None) -> None:
    """Confirm a resolved lane is actually safe to execute in. This is
    the check that must run before an agent process is launched with
    `lane.path` as its cwd (D-290 requirement: "the worktree is validated
    against the task/session/branch" and "contamination checks run
    against the actual lane checkout").

    Raises WorktreeProvisioningError -- never returns a partial/soft
    result -- so callers can treat "validation didn't raise" as the
    entire safety contract, with nothing left for the caller to
    separately remember to check.
    """
    main_repo_cwd = Path(main_repo_cwd) if main_repo_cwd else _require_repo_root(None)

    if not lane.path.exists():
        raise WorktreeProvisioningError(f"Lane worktree for {lane.task_id}/{lane.session_id} does not exist: {lane.path}")

    worktrees = {w.path.resolve(): w for w in list_worktrees(cwd=main_repo_cwd)}
    info = worktrees.get(lane.path.resolve())
    if info is None:
        raise WorktreeProvisioningError(f"{lane.path} is not a registered worktree of {main_repo_cwd}")
    if info.branch != lane.branch:
        raise WorktreeProvisioningError(
            f"{lane.path} is on branch {info.branch!r}, expected {lane.branch!r} "
            f"for task {lane.task_id!r}/session {lane.session_id!r}"
        )
    if not is_isolated_worktree(cwd=lane.path):
        raise WorktreeProvisioningError(f"{lane.path} resolved as the MAIN worktree, not an isolated lane -- refusing to execute here.")

    contamination = check_contamination(cwd=lane.path)
    if not contamination.clean:
        raise WorktreeProvisioningError(
            f"Lane worktree {lane.path} for {lane.task_id}/{lane.session_id} is "
            f"not clean before execution: {contamination.unexpected_files}. "
            f"{contamination.note}"
        )


class UnsafeCleanupError(RuntimeError):
    pass


def safe_cleanup_worktree(
    path: Path,
    main_repo_cwd: Optional[Path] = None,
    require_merged_into: str = "main",
    force: bool = False,
) -> str:
    """Remove a completed lane's worktree, but only if it's safe:

    - the worktree must be clean (no uncommitted changes)
    - its branch must be fully merged into `require_merged_into`
      (default 'main') -- unmerged work is never removed
    - `git worktree remove` itself is used (not manual `rm -rf`), so git's
      own safety checks also apply

    Raises UnsafeCleanupError instead of doing anything destructive if any
    condition isn't met -- "fail safely rather than force-removing
    uncertain state" per D-288's task spec. `force=True` only bypasses
    git's own "worktree has changes" guard for a worktree already
    confirmed clean+merged by the checks above; it never bypasses this
    function's own merged/clean checks.
    """
    path = Path(path)
    if not path.exists():
        # D-289/CN-003: same fix as check_contamination() -- a worktree
        # git still lists but whose directory is gone on disk must raise
        # the documented error type, not a raw FileNotFoundError from
        # subprocess. Not treated as "safe to clean up silently": the
        # caller still needs to know something is inconsistent (git
        # metadata references a path git worktree prune hasn't reconciled
        # yet), so this still raises rather than succeeding quietly.
        raise UnsafeCleanupError(
            f"{path} does not exist on disk but git may still reference it as a "
            "worktree -- refusing to guess. Run `git worktree prune` (from the "
            "main checkout) to reconcile stale metadata, then re-check."
        )
    worktrees = list_worktrees(cwd=main_repo_cwd)
    matches = [w for w in worktrees if w.path.resolve() == path.resolve()]
    if not matches:
        raise UnsafeCleanupError(f"{path} is not a known worktree of this repo -- refusing to touch it.")
    info = matches[0]
    if info.is_main:
        raise UnsafeCleanupError("Refusing to remove the main worktree.")

    status = _run_git(["status", "--porcelain"], cwd=path)
    if status.returncode != 0:
        raise UnsafeCleanupError(f"Could not check status of {path}: {status.stderr.strip()}")
    if status.stdout.strip():
        raise UnsafeCleanupError(
            f"{path} has uncommitted changes -- refusing to remove a worktree "
            "that might contain unmerged work."
        )

    if info.branch is None:
        raise UnsafeCleanupError(f"{path} is in a detached-HEAD state -- refusing to remove without a branch to verify merge status against.")

    merged = _run_git(["branch", "--merged", require_merged_into], cwd=main_repo_cwd)
    if merged.returncode != 0:
        raise UnsafeCleanupError(f"Could not check merge status against {require_merged_into!r}: {merged.stderr.strip()}")
    merged_branches = {line.strip().lstrip("*+").strip() for line in merged.stdout.splitlines()}
    if info.branch not in merged_branches:
        raise UnsafeCleanupError(
            f"Branch {info.branch!r} for {path} is not merged into "
            f"{require_merged_into!r} -- refusing to remove unmerged work."
        )

    remove_args = ["worktree", "remove"]
    if force:
        remove_args.append("--force")
    remove_args.append(str(path))
    removed = _run_git(remove_args, cwd=main_repo_cwd)
    if removed.returncode != 0:
        raise UnsafeCleanupError(f"git worktree remove failed for {path}: {removed.stderr.strip()}")

    return f"Removed {path} (branch {info.branch!r}, confirmed clean and merged into {require_merged_into!r})."
