"""
"What changed?" project-specific change summary (C-062, adapted for
this framework by C-076).

Answers a time-windowed question — "what happened in the last N hours/
days?" — by combining three sources this repo already tracks separately,
rather than inventing a new activity log:

    - git history: `git log --since=...` (commit subjects, author, count)
    - task history: orchestration/tasks.yaml, filtered by `updated_at`
      (status transitions — claims, submissions, approvals)
    - agent activity: control_db's events table, filtered by
      `occurred_at` (session starts, lease acquires, etc.)

No new storage, no new activity log — this module only reads and
composes what already exists.

A fourth source — recorded "decisions" — existed in the AI-Brain
version this was extracted from, backed by that project's own
domain-specific facts database. It's intentionally left out here: this
framework has no opinion on and no dependency on wherever the project
that adopts it keeps its domain data. AI-Brain's own copy of this file
is unchanged and keeps the decisions source; if you want an equivalent
here, add it back as a source your own project supplies (e.g. a
callable passed into `build_change_summary`), not a hard import.

"Documents" from the original C-062 spec (docs/iteration improvement
ideas.md) is covered by git history: a doc edit is a commit like any
other, and a separate document-diffing pass would just re-derive what
`git log --stat` already reports — deliberately not built as a fourth
source.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from opencode_crack.orchestrator import timestamps
from opencode_crack.orchestrator.task_board import BOARD_PATH
from opencode_crack.runtime import control_db
from opencode_crack.runtime.control_db import CONTROL_DB_PATH


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored timestamp into an aware UTC datetime.

    Tries the house `timestamps.TIMESTAMP_FORMAT` first (what
    tasks.yaml/concerns.yaml/knowledge.yaml all use), then falls back to
    `datetime.fromisoformat` for the other on-disk shapes this module
    reads (control_db's `occurred_at`, decisions' `decided_at`), which
    predate that contract and use plain ISO-8601 with offsets. Naive
    results are normalized to UTC. Returns None for anything
    unparseable rather than raising — a summary tool should degrade,
    not crash, on one malformed row."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, timestamps.TIMESTAMP_FORMAT)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _git_commits_since(since: datetime, repo_path: Path = Path(".")) -> list[dict]:
    """Commit subjects in the window, newest first.

    Shells out to `git log` rather than a git library — this repo has
    no existing git-library dependency (see gen_token.py's own use of
    subprocess-free JWT signing for the same "don't add a dependency
    for one small thing" reasoning) and `git log` is already present in
    every environment this runs in. Returns [] (not an error) when the
    path isn't a git repo or the command fails, since a change summary
    should degrade gracefully rather than crash the whole report over
    one unavailable source."""
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={since.isoformat()}",
                "--pretty=format:%H|%an|%ad|%s",
                "--date=iso-strict",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    commits = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, author, date, subject = parts
        commits.append({
            "sha": sha[:10],
            "author": author,
            "date": date,
            "subject": subject,
        })
    return commits


def _task_changes_since(since: datetime, tasks_path: Path = BOARD_PATH) -> list[dict]:
    """Tasks whose `updated_at` falls in the window, newest first.

    Reads tasks.yaml directly rather than going through task_board's
    higher-level Task objects — this only needs status/updated_at/notes,
    and importing task_board's full parse-and-validate path would pull
    in git-pull-before-read behavior this read-only summary doesn't
    need. Returns [] when the file doesn't exist (fresh checkout,
    fine)."""
    import yaml

    if not Path(tasks_path).exists():
        return []
    with open(tasks_path) as f:
        tasks = yaml.safe_load(f) or {}

    changed = []
    for task_id, fields in tasks.items():
        ts = _parse_ts(fields.get("updated_at"))
        if ts is not None and ts >= since:
            changed.append({
                "task_id": task_id,
                "status": fields.get("status"),
                "assignee": fields.get("assignee"),
                "updated_at": fields.get("updated_at"),
            })
    changed.sort(key=lambda t: t["updated_at"] or "", reverse=True)
    return changed


def _agent_events_since(since: datetime, db_path: Path = CONTROL_DB_PATH,
                         limit: int = 200) -> list[dict]:
    """Agent/session/lease events in the window, newest first.

    control_db.get_recent_events() is limit-based, not time-windowed —
    fetches a generous limit and filters client-side by `occurred_at`
    rather than adding a new time-filtered query to control_db.py,
    keeping this task's footprint to one new file. Returns [] when
    control.db doesn't exist yet (matches
    get_control_plane_summary's own not-an-error convention)."""
    if not Path(db_path).exists():
        return []
    events = control_db.get_recent_events(limit=limit, db_path=db_path)
    in_window = [
        e for e in events
        if (ts := _parse_ts(e.get("occurred_at"))) is not None and ts >= since
    ]
    return in_window


def build_change_summary(*, hours: Optional[float] = None, days: Optional[float] = None,
                          repo_path: Path = Path("."), control_db_path: Optional[Path] = None,
                          tasks_path: Path = BOARD_PATH) -> dict:
    """Compose the three sources into one project-specific change
    summary for the given window.

    Exactly one of `hours`/`days` should be given; `days` is used when
    both are given (mirrors the `summary(days=N)` convention already
    used by usage_ledger). Defaults to the last 24 hours when neither
    is given — the most common "what happened since yesterday" case.

    `control_db_path`/`tasks_path` default to production paths when
    omitted; tests pass tmp_path fixtures here for isolation.

    Returns a dict with a `since` timestamp (ISO, for display/logging)
    and one list per source (`commits`, `task_changes`, `agent_events`),
    plus a `counts` dict so callers/formatters don't need to len() each
    list themselves."""
    if days is not None:
        window = timedelta(days=days)
    elif hours is not None:
        window = timedelta(hours=hours)
    else:
        window = timedelta(hours=24)

    since = datetime.now(timezone.utc) - window

    commits = _git_commits_since(since, repo_path=repo_path)
    task_changes = _task_changes_since(since, tasks_path=tasks_path)
    agent_kwargs = {"db_path": control_db_path} if control_db_path is not None else {}
    agent_events = _agent_events_since(since, **agent_kwargs)

    return {
        "since": since.isoformat(),
        "window_hours": window.total_seconds() / 3600,
        "commits": commits,
        "task_changes": task_changes,
        "agent_events": agent_events,
        "counts": {
            "commits": len(commits),
            "task_changes": len(task_changes),
            "agent_events": len(agent_events),
        },
    }
