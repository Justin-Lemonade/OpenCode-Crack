"""Tests for src/orchestrator/repo_search.py -- the repo-wide (non-knowledge)
search index over Markdown docs.
"""
from pathlib import Path

from opencode_crack.orchestrator import repo_search as rs


def _write(base: Path, rel: str, text: str) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestBuildIndex:
    def test_indexes_markdown_files(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Alpha\n\nabout widgets")
        _write(tmp_path, "reports/b.md", "# Beta\n\nabout gadgets")
        count = rs.build_index(root=tmp_path, db_path=tmp_path / "idx.db", force=True)
        assert count == 2

    def test_excludes_generated_boards(self, tmp_path):
        _write(tmp_path, "STATUS.md", "# Status\n\ngenerated")
        _write(tmp_path, "docs/a.md", "# Alpha\n\nreal content")
        count = rs.build_index(root=tmp_path, db_path=tmp_path / "idx.db", force=True)
        assert count == 1

    def test_excludes_dot_git_and_pycache(self, tmp_path):
        _write(tmp_path, ".git/hooks/note.md", "# Should not count")
        _write(tmp_path, "__pycache__/note.md", "# Should not count either")
        _write(tmp_path, "docs/a.md", "# Real doc")
        count = rs.build_index(root=tmp_path, db_path=tmp_path / "idx.db", force=True)
        assert count == 1

    def test_incremental_skip_when_unchanged(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Alpha\n\nwidgets")
        db_path = tmp_path / "idx.db"
        rs.build_index(root=tmp_path, db_path=db_path, force=True)
        count = rs.build_index(root=tmp_path, db_path=db_path)
        assert count == 1

    def test_rebuilds_on_new_file(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Alpha\n\nwidgets")
        db_path = tmp_path / "idx.db"
        rs.build_index(root=tmp_path, db_path=db_path, force=True)
        _write(tmp_path, "docs/b.md", "# Beta\n\ngadgets")
        count = rs.build_index(root=tmp_path, db_path=db_path)
        assert count == 2


class TestSearch:
    def test_finds_matching_doc(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Zeppelin design\n\nlighter than air craft")
        _write(tmp_path, "docs/b.md", "# Unrelated\n\nsomething else entirely")
        db_path = tmp_path / "idx.db"
        results = rs.search("zeppelin", root=tmp_path, db_path=db_path)
        assert len(results) == 1
        assert results[0]["path"] == "docs/a.md"
        assert results[0]["title"] == "Zeppelin design"

    def test_title_extracted_from_heading(self, tmp_path):
        _write(tmp_path, "reports/x.md", "## D-999 report title\n\nbody text here")
        db_path = tmp_path / "idx.db"
        results = rs.search("body text", root=tmp_path, db_path=db_path)
        assert results[0]["title"] == "D-999 report title"

    def test_path_prefix_filter(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Doc\n\nwidgets everywhere")
        _write(tmp_path, "reports/a.md", "# Report\n\nwidgets everywhere too")
        db_path = tmp_path / "idx.db"
        results = rs.search("widgets", root=tmp_path, db_path=db_path, path_prefix="docs/")
        assert len(results) == 1
        assert results[0]["path"] == "docs/a.md"

    def test_snippet_is_short_not_full_body(self, tmp_path):
        long_body = "filler word " * 200 + "findthisterm " + "filler word " * 200
        _write(tmp_path, "docs/a.md", f"# Doc\n\n{long_body}")
        db_path = tmp_path / "idx.db"
        results = rs.search("findthisterm", root=tmp_path, db_path=db_path)
        assert "findthisterm" in results[0]["snippet"].lower() or \
               ">>findthisterm<<" in results[0]["snippet"]
        # The whole point of snippet() is NOT returning the full 800+ word body.
        assert len(results[0]["snippet"]) < len(long_body)

    def test_no_match_returns_empty_list(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Doc\n\nwidgets")
        db_path = tmp_path / "idx.db"
        assert rs.search("nonexistentterm", root=tmp_path, db_path=db_path) == []

    def test_empty_query_returns_empty(self, tmp_path):
        db_path = tmp_path / "idx.db"
        assert rs.search("", root=tmp_path, db_path=db_path) == []
        assert rs.search("   ", root=tmp_path, db_path=db_path) == []

    def test_malformed_query_fails_soft(self, tmp_path):
        _write(tmp_path, "docs/a.md", "# Doc\n\nwidgets")
        db_path = tmp_path / "idx.db"
        assert rs.search('"', root=tmp_path, db_path=db_path) == []
