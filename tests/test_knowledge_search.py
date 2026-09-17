"""Tests for src/orchestrator/knowledge_search.py: the FTS5 BM25 index
that replaced the substring-scan ranking in durable_knowledge.search_entries().
"""
from pathlib import Path

import pytest

from opencode_crack.orchestrator import knowledge_search as ks
from opencode_crack.orchestrator import durable_knowledge as dk


def _write_entry(directory: Path, entry_id: str, title: str, body: str,
                  tags: str = "[]") -> Path:
    path = directory / f"{entry_id}.md"
    path.write_text(
        f"""---
id: {entry_id}
type: learning
title: "{title}"
created_at: "2026-09-08T00:00:00Z"
updated_at: "2026-09-08T00:00:00Z"
status: active
tags: {tags}
---
{body}
""",
        encoding="utf-8",
    )
    return path


class TestBuildIndex:
    def test_indexes_all_entries(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "content about widgets")
        _write_entry(tmp_path, "KNOW-20260908-002", "Beta", "content about gadgets")
        db_path = tmp_path / "idx.db"
        count = ks.build_index(directory=tmp_path, db_path=db_path, force=True)
        assert count == 2

    def test_incremental_skip_when_unchanged(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "widgets")
        db_path = tmp_path / "idx.db"
        ks.build_index(directory=tmp_path, db_path=db_path, force=True)
        # Second call with no source changes and force=False should reuse
        # the existing index (same behavior/count) rather than re-scan.
        count = ks.build_index(directory=tmp_path, db_path=db_path)
        assert count == 1

    def test_rebuilds_when_new_file_added(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "widgets")
        db_path = tmp_path / "idx.db"
        ks.build_index(directory=tmp_path, db_path=db_path, force=True)
        _write_entry(tmp_path, "KNOW-20260908-002", "Beta", "gadgets")
        count = ks.build_index(directory=tmp_path, db_path=db_path)
        assert count == 2


class TestBM25Scores:
    def test_ranks_title_match_above_body_only_match(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Zeppelin architecture",
                     "unrelated body text")
        _write_entry(tmp_path, "KNOW-20260908-002", "Unrelated title",
                     "mentions zeppelin once in passing")
        db_path = tmp_path / "idx.db"
        scores = ks.bm25_scores(["zeppelin"], directory=tmp_path, db_path=db_path)
        assert set(scores) == {"KNOW-20260908-001", "KNOW-20260908-002"}
        assert scores["KNOW-20260908-001"] > scores["KNOW-20260908-002"]

    def test_no_match_returns_empty(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "widgets")
        db_path = tmp_path / "idx.db"
        scores = ks.bm25_scores(["nonexistentterm"], directory=tmp_path, db_path=db_path)
        assert scores == {}

    def test_empty_keywords_returns_empty(self, tmp_path):
        db_path = tmp_path / "idx.db"
        assert ks.bm25_scores([], directory=tmp_path, db_path=db_path) == {}

    def test_malformed_query_fails_soft(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "widgets")
        db_path = tmp_path / "idx.db"
        # A lone hyphen/quote is invalid FTS5 query syntax -- should not raise.
        scores = ks.bm25_scores(['"'], directory=tmp_path, db_path=db_path)
        assert scores == {}


class TestSearchEntriesIntegration:
    def test_search_entries_uses_bm25_ranking(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Zeppelin architecture",
                     "unrelated body text")
        _write_entry(tmp_path, "KNOW-20260908-002", "Unrelated title",
                     "mentions zeppelin once in passing")
        results = dk.search_entries(keywords=["zeppelin"], directory=tmp_path,
                                     limit=10)
        ids = [r["id"] for r in results]
        assert ids[0] == "KNOW-20260908-001"  # title hit ranks first

    def test_search_entries_no_keywords_still_works(self, tmp_path):
        _write_entry(tmp_path, "KNOW-20260908-001", "Alpha", "widgets")
        results = dk.search_entries(keywords=[], directory=tmp_path, limit=10)
        assert len(results) == 1


# ===========================================================================
# knowledge_board.search_relevant() -- C-75: automatic contract injection.
#
# This is a DIFFERENT function from everything above. The classes above
# test durable_knowledge.py's FTS5-backed KNOW-*.md search. This section
# tests knowledge_board.py's tag/keyword-overlap search over K-*.md
# entries -- the ranking C-75 specifies for automatic RELEVANT PRIOR
# KNOWLEDGE injection into task contracts. Same file per C-75's own
# acceptance criteria; kept in a clearly separated section rather than a
# new file to avoid a second near-identical "test_knowledge_*" filename.
# ===========================================================================
import subprocess as _subprocess

from opencode_crack.orchestrator import knowledge_board as kb
from opencode_crack.orchestrator import knowledge_parser as kp
from opencode_crack.orchestrator import knowledge_report as kr


@pytest.fixture
def kb_fixture(tmp_path, monkeypatch):
    knowledge_dir = tmp_path / "knowledge"
    board_path = tmp_path / "orchestration" / "knowledge.yaml"
    board_md_path = tmp_path / "KNOWLEDGE_BOARD.md"

    monkeypatch.setattr(kp, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(kb, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(kb, "BOARD_PATH", board_path)
    monkeypatch.setattr(kb, "ACTIVITY_LOG", knowledge_dir / "activity.log")
    monkeypatch.setattr(kr, "BOARD_MD_PATH", board_md_path)

    def fake_run_git(*args):
        return _subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(kb, "_run_git", fake_run_git)
    return board_path


def _file(**kw):
    base = dict(title="Something learned", body="body text", category="technique",
                submitted_by="agent-a", tags=["python"], publish=False)
    base.update(kw)
    return kb.file_knowledge(**base)


def _file_verified(**kw):
    """File + advance straight to reviewer-verified (the default eligible
    status for search_relevant's automatic-injection path). Ladder is
    unverified -> agent-verified -> reviewer-verified, both steps required."""
    e = _file(**kw)
    kb.mark_agent_verified(e.id, "agent-a", "self-checked")
    return kb.mark_reviewer_verified(e.id, "reviewer", "checked")


class TestSearchRelevant:
    def test_exact_tag_match_scores_higher_than_partial(self, kb_fixture):
        exact = _file_verified(title="Worktree isolation fact",
                                tags=["worktree", "isolation"])
        partial = _file_verified(title="Unrelated entry", tags=["isolation"])
        task = {"title": "Fix worktree isolation bug", "body": "worktree isolation"}
        results = kb.search_relevant(task)
        ids = [e.id for e in results]
        assert ids[0] == exact.id
        assert partial.id in ids  # still matches (1 tag), just ranked lower

    def test_unverified_excluded_by_default(self, kb_fixture):
        e = _file(title="Fresh unverified note", tags=["worktree"])
        task = {"title": "worktree work", "body": ""}
        results = kb.search_relevant(task)
        assert e.id not in [r.id for r in results]

    def test_unverified_included_when_requested(self, kb_fixture):
        e = _file(title="Fresh unverified note", tags=["worktree"])
        task = {"title": "worktree work", "body": ""}
        results = kb.search_relevant(task, include_unverified=True)
        assert e.id in [r.id for r in results]

    def test_confirmed_entries_are_eligible(self, kb_fixture):
        e = _file(title="Confirmed fact", tags=["worktree"])
        kb.mark_agent_verified(e.id, "agent-a", "self-checked")
        kb.mark_reviewer_verified(e.id, "r", "ok")
        kb.confirm_knowledge(e.id, "human")
        task = {"title": "worktree work", "body": ""}
        results = kb.search_relevant(task)
        assert e.id in [r.id for r in results]

    def test_record_retrieval_called_only_on_returned_entries(self, kb_fixture):
        matched = _file_verified(title="Matched", tags=["worktree"])
        unmatched = _file_verified(title="Not matched at all", tags=["banking"])
        task = {"title": "worktree work", "body": ""}
        kb.search_relevant(task)
        matched_after = kb.get_knowledge(matched.id)
        unmatched_after = kb.get_knowledge(unmatched.id)
        assert matched_after.state.retrieval_count == 1
        assert unmatched_after.state.retrieval_count == 0

    def test_superseded_entry_excluded_when_successor_also_candidate(self, kb_fixture):
        old = _file_verified(title="Old fact", tags=["worktree", "isolation"])
        new = _file_verified(title="New fact", tags=["worktree", "isolation"])
        kb.supersede_knowledge(old.id, new.id, "corrected")
        task = {"title": "worktree isolation", "body": ""}
        results = kb.search_relevant(task)
        ids = [r.id for r in results]
        assert old.id not in ids
        assert new.id in ids

    def test_empty_knowledge_base_returns_empty_list(self, kb_fixture):
        task = {"title": "anything", "body": "at all"}
        assert kb.search_relevant(task) == []

    def test_limit_respected(self, kb_fixture):
        for i in range(5):
            _file_verified(title=f"Entry {i}", tags=["worktree"])
        task = {"title": "worktree", "body": ""}
        results = kb.search_relevant(task, limit=2)
        assert len(results) == 2

    def test_ties_broken_by_useful_count_then_retrieval_count(self, kb_fixture):
        a = _file_verified(title="A", tags=["worktree"])
        b = _file_verified(title="B", tags=["worktree"])
        kb.mark_useful(a.id)  # a has useful_count=1, b has 0 -- a should rank first
        task = {"title": "worktree", "body": ""}
        results = kb.search_relevant(task)
        assert [r.id for r in results] == [a.id, b.id]

    def test_zero_score_entries_dropped_not_padded(self, kb_fixture):
        _file_verified(title="Totally unrelated", tags=["banking"])
        task = {"title": "worktree isolation bug", "body": ""}
        assert kb.search_relevant(task) == []

    def test_no_keyword_overlap_with_task_returns_empty(self, kb_fixture):
        _file_verified(title="Something", tags=["python", "testing"])
        task = {"title": "completely different subject matter", "body": "nothing shared"}
        assert kb.search_relevant(task) == []

    def test_accepts_plain_string_task(self, kb_fixture):
        e = _file_verified(title="Worktree fact", tags=["worktree"])
        results = kb.search_relevant("fix the worktree bug")
        assert e.id in [r.id for r in results]
