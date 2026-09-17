"""Tests for src/orchestrator/concerns_board.py.

Fast + no network: isolates concerns/, orchestration/concerns.yaml, and
CONCERNS_BOARD.md into tmp_path, and monkeypatches git calls, exactly
like tests/test_orchestrator.py does for task_board.
"""
import subprocess

import pytest

from opencode_crack.orchestrator import concern_parser, concerns_board, concerns_report


@pytest.fixture
def fixture_board(tmp_path, monkeypatch):
    concerns_dir = tmp_path / "concerns"
    board_path = tmp_path / "orchestration" / "concerns.yaml"
    board_md_path = tmp_path / "CONCERNS_BOARD.md"

    monkeypatch.setattr(concern_parser, "CONCERNS_DIR", concerns_dir)
    monkeypatch.setattr(concerns_board, "CONCERNS_DIR", concerns_dir)
    monkeypatch.setattr(concerns_board, "BOARD_PATH", board_path)
    monkeypatch.setattr(concerns_board, "ACTIVITY_LOG", concerns_dir / "activity.log")
    monkeypatch.setattr(concerns_report, "BOARD_MD_PATH", board_md_path)
    return board_path


@pytest.fixture
def no_op_git(monkeypatch):
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(concerns_board, "_run_git", fake_run_git)


# --- filing --------------------------------------------------------------

def test_file_concern_allocates_cn_001_first(fixture_board, no_op_git):
    c = concerns_board.file_concern(
        title="First concern", body="body text", severity="high",
        raised_by="agent-a", category="process",
    )
    assert c.id == "CN-001"
    assert c.state.status == "open"
    assert c.severity == "HIGH"
    assert c.category == "process"


def test_file_concern_increments_id(fixture_board, no_op_git):
    concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    c2 = concerns_board.file_concern(title="B", body="b", severity="low", raised_by="a")
    assert c2.id == "CN-002"


def test_file_concern_writes_markdown_file(fixture_board, no_op_git):
    concerns_board.file_concern(
        title="Spec ambiguity in D-300", body="Unclear what 'batch' means.",
        severity="medium", raised_by="agent-x", category="process",
        related_task="D-300",
    )
    matches = list(concerns_board.CONCERNS_DIR.glob("CN-001_*.md"))
    assert len(matches) == 1
    text = matches[0].read_text(encoding="utf-8")
    assert "## CN-001 — Spec ambiguity in D-300" in text
    assert "**Severity:** MEDIUM" in text
    assert "**Related task:** D-300" in text
    assert "Unclear what 'batch' means." in text


def test_file_concern_filename_embeds_creation_date(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    today = c.state.created_at[:10]
    assert (concerns_board.CONCERNS_DIR / f"CN-001_{today}.md").exists()


def test_file_concern_regenerates_board_md(fixture_board, no_op_git):
    concerns_board.file_concern(title="A", body="b", severity="blocker", raised_by="a")
    md = concerns_report.BOARD_MD_PATH.read_text(encoding="utf-8")
    assert "CN-001" in md
    assert "BLOCKER" in md


def test_file_concern_rejects_invalid_severity(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="Invalid severity"):
        concerns_board.file_concern(title="A", body="b", severity="URGENT!!!", raised_by="a")


def test_file_concern_rejects_invalid_category(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="Invalid category"):
        concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a", category="nonsense")


def test_file_concern_rejects_empty_title_or_body(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="non-empty title"):
        concerns_board.file_concern(title="  ", body="b", severity="low", raised_by="a")
    with pytest.raises(ValueError, match="non-empty body"):
        concerns_board.file_concern(title="A", body="   ", severity="low", raised_by="a")


def test_file_concern_pull_failure_raises_before_writing(fixture_board, monkeypatch):
    def failing_pull(*args):
        if args[:2] == ("pull", "--rebase"):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="conflict")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(concerns_board, "_run_git", failing_pull)
    with pytest.raises(RuntimeError, match="git pull failed"):
        concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    assert list(concerns_board.CONCERNS_DIR.glob("CN-001*.md")) == []


def test_file_concern_sets_created_and_updated_but_not_resolved_at(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    assert c.state.created_at is not None
    assert c.state.updated_at is not None
    assert c.state.resolved_at is None


# --- lifecycle transitions ------------------------------------------------

def test_acknowledge_from_open(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    acked = concerns_board.acknowledge_concern(c.id)
    assert acked.state.status == "acknowledged"


def test_acknowledge_from_non_open_raises(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    concerns_board.acknowledge_concern(c.id)
    with pytest.raises(RuntimeError, match="Cannot acknowledge"):
        concerns_board.acknowledge_concern(c.id)


def test_resolve_requires_notes(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    with pytest.raises(ValueError, match="requires notes"):
        concerns_board.resolve_concern(c.id, "")


def test_resolve_from_open_and_from_acknowledged(fixture_board, no_op_git):
    c1 = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    resolved = concerns_board.resolve_concern(c1.id, "fixed directly")
    assert resolved.state.status == "resolved"
    assert resolved.state.resolution_notes == "fixed directly"
    assert resolved.state.resolved_at is not None

    c2 = concerns_board.file_concern(title="B", body="b", severity="low", raised_by="a")
    concerns_board.acknowledge_concern(c2.id)
    resolved2 = concerns_board.resolve_concern(c2.id, "fixed after review")
    assert resolved2.state.status == "resolved"
    assert resolved2.state.resolved_at is not None


def test_dismiss_requires_notes(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    with pytest.raises(ValueError, match="requires notes"):
        concerns_board.dismiss_concern(c.id, "")


def test_dismiss_sets_wontfix(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    dismissed = concerns_board.dismiss_concern(c.id, "not actionable, by design")
    assert dismissed.state.status == "wontfix"
    assert dismissed.state.resolution_notes == "not actionable, by design"
    assert dismissed.state.resolved_at is not None


def test_reopen_clears_resolved_at(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    concerns_board.resolve_concern(c.id, "done")
    reopened = concerns_board.reopen_concern(c.id, "recurred")
    assert reopened.state.resolved_at is None


def test_reopen_from_resolved_and_wontfix(fixture_board, no_op_git):
    c1 = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    concerns_board.resolve_concern(c1.id, "done")
    reopened = concerns_board.reopen_concern(c1.id, "recurred")
    assert reopened.state.status == "open"

    c2 = concerns_board.file_concern(title="B", body="b", severity="low", raised_by="a")
    concerns_board.dismiss_concern(c2.id, "wontfix")
    reopened2 = concerns_board.reopen_concern(c2.id)
    assert reopened2.state.status == "open"


def test_reopen_from_open_raises(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    with pytest.raises(RuntimeError, match="Cannot reopen"):
        concerns_board.reopen_concern(c.id)


def test_transition_unknown_id_raises(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="No such concern"):
        concerns_board.acknowledge_concern("CN-999")


# --- listing / filtering ---------------------------------------------------

def test_list_concerns_filters_by_status(fixture_board, no_op_git):
    c1 = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    concerns_board.file_concern(title="B", body="b", severity="low", raised_by="a")
    concerns_board.resolve_concern(c1.id, "done")

    open_only = concerns_board.list_concerns(status="open")
    assert [c.id for c in open_only] == ["CN-002"]


def test_list_concerns_filters_by_severity(fixture_board, no_op_git):
    concerns_board.file_concern(title="A", body="b", severity="blocker", raised_by="a")
    concerns_board.file_concern(title="B", body="b", severity="low", raised_by="a")

    blockers = concerns_board.list_concerns(severity="blocker")
    assert [c.id for c in blockers] == ["CN-001"]


def test_get_concern_returns_none_for_missing(fixture_board, no_op_git):
    assert concerns_board.get_concern("CN-999") is None


# --- sync ------------------------------------------------------------------

def test_sync_registers_new_md_files_as_open(fixture_board, no_op_git):
    concerns_board.CONCERNS_DIR.mkdir(parents=True, exist_ok=True)
    (concerns_board.CONCERNS_DIR / "CN-001.md").write_text(
        "## CN-001 — Manually placed\n\n**Severity:** LOW\n**Category:** other\n"
        "**Raised by:** someone\n\nbody\n",
        encoding="utf-8",
    )
    concerns = concerns_board.sync()
    assert len(concerns) == 1
    assert concerns[0].state.status == "open"
    assert concerns[0].state.created_at is not None


def test_sync_preserves_existing_state(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    concerns_board.resolve_concern(c.id, "already handled")
    concerns_board.sync()
    reloaded = concerns_board.get_concern(c.id)
    assert reloaded.state.status == "resolved"
    assert reloaded.state.resolution_notes == "already handled"


# --- staleness ---------------------------------------------------------

def test_is_stale_false_for_resolved(fixture_board, no_op_git):
    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    resolved = concerns_board.resolve_concern(c.id, "done")
    assert concerns_board.is_stale(resolved) is False


def test_is_stale_true_for_old_open_concern(fixture_board, no_op_git):
    from datetime import datetime, timedelta, timezone

    c = concerns_board.file_concern(title="A", body="b", severity="low", raised_by="a")
    far_future = datetime.now(timezone.utc) + timedelta(hours=concerns_board.STALE_AFTER_HOURS + 1)
    assert concerns_board.is_stale(c, now=far_future) is True


# --- timestamp contract (mirrors D-263) -------------------------------

def test_save_states_rejects_fractional_second_timestamp(fixture_board, no_op_git):
    states = concerns_board._load_states()
    states["CN-001"] = {
        "status": "open", "created_at": "2026-08-27T09:00:00.123456Z",
        "updated_at": "2026-08-27T09:00:00Z", "resolution_notes": "",
    }
    with pytest.raises(ValueError, match="does not match the required"):
        concerns_board._save_states(states)


def test_save_states_accepts_well_formed_timestamp(fixture_board, no_op_git):
    from opencode_crack.orchestrator.timestamps import now
    states = concerns_board._load_states()
    ts = now()
    states["CN-001"] = {
        "status": "open", "created_at": ts, "updated_at": ts, "resolution_notes": "",
    }
    concerns_board._save_states(states)  # must not raise
