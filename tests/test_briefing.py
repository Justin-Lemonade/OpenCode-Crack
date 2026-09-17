"""Tests for src/orchestrator/briefing.py — the compact delegated-agent
startup briefing (roadmap D-149).

The briefing must contain exactly what an agent needs to begin task
selection/work — queue summary, top eligible D-tier tasks, essential
claim/submit/review rules, and pointers to the full protocol documents —
without reproducing those documents. It must be deterministic (byte-stable
across repeated calls on the same board) and have a machine-readable JSON
form. All tests are hermetically routed to a small fixture board so nothing
depends on the real roadmap or board state.
"""
import json

import pytest

from opencode_crack.orchestrator import briefing
from opencode_crack.orchestrator.briefing import (
    TOP_TASK_LIMIT,
    build_briefing,
    build_briefing_json,
    briefing_fields,
)
from opencode_crack.orchestrator.task_board import Task, TaskState


def _task(task_id, tier="delegate", priority="MEDIUM", status="open", title="Some task") -> Task:
    return Task(
        id=task_id, tier=tier, title=title, priority=priority,
        state=TaskState(status=status),
    )


@pytest.fixture
def board():
    """A fixture board exercising every status and tier, with priorities
    deliberately out of order so sorting is what's being tested."""
    return [
        _task("D-001", priority="MEDIUM", title="Medium open"),
        _task("D-002", priority="HIGH", title="High open"),
        _task("D-003", priority="CRITICAL", title="Critical open"),
        _task("D-004", priority="LOW", title="Low open"),
        _task("D-005", tier="primary_claude", priority="CRITICAL", title="C open"),
        _task("D-006", tier="human", priority="CRITICAL", title="H open"),
        _task("D-007", priority="HIGH", status="claimed", title="Claimed"),
        _task("D-008", priority="HIGH", status="review", title="Review"),
        _task("D-009", priority="HIGH", status="done", title="Done"),
        _task("D-010", priority="HIGH", status="blocked", title="Blocked"),
        _task("D-011", priority="MEDIUM", status="in_progress", title="In progress"),
    ]


# --- briefing_fields ---------------------------------------------------------

def test_queue_summary_counts_statuses(board):
    f = briefing_fields(board)
    assert f["queue_summary"] == {
        "total": 11,
        "open": 6,
        "active": 2,      # D-007 claimed + D-011 in_progress
        "review": 1,
        "blocked": 1,
        "done": 1,
    }


def test_top_tasks_are_open_delegate_only_sorted_by_priority(board):
    f = briefing_fields(board)
    ids = [t["task_id"] for t in f["top_eligible_tasks"]]
    # Only open delegate tasks: D-001..D-004. C/H tiers, claimed/review/
    # done/blocked/in_progress are all excluded.
    assert ids == ["D-003", "D-002", "D-001", "D-004"]
    priorities = [t["priority"] for t in f["top_eligible_tasks"]]
    assert priorities == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    # Each entry carries the id/priority/title fields a consumer needs.
    assert f["top_eligible_tasks"][0] == {
        "task_id": "D-003", "priority": "CRITICAL", "title": "Critical open",
    }


def test_top_tasks_capped_at_limit():
    many = [_task(f"D-{i:03d}", priority="MEDIUM") for i in range(1, 30)]
    f = briefing_fields(many)
    assert len(f["top_eligible_tasks"]) == TOP_TASK_LIMIT


def test_top_tasks_empty_when_no_open_delegate(board):
    no_delegate_open = [_t for _t in board if _t.tier != "delegate"] + [
        _task("D-020", status="review"),
        _task("D-021", status="done"),
    ]
    f = briefing_fields(no_delegate_open)
    assert f["top_eligible_tasks"] == []


def test_essential_rules_and_pointers_present(board):
    f = briefing_fields(board)
    joined = "\n".join(f["essential_rules"]).lower()
    assert "claim" in joined
    assert "submit" in joined
    assert "approve" in joined
    assert "reject" in joined
    assert "docs/AGENTS.md" in f["protocol_pointers"]
    assert ".agent_prompts/find_work.md" in f["protocol_pointers"]
    assert "STATUS.md" in f["protocol_pointers"]


# --- human-readable form ------------------------------------------------------

def test_build_briefing_includes_required_sections(board):
    out = build_briefing(board)
    assert "## Top eligible D-tier tasks" in out
    assert "## Essential workflow rules" in out
    assert "## Full protocol documents" in out
    assert "- D-003 (CRITICAL) — Critical open" in out
    assert "Queue: 11 tasks — 1 done, 1 review, 2 active, 1 blocked, 6 open" in out


def test_build_briefing_points_to_documents_not_reproduce(board):
    out = build_briefing(board)
    # Pointers to the full documents, not their contents.
    assert "AGENTS.md: agent coordination guide" in out
    assert ".agent_prompts/find_work.md: full find-work protocol" in out
    assert "STATUS.md: full board snapshot" in out


# --- determinism ---------------------------------------------------------------

def test_briefing_byte_stable_across_repeated_calls(board):
    first = build_briefing(board)
    for _ in range(5):
        assert build_briefing(board) == first


def test_briefing_json_byte_stable_across_repeated_calls(board):
    first = build_briefing_json(board)
    for _ in range(5):
        assert build_briefing_json(board) == first


# --- JSON form ----------------------------------------------------------------

def test_briefing_json_is_valid_json_with_required_fields(board):
    data = json.loads(build_briefing_json(board))
    assert set(data) == {
        "queue_summary", "top_eligible_tasks", "essential_rules", "protocol_pointers",
    }
    assert data["queue_summary"]["total"] == 11
    assert data["top_eligible_tasks"][0]["task_id"] == "D-003"
    assert isinstance(data["essential_rules"], list) and data["essential_rules"]
    assert isinstance(data["protocol_pointers"], dict)


def test_briefing_json_deterministic_with_sort_keys():
    """The JSON form must not depend on dict insertion order of the input."""
    rev = [_task("D-002", priority="LOW"), _task("D-001", priority="HIGH")]
    fwd = [_task("D-001", priority="HIGH"), _task("D-002", priority="LOW")]
    assert json.loads(build_briefing_json(rev)) == json.loads(build_briefing_json(fwd))


# --- compactness ---------------------------------------------------------------

def test_briefing_is_bounded_regardless_of_board_size():
    """The briefing is a fixed-budget artifact: growing the board must not
    balloon the output — the queue summary is O(1) and top tasks are capped."""
    small = [_task("D-001", priority="MEDIUM")]
    big = [_task(f"D-{i:03d}", priority="MEDIUM") for i in range(1, 200)]
    small_out = build_briefing(small)
    big_out = build_briefing(big)
    # The queue summary + top-task cap means growth is bounded well under
    # an order of magnitude, far smaller than the full protocol docs.
    assert len(big_out) < len(small_out) * 3
    assert len(big_out) < 2500
