"""Tests for src/orchestrator/cli.py unknown-task behavior (roadmap D-089).

Every task-ID-taking CLI action must exit non-zero with a useful message
when handed an unknown task ID, instead of silently succeeding (or worse,
crashing with a traceback AFTER polluting the board by writing state for a
task that doesn't exist). These tests use a small fixture roadmap and
no-op git so nothing touches the real board.

The task_board._update_status guard is pinned here too: reject/complete/
release/block previously wrote state for unknown IDs before crashing.
"""
import json
import subprocess

import pytest

from opencode_crack.orchestrator import task_board, task_parser
from opencode_crack.orchestrator.cli import run_orchestrate

FIXTURE_ROADMAP = """\
# Fixture Roadmap

## H-001 — Decide something important

**Priority: CRITICAL.**

Human decision.

---

## D-001 — Run the tests and report failures

**Priority: HIGH.**

Narrow, testable, reversible.
"""

UNKNOWN = "Z-999"


@pytest.fixture
def fixture_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FIXTURE_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


@pytest.fixture
def fixture_board(tmp_path, fixture_roadmap, monkeypatch):
    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")
    return board_path


@pytest.fixture
def no_op_git(monkeypatch):
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(task_board, "_run_git", fake_run_git)


def _run_cli(capsys, *argv):
    """Run the orchestrate CLI; return (exit_code, printed output)."""
    try:
        run_orchestrate(list(argv))
        return 0, capsys.readouterr().out
    except SystemExit as exc:
        return exc.code, capsys.readouterr().out


# --- unknown task ID on every task-ID-taking action -------------------------

@pytest.mark.parametrize(
    "argv",
    [
        ["start", UNKNOWN, "--agent", "test-agent"],
        ["reject", UNKNOWN, "--notes", "why"],
        ["complete", UNKNOWN],
        ["release", UNKNOWN],
        ["block", UNKNOWN, "--notes", "why"],
        ["contract", UNKNOWN],
        ["dispatch", UNKNOWN],
        ["claim", UNKNOWN, "--agent", "test-agent"],
        ["submit", UNKNOWN, "--notes", "n", "--agent", "test-agent"],
        ["approve", UNKNOWN],
    ],
    ids=[
        "start", "reject", "complete", "release", "block",
        "contract", "dispatch", "claim", "submit", "approve",
    ],
)
def test_unknown_task_id_exits_nonzero_with_useful_message(
    fixture_board, no_op_git, capsys, argv
):
    code, out = _run_cli(capsys, *argv)
    assert code == 1
    assert UNKNOWN in out
    assert "No such task" in out


def test_unknown_id_write_guard_does_not_pollute_board(fixture_board, no_op_git):
    """reject/complete/release/block used to write state for unknown IDs
    (then crash on the None task). The _update_status guard must raise
    BEFORE any write, so the board file stays untouched."""
    with pytest.raises(ValueError, match="No such task"):
        task_board.reject_task(UNKNOWN, notes="why")
    with pytest.raises(ValueError, match="No such task"):
        task_board.complete_task(UNKNOWN)
    with pytest.raises(ValueError, match="No such task"):
        task_board.release_task(UNKNOWN)
    with pytest.raises(ValueError, match="No such task"):
        task_board.block_task(UNKNOWN, notes="why")

    board_text = (
        task_board.BOARD_PATH.read_text(encoding="utf-8")
        if task_board.BOARD_PATH.exists()
        else ""
    )
    assert UNKNOWN not in board_text


def test_known_task_actions_still_work_after_guard(fixture_board, no_op_git, capsys):
    task_board.claim_task("D-001", "agent-a")
    code, out = _run_cli(capsys, "start", "D-001", "--agent", "agent-a")
    assert code == 0
    assert "in_progress" in out

    code, out = _run_cli(capsys, "submit", "D-001", "--notes", "finished", "--agent", "agent-a")
    assert code == 0
    assert "review" in out

    code, out = _run_cli(capsys, "approve", "D-001")
    assert code == 0
    assert "done" in out

    assert task_board.get_task("D-001").state.status == "done"


# --- D-149: `orchestrate briefing` --------------------------------------------

@pytest.fixture
def briefing_board(fixture_board, tmp_path, monkeypatch):
    """Hermetic board for briefing tests: no delegated-task extension files
    leak in (parse_roadmap reads DELEGATED_TASKS_DIR in addition to the
    patched ROADMAP_PATH)."""
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "empty_delegated")
    return fixture_board


def test_briefing_lists_top_open_d_task(briefing_board, capsys):
    """The briefing must surface the current top eligible D-tier task and the
    essential workflow rules without touching git or the real board."""
    code, out = _run_cli(capsys, "briefing")
    assert code == 0
    assert "Delegated Agent Briefing" in out
    assert "D-001 (HIGH)" in out
    assert "Top eligible D-tier tasks" in out
    assert "Essential workflow rules" in out
    assert "submit" in out
    assert "approve" in out


def test_briefing_json_flag_is_deterministic(briefing_board, capsys):
    code1, out1 = _run_cli(capsys, "briefing", "--json")
    code2, out2 = _run_cli(capsys, "briefing", "--json")
    assert code1 == 0 and code2 == 0
    assert json.loads(out1) == json.loads(out2)
    data = json.loads(out1)
    assert data["queue_summary"]["total"] == 2
    assert data["top_eligible_tasks"][0]["task_id"] == "D-001"
    assert "docs/AGENTS.md" in data["protocol_pointers"]


# --- D-179 follow-up: Unicode-safe console ------------------------------------

def test_list_survives_console_charset_that_cannot_encode_task_title(
    fixture_board, no_op_git, capsys, monkeypatch, tmp_path
):
    """Regression (D-179 follow-up): task titles may contain characters a
    console's codepage can't encode (e.g. U+2192 on cp1251). `orchestrate
    list` used to raise UnicodeEncodeError and die when printing them. The
    CLI must reconfigure its streams (errors="replace") and print instead."""
    import io
    import sys

    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(
        "# Fixture Roadmap\n\n"
        "## H-001 — Decide the evaluation benchmark set \u2192\U0001F680\n\n"
        "**Priority: MEDIUM-LOW.**\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "empty_delegated")

    buf = io.BytesIO()
    narrow_stdout = io.TextIOWrapper(buf, encoding="cp1251", newline="\n")
    monkeypatch.setattr(sys, "stdout", narrow_stdout)

    code, _ = _run_cli(capsys, "list", "--status", "open")

    assert code == 0
    narrow_stdout.flush()
    out = buf.getvalue().decode("cp1251")
    assert "H-001" in out
