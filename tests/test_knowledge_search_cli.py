"""Tests for the 'orchestrate knowledge search' CLI subcommand's --json
flag (mirrors tests/test_repo_search_cli.py for 'orchestrate search').

Filed alongside the gap-audit fix that added --json here: 'orchestrate
search' (repo_search) already had --json, but 'orchestrate knowledge
search' (durable KNOW-*.md entries) did not, so agents/scripts could
only consume durable-knowledge results as formatted text.
"""
import json

from opencode_crack.orchestrator.cli import run_orchestrate
from opencode_crack.orchestrator import knowledge_board as kb


def test_knowledge_search_no_results(monkeypatch, capsys):
    monkeypatch.setattr(kb, "search_durable_knowledge", lambda **k: [])
    run_orchestrate(["knowledge", "search", "--query", "totally-unmatched"])
    out = capsys.readouterr().out
    assert "No matching durable knowledge entries found." in out


def test_knowledge_search_prints_text_by_default(monkeypatch, capsys):
    fake_results = [
        {"id": "KNOW-20260910-001", "title": "Alpha", "type": "learning",
         "status": "active", "confidence": "medium", "path": "knowledge/a.md",
         "relevance_score": 3.5, "tags": [], "task_ids": [], "files": [],
         "useful_count": 0, "retrieval_count": 0},
    ]
    monkeypatch.setattr(kb, "search_durable_knowledge", lambda **k: fake_results)
    run_orchestrate(["knowledge", "search", "--query", "alpha"])
    out = capsys.readouterr().out
    assert "KNOW-20260910-001" in out
    assert "1 result(s)" in out
    # Should not be valid JSON by default -- confirms text is the default mode
    try:
        json.loads(out)
        assert False, "expected non-JSON text output by default"
    except json.JSONDecodeError:
        pass


def test_knowledge_search_json_output(monkeypatch, capsys):
    fake_results = [
        {"id": "KNOW-20260910-001", "title": "Alpha", "type": "learning",
         "status": "active", "confidence": "medium", "path": "knowledge/a.md",
         "relevance_score": 3.5, "tags": ["x"], "task_ids": [], "files": [],
         "useful_count": 0, "retrieval_count": 0},
    ]
    monkeypatch.setattr(kb, "search_durable_knowledge", lambda **k: fake_results)
    run_orchestrate(["knowledge", "search", "--query", "alpha", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == fake_results


def test_knowledge_search_json_respects_status_filter(monkeypatch, capsys):
    fake_results = [
        {"id": "KNOW-1", "title": "A", "type": "learning", "status": "active",
         "confidence": "medium", "path": "a.md", "relevance_score": 1.0,
         "tags": [], "task_ids": [], "files": [], "useful_count": 0,
         "retrieval_count": 0},
        {"id": "KNOW-2", "title": "B", "type": "learning", "status": "resolved",
         "confidence": "medium", "path": "b.md", "relevance_score": 1.0,
         "tags": [], "task_ids": [], "files": [], "useful_count": 0,
         "retrieval_count": 0},
    ]
    monkeypatch.setattr(kb, "search_durable_knowledge", lambda **k: fake_results)
    run_orchestrate(["knowledge", "search", "--query", "x", "--status", "active", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert [e["id"] for e in parsed] == ["KNOW-1"]
