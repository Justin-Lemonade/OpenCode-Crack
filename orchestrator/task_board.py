"""
Task board: the state layer on top of `iteration improvement ideas.md`.

Design
------
`orchestration/tasks.yaml` holds ONLY state (status/assignee/timestamps),
keyed by task ID. The task *content* (title, priority, description) always
comes from task_parser.parse_roadmap() — never duplicated here. This keeps
the roadmap doc as the single source of truth (see task_parser.py).

Locking / overlap prevention
-----------------------------
Multiple agents (this Claude session today, a future Claude session, a
delegated cheap model, etc.) may work on this project at different times,
possibly concurrently. The repo itself is the shared coordination point:

    claim(task_id, agent) does:
        1. git pull            (see the latest claims from other agents)
        2. refuse if the task is already claimed/in_progress/done
        3. write the claim into tasks.yaml
        4. git commit + push   (publish the claim immediately)
        5. if the push is rejected (someone else pushed first): pull again
           and re-check status. If it's now taken, abort — don't retry-steal.

This turns "two agents grab the same task" into a race that git's
push-rejection already resolves, instead of requiring a separate lock
server. It's not instantaneous (an agent could in theory start working in
the few seconds between pull and push), but for this project's cadence
(agents work in discrete sessions, not sub-second loops) that's an
acceptable, honest tradeoff — documented rather than hidden.

Ownership (D-363 / CN-010)
--------------------------
The claim also records an OWNER token: the claiming agent string plus the
claiming process's SESSION_ID, persisted in tasks.yaml as `owner` /
`owner_session`. Every later worker-side transition (start, submit, block
or release from an active state) must present `--agent` matching the
recorded owner, or an explicit override for the sanctioned cases (stale
takeover per find_work step 9.5, Primary Claude / human intervention).
Reviewer-side transitions (approve / reject / complete) stay role-based:
the reviewer is by design a different party, so they carry optional actor
attribution instead of an owner check.

Honest boundary: agent strings are self-asserted — there is no central
auth service (deliberately out of scope). The token is therefore a
claim-check plus an audit mechanism, not cryptographic authentication:
cross-owner moves are now explicitly rejected at the API layer instead of
silently allowed, and every transition logs both the recorded owner and
the acting SESSION_ID, so impersonation is detectable after the fact.
Shared git authorship is never treated as identity.
"""
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

from src.orchestrator.task_parser import parse_roadmap

BOARD_PATH = Path("orchestration/tasks.yaml")

# One session ID per process lifetime (set at import time, not per call) so
# activity.log entries from a single run can be grouped together. The 8-char
# prefix keeps lines short while still being effectively unique per session.
SESSION_ID: str = uuid.uuid4().hex[:8]

VALID_STATUSES = {"open", "claimed", "in_progress", "review", "done", "blocked"}
ACTIVE_STATUSES = {"claimed", "in_progress"}

REPORTS_DIR = Path("reports")
ACTIVITY_LOG = REPORTS_DIR / "activity.log"

# A claimed/in_progress task with no update in this long is almost always
# an agent that crashed, got interrupted, or forgot to report back — not
# genuinely still being worked on. Surfaced as "STALE" in status output so
# it reads as effectively open again, without silently reopening it (a
# human/agent should look at *why* it stalled before someone else grabs it).
STALE_AFTER_HOURS = 48


class GitSyncError(RuntimeError):
    """Raised when a board mutation cannot safely synchronize with origin."""


class OwnershipError(RuntimeError):
    """Raised when a caller tries to move a task it does not own.

    Subclasses RuntimeError so existing generic handlers keep catching it;
    callers that care about the distinction can catch OwnershipError
    explicitly. See the Ownership section in the module docstring.
    """


def _sync_before_mutation(action: str) -> None:
    pull = _run_git("pull", "--rebase", "origin", "main")
    if pull.returncode == 0:
        return

    detail = (pull.stderr or pull.stdout or "unknown git error").strip()
    lowered = detail.lower()
    if any(marker in lowered for marker in (
        "could not resolve host",
        "could not connect to server",
        "failed to connect",
        "connection timed out",
        "network is unreachable",
    )):
        raise GitSyncError(
            f"remote is unreachable; cannot safely {action}. "
            "Claiming requires a successful pull and push so the shared lock "
            "cannot be verified offline. Retry when GitHub is reachable. "
            f"Details: {detail}"
        )
    raise GitSyncError(
        f"git pull failed; cannot safely {action}. Resolve the repository "
        f"state manually before retrying. Details: {detail}"
    )


@dataclass
class TaskState:
    status: str = "open"
    assignee: Optional[str] = None
    claimed_at: Optional[str] = None
    updated_at: Optional[str] = None
    notes: str = ""
    report_path: Optional[str] = None
    # D-363 ownership: `owner` is the claiming agent string (assignee is
    # kept for display/back-compat and always mirrors it while active);
    # `owner_session` is the claiming process's SESSION_ID for audit
    # correlation. Both are None on entries written before D-363 (legacy).
    owner: Optional[str] = None
    owner_session: Optional[str] = None


def _owner_of(entry: dict) -> Optional[str]:
    """Effective owner of a raw tasks.yaml entry: the D-363 `owner` field,
    falling back to `assignee` for legacy entries written before it."""
    return entry.get("owner") or entry.get("assignee")


def _require_owner(task_id: str, entry: dict, agent: Optional[str], action: str) -> str:
    """Enforce the ownership contract for a worker-side transition.

    Returns "ok", or "legacy" for a pre-D-363 entry matched by assignee
    string (the caller backfills owner_session so the entry self-heals).
    Raises OwnershipError on mismatch and RuntimeError when the task has
    no owner at all. Agent names are self-asserted identifiers, never
    secrets. Explicit overrides (stale takeover / Primary Claude / human)
    bypass this check in the caller and are logged distinctly there.
    """
    owner = _owner_of(entry)
    if owner is None:
        raise RuntimeError(
            f"Cannot {action} {task_id}: it has no assignee (status={entry.get('status')!r}). "
            "Call claim() successfully first."
        )
    if agent is None:
        raise OwnershipError(
            f"Cannot {action} {task_id}: no --agent presented, but it is owned by "
            f"{owner!r}. Pass --agent with the exact claiming agent string."
        )
    if agent != owner:
        raise OwnershipError(
            f"Cannot {action} {task_id}: owned by {owner!r}, not {agent!r}. "
            "Only the owning agent (or an explicit override for stale takeover / "
            "Primary Claude / human) may move an active task."
        )
    if "owner" not in entry:
        return "legacy"
    return "ok"


@dataclass
class Task:
    """A merged view: roadmap content + tracked state."""
    id: str
    tier: str
    title: str
    priority: str
    state: TaskState = field(default_factory=TaskState)


def _now() -> str:
    from src.orchestrator.timestamps import now as _shared_now
    return _shared_now()


# Matches the serialization contract exactly: whole-second UTC, e.g.
# '2026-08-21T17:21:06Z'. Rejects fractional seconds, offsets other than
# 'Z', and any other drift from _now()'s own format.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# The state fields that must always be whole-second UTC timestamps (or None).
_TIMESTAMP_FIELDS = ("claimed_at", "updated_at")


def _validate_timestamp_format(task_id: str, field: str, value: Optional[str]) -> None:
    """Raise if `value` isn't None and isn't a whole-second '%Y-%m-%dT%H:%M:%SZ'
    string. Called from the single on-disk writer (_save_states) so no path —
    CLI, bulk migration script, or future caller — can silently persist a
    timestamp the serialization contract (tests/test_serialization_contract.py)
    and is_stale()'s strptime don't agree on.

    Thin wrapper around src.orchestrator.timestamps (extracted so
    concerns_board.py, and any future orchestration/*.yaml board, reuse the
    same guard instead of re-deriving it — see timestamps.py's docstring).
    """
    from src.orchestrator.timestamps import validate_timestamp_format as _shared_validate
    _shared_validate(task_id, field, value)


def is_stale(task: "Task", now: Optional[datetime] = None) -> bool:
    """True if an active (claimed/in_progress) task hasn't been touched in
    STALE_AFTER_HOURS. Always False for open/done/blocked — staleness only
    describes work that claims to be happening but shows no recent sign of it."""
    if task.state.status not in ACTIVE_STATUSES or not task.state.updated_at:
        return False
    now = now or datetime.now(timezone.utc)
    updated = datetime.strptime(task.state.updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (now - updated) > timedelta(hours=STALE_AFTER_HOURS)


def _run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _load_states() -> dict:
    """Read raw state dict from tasks.yaml. Empty dict if file is new."""
    if not BOARD_PATH.exists():
        return {}
    with open(BOARD_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _save_states(states: dict) -> None:
    """The single on-disk writer for tasks.yaml. Validates every persisted
    timestamp against the serialization contract before writing anything —
    this is the choke point a D-263-style violation must pass through
    regardless of which caller (claim/start/complete, a repair script, a
    future bulk migration) produced the state dict."""
    for task_id, entry in states.items():
        if not isinstance(entry, dict):
            continue
        for field in _TIMESTAMP_FIELDS:
            _validate_timestamp_format(task_id, field, entry.get(field))
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BOARD_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(states, f, sort_keys=True, default_flow_style=False)


def _merge_tasks() -> list[Task]:
    """Pure read: derive the current task list from the roadmap doc +
    tracked state, with NO file writes. Safe to call as often as needed
    (e.g. every list/get) without ever dirtying the working tree."""
    roadmap_tasks = parse_roadmap()
    states = _load_states()

    merged = []
    for rt in roadmap_tasks:
        raw_state = states.get(rt.id, {})
        state = TaskState(**raw_state) if raw_state else TaskState()
        merged.append(Task(id=rt.id, tier=rt.tier, title=rt.title, priority=rt.priority, state=state))
    return merged


def sync() -> list[Task]:
    """Re-derive the task list AND persist it: writes tasks.yaml (dropping
    IDs the roadmap no longer defines, adding new ones as 'open') and
    regenerates STATUS.md. This is the only function that writes — call it
    when you actually intend to publish a change (claim/release/complete/
    etc. already do). Reads (list_tasks/get_task) use _merge_tasks() instead
    so simply looking at the board never creates a spurious local diff."""
    merged = _merge_tasks()

    new_states = {}
    for t in merged:
        entry = {
            "status": t.state.status,
            "assignee": t.state.assignee,
            "claimed_at": t.state.claimed_at,
            "updated_at": t.state.updated_at,
            "notes": t.state.notes,
            "report_path": t.state.report_path,
        }
        # D-363: ownership must survive sync() — dropping these here
        # would silently strip every claim's owner on the next publish.
        # None-valued keys are omitted so pre-D-363 entries stay key-less
        # (and therefore recognizably legacy) until a real transition
        # backfills them, instead of churning all 400+ rows with nulls.
        if t.state.owner is not None:
            entry["owner"] = t.state.owner
        if t.state.owner_session is not None:
            entry["owner_session"] = t.state.owner_session
        new_states[t.id] = entry
    _save_states(new_states)
    _write_status(merged)
    return merged


def _write_status(tasks: list) -> None:
    from src.orchestrator import status_report
    status_report.write_status(tasks)


def list_tasks(tier: Optional[str] = None, status: Optional[str] = None) -> list[Task]:
    tasks = _merge_tasks()
    if tier:
        tasks = [t for t in tasks if t.tier == tier]
    if status:
        tasks = [t for t in tasks if t.state.status == status]
    return tasks


def get_task(task_id: str) -> Optional[Task]:
    for t in _merge_tasks():
        if t.id == task_id:
            return t
    return None


_TASK_REF_RE = re.compile(r"[HDC]-\d{3}")


def get_next_task() -> Optional[Task]:
    """Return the best open delegate task per the find_work selection policy.

    Selection order:
    1. Tasks with feedback (non-empty notes from prior review) first.
    2. Among those, tasks that unblock the most other open tasks first.
    3. Then by priority label (CRITICAL > HIGH > MEDIUM-HIGH > MEDIUM > LOW-MEDIUM > LOW).
    4. Finally by task ID to keep the result deterministic.
    """
    tasks = _merge_tasks()
    open_delegate = [t for t in tasks if t.tier == "delegate" and t.state.status == "open"]
    if not open_delegate:
        return None

    priority_order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM-HIGH": 2,
        "MEDIUM": 3,
        "LOW-MEDIUM": 4,
        "LOW": 5,
    }

    candidate_ids = {t.id for t in open_delegate}
    dependency_counts: dict[str, int] = {t.id: 0 for t in open_delegate}

    for t in open_delegate:
        try:
            from src.orchestrator.task_parser import get_section_text
            section = get_section_text(t.id)
            refs = set(_TASK_REF_RE.findall(section))
            for ref in refs:
                if ref in candidate_ids:
                    dependency_counts[ref] += 1
        except ValueError:
            pass

    def _sort_key(t: Task):
        has_feedback = 1 if (t.state.notes and t.state.notes.strip()) else 0
        unblocks = dependency_counts.get(t.id, 0)
        priority = priority_order.get(t.priority, 9)
        return (-has_feedback, -unblocks, priority, t.id)

    open_delegate.sort(key=_sort_key)
    return open_delegate[0]


def _append_activity(line: str) -> None:
    """Append one line to reports/activity.log. This is written by the
    TOOL itself (not by agent-authored prose), on every status change, so
    it's a reliable audit trail even if an agent forgets or fabricates a
    report — 'what actually happened, in what order' always exists
    independent of what any single agent claims happened."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now()}  [{SESSION_ID}]  {line}\n")


def _git_publish(message: str, extra_paths: Optional[list] = None) -> bool:
    """Commit + push tasks.yaml, STATUS.md, and the activity log together
    (so none of the three can drift apart), plus any extra_paths — e.g. a
    submitted report file, which otherwise would sit untracked forever."""
    from src.orchestrator.status_report import STATUS_PATH

    paths = [str(BOARD_PATH), str(STATUS_PATH), str(ACTIVITY_LOG)]
    if extra_paths:
        paths.extend(str(p) for p in extra_paths)
    _run_git("add", *paths)
    # D-321/CN-007: `git commit` without a pathspec commits the whole
    # index, which would silently sweep another concurrent lane's staged
    # files into this commit. Fail closed instead (raises, commits nothing).
    from src.orchestrator.worktree_guard import assert_expected_staged_only
    assert_expected_staged_only(paths)
    commit = _run_git("commit", "-m", message)
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        # Something else went wrong (not just "no changes") — surface it.
        raise RuntimeError(f"git commit failed: {commit.stderr}")
    push = _run_git("push")
    return push.returncode == 0


def claim_task(task_id: str, agent: str) -> Task:
    """Claim a task for an agent. Pulls first to see the latest state,
    refuses if already taken, publishes the claim immediately."""
    _sync_before_mutation("claim")

    task = get_task(task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.state.status not in ("open", "blocked"):
        raise RuntimeError(
            f"{task_id} is already '{task.state.status}'"
            + (f" (assignee: {task.state.assignee})" if task.state.assignee else "")
        )

    states = _load_states()
    states[task_id] = {
        "status": "claimed",
        "assignee": agent,
        "claimed_at": _now(),
        "updated_at": _now(),
        "notes": states.get(task_id, {}).get("notes", ""),
        # D-363: ownership token — the claiming agent string plus this
        # process's SESSION_ID for audit correlation (both non-secret).
        "owner": agent,
        "owner_session": SESSION_ID,
    }
    _save_states(states)
    _write_status(_merge_tasks())
    _append_activity(f"{task_id}  {task.state.status} -> claimed  {agent}  owner={agent}  actor_session={SESSION_ID}")

    published = _git_publish(f"orchestrator: claim {task_id} for {agent}")
    if not published:
        # Someone else pushed between our pull and our push. Re-sync and
        # tell the caller the truth instead of silently overwriting.
        _run_git("pull", "--rebase", "origin", "main")
        fresh = get_task(task_id)
        raise RuntimeError(
            f"Push rejected — {task_id} may have just been claimed by another "
            f"agent. Current status: {fresh.state.status if fresh else 'unknown'}. "
            "Pick a different task."
        )
    return get_task(task_id)


def release_task(task_id: str, notes: str = "", agent: Optional[str] = None,
                 override: bool = False) -> Task:
    """Give up a claim without completing it (e.g. agent got interrupted).

    Owner-gated like start/submit: the owner passes --agent, anything else
    needs override=True (stale takeover per find_work step 9.5, Primary
    Claude / human), which is audit-logged as an override, not a normal
    release.
    """
    return _update_status(task_id, "open", notes=notes, clear_assignee=True,
                          agent=agent, override=override)


def complete_task(task_id: str, notes: str = "") -> Task:
    """Mark a task done directly, bypassing review. Intended for Primary
    Claude's own work (no separate agent to verify), or for manually
    approving something after out-of-band verification. Delegated agents
    should call submit_for_review() instead — see that function."""
    return _update_status(task_id, "done", notes=notes)


def block_task(task_id: str, notes: str, agent: Optional[str] = None) -> Task:
    """Owner-gated when the task is active (claimed/in_progress): pass the
    claiming --agent. Open tasks (e.g. a watchdog cap on unclaimed work)
    carry no owner, so no agent is needed for those.
    """
    return _update_status(task_id, "blocked", notes=notes, agent=agent)


def submit_for_review(task_id: str, notes: str, report_path: Optional[str] = None,
                      agent: Optional[str] = None) -> Task:
    """A delegated agent's way of saying 'I'm done, please check my work' —
    moves the task to 'review', NOT 'done'. This is the fix for two
    problems observed in practice: (1) an agent finished real work but
    never told the board, so the task sat as 'claimed' looking untouched
    while a second agent could plausibly have picked it up again if claim()
    were ever relaxed to allow re-claiming stale 'claimed' tasks; (2) a
    report pasted into chat claimed 7 tasks 'complete' but the board
    showed most of them still 'claimed' — self-reported completion was
    never actually verified against the repo. Splitting 'agent says done'
    (review) from 'verified done' (done, via approve_task) means nothing
    can be silently marked finished without someone actually checking —
    while still keeping the task LOCKED (not open) so nobody re-grabs it
    while it's awaiting review."""
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.state.status not in ACTIVE_STATUSES:
        raise RuntimeError(
            f"Cannot submit {task_id} for review: status is {task.state.status!r}, "
            f"expected claimed/in_progress. Claim it first."
        )
    return _update_status(task_id, "review", notes=notes, report_path=report_path,
                          agent=agent)


def approve_task(task_id: str, notes: str = "") -> Task:
    """Primary Claude (or a human) confirms a submitted task's work is
    good and marks it done. Only valid from 'review' — you can't approve
    something nobody submitted."""
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.state.status != "review":
        raise RuntimeError(f"Cannot approve {task_id}: status is {task.state.status!r}, expected 'review'.")
    return _update_status(task_id, "done", notes=notes or task.state.notes)


def reject_task(task_id: str, notes: str) -> Task:
    """Send a submitted task back to 'open' with feedback on what's
    missing/wrong, clearing the assignee so anyone (including the same
    agent) can pick it back up having read why it bounced. `notes` is
    required — a rejection with no reason is just as useless as a report
    with no content."""
    if not notes:
        raise ValueError("reject_task requires notes explaining what needs fixing.")
    return _update_status(task_id, "open", notes=notes, clear_assignee=True)


def start_task(task_id: str, agent: Optional[str] = None) -> Task:
    """Mark a claimed task in_progress as its owner.

    D-363: `agent` must be the exact claiming agent string (--agent).
    Previously only assignee *presence* was checked, so any local process
    could advance anyone's claimed task (CN-010). There is no override on
    start: nobody but the owner starts a claimed task — stale takeovers go
    through release (with override) followed by a fresh claim.
    """
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    states = _load_states()
    _require_owner(task_id, states.get(task_id, {}), agent, "start")
    return _update_status(task_id, "in_progress", agent=agent)


def _update_status(task_id: str, status: str, notes: str = "", clear_assignee: bool = False,
                    report_path: Optional[str] = None, agent: Optional[str] = None,
                    override: bool = False) -> Task:
    assert status in VALID_STATUSES
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    _sync_before_mutation(f"update {task_id}")
    states = _load_states()
    existing = states.get(task_id, {})
    prev_status = existing.get("status", "open")
    # D-363 ownership: worker-side moves out of an active state need the
    # owner (or an explicit, audit-logged override). Reviewer-side moves
    # (approve/reject/complete, which pass no agent) stay role-based: the
    # reviewer is by design a different party — see module docstring.
    legacy = False
    overridden = False
    if prev_status in ACTIVE_STATUSES and status != "done":
        if override:
            overridden = True
        elif _require_owner(task_id, existing, agent, f"move to {status}") == "legacy":
            legacy = True
    existing["status"] = status
    existing["updated_at"] = _now()
    if notes:
        existing["notes"] = notes
    if report_path:
        existing["report_path"] = report_path
    if clear_assignee:
        existing["assignee"] = None
        existing["claimed_at"] = None
        existing["owner"] = None
        existing["owner_session"] = None
    elif legacy:
        # Self-healing migration: a pre-D-363 entry just proved its owner
        # by assignee string — record the session so it is no longer
        # indistinguishable from unattributed history.
        existing["owner"] = existing.get("assignee")
        existing["owner_session"] = SESSION_ID
    states[task_id] = existing
    _save_states(states)
    _write_status(_merge_tasks())
    _append_activity(
        f"{task_id}  {prev_status} -> {status}"
        + (f"  {existing.get('assignee')}" if existing.get("assignee") else "")
        + (f"  owner={existing.get('owner')}" if existing.get("owner") else "")
        + f"  actor_session={SESSION_ID}"
        + ("  override" if overridden else "")
        + ("  legacy" if legacy else "")
        + (f"  \"{notes}\"" if notes else "")
    )
    _git_publish(f"orchestrator: {task_id} -> {status}", extra_paths=[report_path] if report_path else None)
    return get_task(task_id)
