"""Tests for src/orchestrator/status_report.py (roadmap D-052 + D-053).

D-052 scope: render_markdown()'s Recent Activity tail-of-activity.log
section (present last-10-lines / absent when no log / all lines when few).
D-053 scope: section-by-section behavior of write_status() with a known
task list — counts, review/blocked/stale sections, and the output path.
Concrete Task/TaskState objects, never mocks; STATUS_PATH is
monkeypatched to a tmp path so the real STATUS.md is never written.
"""
from datetime import datetime, timedelta, timezone

import pytest

from opencode_crack.orchestrator import status_report
from opencode_crack.orchestrator.task_board import Task, TaskState


@pytest.fixture
def sample_log(tmp_path, monkeypatch):
    log_path = tmp_path / "activity.log"
    lines = [f"2026-08-18T00:00:{i:02d}Z  D-0{i:02d}  open -> claimed  agent-x" for i in range(12)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr("opencode_crack.orchestrator.task_board.ACTIVITY_LOG", log_path)
    return lines


def test_recent_activity_section_shows_last_10_lines(sample_log):
    md = status_report.render_markdown([])
    assert "## Recent Activity" in md
    # 12 lines in the log -> only the last 10 appear (D-000/D-001 dropped)
    assert "D-000  open -> claimed" not in md
    assert "D-001  open -> claimed" not in md
    assert "D-003  open -> claimed" in md
    assert "D-011  open -> claimed" in md
    # first line inside the fenced block is line index 2 (D-002 is line 2)
    block = md.split("```")[1]
    block_lines = block.strip().splitlines()
    assert len(block_lines) == 10
    assert block_lines[0] == sample_log[2]
    assert block_lines[-1] == sample_log[-1]


def test_recent_activity_section_absent_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "opencode_crack.orchestrator.task_board.ACTIVITY_LOG", tmp_path / "missing.log"
    )
    md = status_report.render_markdown([])
    assert "## Recent Activity" not in md


def test_recent_activity_shows_all_lines_when_fewer_than_10(tmp_path, monkeypatch):
    log_path = tmp_path / "activity.log"
    log_path.write_text("2026-08-18T00:00:00Z  D-001  open -> claimed  agent-x\n", encoding="utf-8")
    monkeypatch.setattr("opencode_crack.orchestrator.task_board.ACTIVITY_LOG", log_path)
    md = status_report.render_markdown([])
    block = md.split("```")[1]
    assert block.strip().splitlines() == ["2026-08-18T00:00:00Z  D-001  open -> claimed  agent-x"]


# --- D-053: section-by-section behavior of write_status() -------------------

def _no_activity_log(tmp_path, monkeypatch):
    monkeypatch.setattr("opencode_crack.orchestrator.task_board.ACTIVITY_LOG", tmp_path / "missing.log")


@pytest.fixture
def tasks():
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=49)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return [
        Task(id="D-001", tier="delegate", title="Open task", priority="MEDIUM",
             state=TaskState(status="open")),
        Task(id="D-002", tier="delegate", title="Review task", priority="MEDIUM",
             state=TaskState(status="review", assignee="agent-a",
                             updated_at="2026-08-18T00:00:00Z", notes="done the thing",
                             report_path="reports/D-002_report.md")),
        Task(id="D-003", tier="delegate", title="Done task", priority="MEDIUM",
             state=TaskState(status="done", updated_at="2026-08-17T00:00:00Z")),
        Task(id="D-004", tier="delegate", title="Blocked task", priority="MEDIUM",
             state=TaskState(status="blocked", notes="needs a human")),
        Task(id="D-005", tier="delegate", title="Stale active task", priority="MEDIUM",
             state=TaskState(status="in_progress", assignee="agent-b",
                             claimed_at="2026-08-15T00:00:00Z", updated_at=stale_ts)),
    ]


def _render_tasks(tasks, tmp_path, monkeypatch):
    _no_activity_log(tmp_path, monkeypatch)
    return status_report.render_markdown(tasks)


def test_write_status_has_all_sections_and_counts(tmp_path, monkeypatch, tasks):
    out_path = tmp_path / "STATUS.md"
    monkeypatch.setattr(status_report, "STATUS_PATH", out_path)
    _no_activity_log(tmp_path, monkeypatch)

    status_report.write_status(tasks)
    md = out_path.read_text(encoding="utf-8")

    assert "## Summary" in md
    assert "- **5** tasks total" in md
    assert "**1** done" in md
    assert "**1** awaiting review" in md
    assert "**1** active" in md
    assert "**1** blocked" in md
    assert "**1** open" in md
    assert "## Awaiting review" in md
    assert "## Active right now" in md
    assert "## Blocked" in md
    assert "## Recently completed" in md
    assert "## Not yet started" in md
    assert "## Recent Activity" not in md


def test_review_task_appears_in_awaiting_review(tasks, tmp_path, monkeypatch):
    md = _render_tasks(tasks, tmp_path, monkeypatch)
    assert "D-002 — Review task" in md
    assert "agent-a" in md
    assert "reports/D-002_report.md" in md
    assert "done the thing" in md


def test_stale_in_progress_task_gets_stale_marker(tasks, tmp_path, monkeypatch):
    md = _render_tasks(tasks, tmp_path, monkeypatch)
    assert "D-005 — Stale active task" in md
    assert "**STALE**" in md


def test_blocked_task_appears_in_blocked_section(tasks, tmp_path, monkeypatch):
    md = _render_tasks(tasks, tmp_path, monkeypatch)
    assert "**D-004**" in md
    assert "Blocked task: needs a human" in md


def test_write_status_writes_to_expected_path(tmp_path, monkeypatch, tasks):
    out_path = tmp_path / "STATUS.md"
    monkeypatch.setattr(status_report, "STATUS_PATH", out_path)
    _no_activity_log(tmp_path, monkeypatch)

    status_report.write_status(tasks)

    assert out_path.exists()
    assert "D-002 — Review task" in out_path.read_text(encoding="utf-8")