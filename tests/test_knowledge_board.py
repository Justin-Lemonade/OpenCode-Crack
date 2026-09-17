"""Tests for src/orchestrator/knowledge_board.py (D-314).

Fast + no network: isolates knowledge/, orchestration/knowledge.yaml, and
KNOWLEDGE_BOARD.md into tmp_path, and monkeypatches git calls, exactly
like tests/test_concerns_board.py does for concerns_board.
"""
import subprocess

import pytest

from opencode_crack.orchestrator import knowledge_board, knowledge_parser, knowledge_report


@pytest.fixture
def fixture_board(tmp_path, monkeypatch):
    knowledge_dir = tmp_path / "knowledge"
    board_path = tmp_path / "orchestration" / "knowledge.yaml"
    board_md_path = tmp_path / "KNOWLEDGE_BOARD.md"

    monkeypatch.setattr(knowledge_parser, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(knowledge_board, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(knowledge_board, "BOARD_PATH", board_path)
    monkeypatch.setattr(knowledge_board, "ACTIVITY_LOG", knowledge_dir / "activity.log")
    monkeypatch.setattr(knowledge_report, "BOARD_MD_PATH", board_md_path)
    return board_path


@pytest.fixture
def no_op_git(monkeypatch):
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(knowledge_board, "_run_git", fake_run_git)


def _file(**kw):
    base = dict(title="Something learned", body="body text", category="technique",
                submitted_by="agent-a", tags=["python"])
    base.update(kw)
    return knowledge_board.file_knowledge(**base)


# --- filing --------------------------------------------------------------

def test_file_knowledge_allocates_k_001_first(fixture_board, no_op_git):
    e = _file()
    assert e.id == "K-001"
    assert e.state.status == "unverified"
    assert e.category == "technique"
    assert e.tags == ["python"]


def test_file_knowledge_increments_id(fixture_board, no_op_git):
    _file()
    e2 = _file(title="Second")
    assert e2.id == "K-002"


def test_file_knowledge_writes_slug_markdown_file(fixture_board, no_op_git):
    knowledge_board.file_knowledge(
        title="Sentence Transformers Fails Under Python 3.14", body="details here",
        category="environment", submitted_by="agent-x", tags=["Python", " Embeddings "],
        related_task="D-260", supersedes="K-001",
    )
    matches = list(knowledge_board.KNOWLEDGE_DIR.glob("K-001_*.md"))
    assert len(matches) == 1
    assert "sentence-transformers-fails-under-pyt" in matches[0].name
    text = matches[0].read_text(encoding="utf-8")
    assert "## K-001 — Sentence Transformers Fails Under Python 3.14" in text
    assert "**Status:** unverified" in text
    assert "**Tags:** python, embeddings" in text
    assert "**Related task:** D-260" in text
    assert "**Supersedes:** K-001" in text


def test_file_knowledge_pull_failure_raises_before_writing(fixture_board, monkeypatch):
    def failing_pull(*args):
        if args[:2] == ("pull", "--rebase"):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="conflict")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(knowledge_board, "_run_git", failing_pull)
    with pytest.raises(RuntimeError, match="git pull failed"):
        _file()
    assert list(knowledge_board.KNOWLEDGE_DIR.glob("K-001*.md")) == []


def test_file_knowledge_rejects_invalid_category(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="Invalid category"):
        _file(category="nonsense")


def test_file_knowledge_rejects_empty_title_or_body(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="non-empty title"):
        _file(title="  ")
    with pytest.raises(ValueError, match="non-empty body"):
        _file(body="   ")


# --- status ladder --------------------------------------------------------

def test_full_ladder_transitions_succeed(fixture_board, no_op_git):
    e = _file()
    e = knowledge_board.mark_agent_verified(e.id, "agent-a", "ran repro script")
    assert e.state.status == "agent-verified"
    e = knowledge_board.mark_reviewer_verified(e.id, "claude", "checked evidence")
    assert e.state.status == "reviewer-verified"
    e = knowledge_board.confirm_knowledge(e.id, "claude")
    assert e.state.status == "confirmed"


def test_out_of_order_transition_raises(fixture_board, no_op_git):
    e = _file()
    with pytest.raises(RuntimeError, match="Cannot confirm"):
        knowledge_board.confirm_knowledge(e.id, "claude")
    with pytest.raises(RuntimeError, match="Cannot reviewer-verify"):
        knowledge_board.mark_reviewer_verified(e.id, "claude")


def test_mark_agent_verified_requires_verified_how(fixture_board, no_op_git):
    e = _file()
    with pytest.raises(ValueError, match="verified_how"):
        knowledge_board.mark_agent_verified(e.id, "agent-a", "")
    with pytest.raises(ValueError, match="verified_how"):
        knowledge_board.mark_agent_verified(e.id, "agent-a", "   ")


def test_reviewer_verify_from_wrong_status_raises(fixture_board, no_op_git):
    e = _file()
    knowledge_board.mark_agent_verified(e.id, "a", "checked")
    knowledge_board.mark_reviewer_verified(e.id, "c")
    with pytest.raises(RuntimeError, match="Cannot reviewer-verify"):
        knowledge_board.mark_reviewer_verified(e.id, "c")


def test_both_provenance_events_survive_full_ladder(fixture_board, no_op_git):
    e = _file()
    knowledge_board.mark_agent_verified(e.id, "agent-a", "ran repro script")
    e = knowledge_board.mark_reviewer_verified(e.id, "primary-claude", "reviewed evidence")
    assert e.state.agent_verified_by == "agent-a"
    assert e.state.agent_verified_at is not None
    assert e.state.verification_notes == "ran repro script"
    assert e.state.reviewer_verified_by == "primary-claude"
    assert e.state.reviewer_verified_at is not None
    # reviewer step must not clear the agent's provenance
    assert e.state.agent_verified_by != e.state.reviewer_verified_by


def test_retract_requires_notes(fixture_board, no_op_git):
    e = _file()
    with pytest.raises(ValueError, match="requires notes"):
        knowledge_board.retract_knowledge(e.id, "")
    retracted = knowledge_board.retract_knowledge(e.id, "repro was flawed")
    assert retracted.state.status == "retracted"


def test_transition_unknown_id_raises(fixture_board, no_op_git):
    with pytest.raises(ValueError, match="No such knowledge entry"):
        knowledge_board.mark_agent_verified("K-999", "a", "how")


# --- counters --------------------------------------------------------------

def test_mark_useful_increments_without_changing_status(fixture_board, no_op_git):
    e = _file()
    e = knowledge_board.mark_useful(e.id)
    assert e.state.useful_count == 1
    assert e.state.status == "unverified"
    e = knowledge_board.mark_useful(e.id)
    assert e.state.useful_count == 2
    assert e.state.status == "unverified"


def test_record_retrieval_increments_without_git(fixture_board, no_op_git, monkeypatch):
    e = _file()
    calls = []

    def fail_on_any_git(*args):
        calls.append(args)
        raise AssertionError(f"record_retrieval must not call git, called with {args}")

    monkeypatch.setattr(knowledge_board, "_run_git", fail_on_any_git)
    knowledge_board.record_retrieval(e.id)
    knowledge_board.record_retrieval(e.id)
    assert calls == []
    states = knowledge_board._load_states()
    assert states[e.id]["retrieval_count"] == 2


def test_supersede_sets_fields_without_changing_new(fixture_board, no_op_git):
    old = _file(title="Old approach")
    new = _file(title="New approach")
    result = knowledge_board.supersede_knowledge(old.id, new.id, "better method found")
    assert result.state.status == "superseded"
    assert result.state.superseded_by == new.id
    assert knowledge_board.get_knowledge(new.id).state.status == "unverified"


# --- listing / filtering ----------------------------------------------------

def test_list_knowledge_filters_by_status_category_tags(fixture_board, no_op_git):
    e1 = _file(title="Env note", category="environment", tags=["python", "docker"])
    e2 = _file(title="Tool note", category="tooling", tags=["git"])
    knowledge_board.mark_agent_verified(e1.id, "a", "checked")

    assert [e.id for e in knowledge_board.list_knowledge(status="agent-verified")] == [e1.id]
    assert [e.id for e in knowledge_board.list_knowledge(category="tooling")] == [e2.id]
    # tags = OR match
    assert {e.id for e in knowledge_board.list_knowledge(tags=["docker", "git"])} == {e1.id, e2.id}
    assert [e.id for e in knowledge_board.list_knowledge(tags=["python"])] == [e1.id]


def test_get_knowledge_returns_none_for_missing(fixture_board, no_op_git):
    assert knowledge_board.get_knowledge("K-999") is None


# --- sync -------------------------------------------------------------------

def test_sync_registers_new_md_files_as_unverified(fixture_board, no_op_git):
    knowledge_board.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    (knowledge_board.KNOWLEDGE_DIR / "K-001_manual.md").write_text(
        "## K-001 — Manually placed\n\n**Status:** draft\n**Category:** other\n"
        "**Tags:** a\n**Submitted by:** someone\n\nbody\n",
        encoding="utf-8",
    )
    entries = knowledge_board.sync()
    assert len(entries) == 1
    assert entries[0].state.status == "unverified"
    assert entries[0].state.created_at is not None


def test_sync_preserves_existing_state(fixture_board, no_op_git):
    e = _file()
    knowledge_board.mark_agent_verified(e.id, "a", "checked")
    knowledge_board.sync()
    reloaded = knowledge_board.get_knowledge(e.id)
    assert reloaded.state.status == "agent-verified"
    assert reloaded.state.agent_verified_by == "a"


# --- timestamp contract (mirrors D-263) --------------------------------------

def test_save_states_rejects_fractional_second_timestamp(fixture_board, no_op_git):
    states = knowledge_board._load_states()
    states["K-001"] = {
        "status": "unverified", "created_at": "2026-08-27T09:00:00.123456Z",
        "updated_at": "2026-08-27T09:00:00Z",
    }
    with pytest.raises(ValueError, match="does not match the required"):
        knowledge_board._save_states(states)


def test_save_states_accepts_well_formed_timestamp(fixture_board, no_op_git):
    from opencode_crack.orchestrator.timestamps import now
    states = knowledge_board._load_states()
    ts = now()
    states["K-001"] = {
        "status": "unverified", "created_at": ts, "updated_at": ts,
    }
    knowledge_board._save_states(states)  # must not raise
