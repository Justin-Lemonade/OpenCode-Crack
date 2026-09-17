"""
Tests for the "what changed?" project change summary (C-062, adapted
for this framework by C-076).

Offline and tmp_path-only per repo convention: a real throwaway git repo
for the git-log source, synthetic tasks.yaml/control.db fixtures for the
other two, no reliance on this repo's own history or state.

A fourth source ("decisions") existed in the AI-Brain version this was
extracted from, backed by that project's own domain-specific facts
database — intentionally dropped here along with its tests. See
change_summary.py's module docstring.
"""
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from opencode_crack.orchestrator.change_summary import (
    _agent_events_since,
    _git_commits_since,
    _task_changes_since,
    build_change_summary,
)


# --- git commits ----------------------------------------------------------


def _init_repo_with_commits(repo: Path, subjects: list[str]) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    for i, subject in enumerate(subjects):
        (repo / f"f{i}.txt").write_text(subject)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", subject], cwd=repo, check=True)


def test_git_commits_since_returns_recent_subjects_newest_first(tmp_path):
    _init_repo_with_commits(tmp_path, ["first", "second", "third"])

    commits = _git_commits_since(datetime.now(timezone.utc) - timedelta(hours=1), repo_path=tmp_path)

    assert [c["subject"] for c in commits] == ["third", "second", "first"]
    assert all(len(c["sha"]) == 10 for c in commits)


def test_git_commits_since_excludes_commits_before_window(tmp_path):
    _init_repo_with_commits(tmp_path, ["old"])
    # A future "since" excludes everything that already happened.
    commits = _git_commits_since(datetime.now(timezone.utc) + timedelta(days=1), repo_path=tmp_path)
    assert commits == []


def test_git_commits_since_returns_empty_list_for_non_git_directory(tmp_path):
    # tmp_path has no .git — must degrade gracefully, not raise.
    assert _git_commits_since(datetime.now(timezone.utc) - timedelta(hours=1), repo_path=tmp_path) == []


# --- task changes -----------------------------------------------------------


def _write_tasks_yaml(path: Path, tasks: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(tasks, f)


def test_task_changes_since_filters_by_updated_at(tmp_path):
    tasks_path = tmp_path / "tasks.yaml"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_tasks_yaml(tasks_path, {
        "D-001": {"status": "done", "assignee": "agent-a", "updated_at": recent},
        "D-002": {"status": "open", "assignee": None, "updated_at": old},
    })

    changed = _task_changes_since(now - timedelta(hours=1), tasks_path=tasks_path)

    assert [t["task_id"] for t in changed] == ["D-001"]


def test_task_changes_since_returns_empty_list_for_missing_file(tmp_path):
    assert _task_changes_since(datetime.now(timezone.utc), tasks_path=tmp_path / "nope.yaml") == []


def test_task_changes_since_sorts_newest_first(tmp_path):
    tasks_path = tmp_path / "tasks.yaml"
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t2 = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_tasks_yaml(tasks_path, {
        "D-old": {"status": "done", "updated_at": t2},
        "D-new": {"status": "done", "updated_at": t1},
    })

    changed = _task_changes_since(now - timedelta(hours=1), tasks_path=tasks_path)

    assert [t["task_id"] for t in changed] == ["D-new", "D-old"]


def test_task_changes_since_skips_tasks_with_unparseable_or_missing_timestamp(tmp_path):
    tasks_path = tmp_path / "tasks.yaml"
    _write_tasks_yaml(tasks_path, {
        "D-bad": {"status": "open", "updated_at": "not-a-timestamp"},
        "D-none": {"status": "open"},
    })

    changed = _task_changes_since(datetime.now(timezone.utc) - timedelta(days=365), tasks_path=tasks_path)

    assert changed == []


# --- agent events -------------------------------------------------------


def test_agent_events_since_returns_empty_list_for_missing_db(tmp_path):
    assert _agent_events_since(datetime.now(timezone.utc), db_path=tmp_path / "nope.db") == []


def test_agent_events_since_filters_by_occurred_at(tmp_path):
    from opencode_crack.runtime import control_db

    db_path = tmp_path / "control.db"
    control_db.init_db(db_path)
    control_db._append_event("test_event", agent_id="agent-a", db_path=db_path)

    events = _agent_events_since(datetime.now(timezone.utc) - timedelta(hours=1), db_path=db_path)

    assert len(events) == 1
    assert events[0]["event_type"] == "test_event"

    # A future "since" excludes the event just written.
    future_events = _agent_events_since(datetime.now(timezone.utc) + timedelta(days=1), db_path=db_path)
    assert future_events == []


# --- build_change_summary composition ------------------------------------


def test_build_change_summary_defaults_to_24_hours(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_repo_with_commits(tmp_path, ["only commit"])

    result = build_change_summary(
        repo_path=tmp_path, tasks_path=tmp_path / "nope.yaml",
        control_db_path=tmp_path / "nope.db",
    )

    assert result["window_hours"] == 24.0
    assert result["counts"]["commits"] == 1


def test_build_change_summary_days_takes_precedence_over_hours(tmp_path):
    _init_repo_with_commits(tmp_path, ["c"])
    result = build_change_summary(
        hours=1, days=2, repo_path=tmp_path,
        tasks_path=tmp_path / "nope.yaml", control_db_path=tmp_path / "nope.db",
    )
    assert result["window_hours"] == 48.0


def test_build_change_summary_shape_has_all_three_sources_and_counts(tmp_path):
    _init_repo_with_commits(tmp_path, ["a commit"])
    result = build_change_summary(
        hours=1, repo_path=tmp_path,
        tasks_path=tmp_path / "nope.yaml", control_db_path=tmp_path / "nope.db",
    )

    assert set(result) == {
        "since", "window_hours", "commits", "task_changes",
        "agent_events", "counts",
    }
    assert set(result["counts"]) == {
        "commits", "task_changes", "agent_events",
    }
    assert result["counts"]["commits"] == len(result["commits"])
