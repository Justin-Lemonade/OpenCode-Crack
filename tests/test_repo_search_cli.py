"""Tests for the 'orchestrate search' CLI subcommand (repo-wide search,
distinct from 'orchestrate knowledge search')."""
import json

from opencode_crack.orchestrator.cli import run_orchestrate
from opencode_crack.orchestrator import repo_search


def test_search_no_results(monkeypatch, capsys):
    monkeypatch.setattr(repo_search, "search", lambda *a, **k: [])
    run_orchestrate(["search", "totally-unmatched-query"])
    out = capsys.readouterr().out
    assert "No matching documents found." in out


def test_search_prints_results(monkeypatch, capsys):
    fake_results = [
        {"path": "docs/a.md", "title": "Alpha", "score": 5.5, "snippet": "a >>match<< here"},
    ]
    monkeypatch.setattr(repo_search, "search", lambda *a, **k: fake_results)
    run_orchestrate(["search", "match"])
    out = capsys.readouterr().out
    assert "docs/a.md" in out
    assert "Alpha" in out
    assert "1 result(s)" in out


def test_search_json_output(monkeypatch, capsys):
    fake_results = [
        {"path": "docs/a.md", "title": "Alpha", "score": 5.5, "snippet": "snip"},
    ]
    monkeypatch.setattr(repo_search, "search", lambda *a, **k: fake_results)
    run_orchestrate(["search", "match", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == fake_results


def test_search_passes_path_and_limit(monkeypatch):
    captured = {}

    def fake_search(query, path_prefix=None, limit=10):
        captured["query"] = query
        captured["path_prefix"] = path_prefix
        captured["limit"] = limit
        return []

    monkeypatch.setattr(repo_search, "search", fake_search)
    run_orchestrate(["search", "foo", "--path", "docs/", "--limit", "3"])
    assert captured == {"query": "foo", "path_prefix": "docs/", "limit": 3}
