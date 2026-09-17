"""Repo-wide BM25 search over Markdown docs -- delegated task files,
reports, concerns, design docs, handoff notes, etc.

Deliberately a *separate* index from knowledge_search.py, not a
generalization of it: `knowledge_search.py` indexes structured
KNOW-*.md entries (YAML frontmatter: id/type/tags/keywords) and is
tuned for that schema. This module indexes plain Markdown by path,
with no assumed structure beyond "has a heading" -- delegated task
files, `reports/*_report.md`, `docs/**/*.md`, `concerns/*.md`, and
`.agent_prompts/*.md` don't share a common frontmatter contract, and
forcing them through the knowledge schema would mean either dropping
real content or inventing fields nothing actually fills in. Two
narrow, honest indexes beat one that pretends everything fits the
same shape.

Use this when looking for "where is X discussed/decided/reported" and
you don't know which file it's in. Use knowledge_search (via
`orchestrate knowledge search`) for durable knowledge entries
specifically -- those already have tags/type/confidence to filter on,
which this index does not model.
"""
import re
import sqlite3
from pathlib import Path
from typing import Optional

INDEX_PATH = Path("orchestration/.repo_search_index.db")

# Generated boards are regenerated from these same source files (or from
# tasks.yaml/concerns.yaml/knowledge.yaml) -- indexing them too would
# just duplicate hits and surface stale copies once the source changes.
EXCLUDED_FILES = {"STATUS.md", "CONCERNS_BOARD.md", "KNOWLEDGE_BOARD.md"}
EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                  "orchestration/contracts"}

_HEADING_RE = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", re.MULTILINE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(
    path UNINDEXED,
    title,
    body,
    tokenize = 'porter'
);
"""


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def _iter_markdown_files(root: Path):
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if str(rel) in EXCLUDED_FILES or path.name in EXCLUDED_FILES:
            continue
        yield path, rel


def _title_of(text: str, fallback: str) -> str:
    match = _HEADING_RE.search(text)
    return match.group(1) if match else fallback


def _source_signature(root: Path) -> str:
    if not root.exists():
        return "0:0"
    entries = [(p, r) for p, r in _iter_markdown_files(root)]
    if not entries:
        return "0:0"
    newest = max(p.stat().st_mtime for p, _ in entries)
    return f"{newest}:{len(entries)}"


def build_index(root: Optional[Path] = None, db_path: Optional[Path] = None,
                 force: bool = False) -> int:
    """(Re)build the repo-wide index if source files changed. Returns the
    number of documents indexed."""
    base = Path(root) if root else Path(".")
    conn = _connect(db_path)
    try:
        signature = _source_signature(base)
        if not force:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = 'signature'"
            ).fetchone()
            if row and row[0] == signature:
                return conn.execute("SELECT COUNT(*) FROM repo_fts").fetchone()[0]

        docs = []
        for path, rel in _iter_markdown_files(base):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            docs.append((rel.as_posix(), _title_of(text, rel.stem), text))

        conn.execute("DELETE FROM repo_fts")
        conn.executemany(
            "INSERT INTO repo_fts (path, title, body) VALUES (?, ?, ?)", docs
        )
        conn.execute(
            "INSERT INTO index_meta (key, value) VALUES ('signature', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (signature,),
        )
        conn.commit()
        return len(docs)
    finally:
        conn.close()


def search(query: str, root: Optional[Path] = None, db_path: Optional[Path] = None,
           path_prefix: Optional[str] = None, limit: int = 10) -> list:
    """Return [{path, title, score, snippet}] ranked best-first. `query`
    is passed through to FTS5 more or less as-is (quote phrases
    yourself if you want an exact phrase match) -- malformed queries
    fail soft to an empty list rather than raising.
    """
    if not query or not query.strip():
        return []

    build_index(root, db_path)
    conn = _connect(db_path)
    try:
        # snippet() must be computed in the same MATCH query it's ranking
        # against -- calling it in a separate lookup-by-rowid query loses
        # the match context and falls back to dumping the whole column.
        sql = (
            "SELECT path, title, bm25(repo_fts, 5.0, 1.0) AS rank, "
            "snippet(repo_fts, 2, '>>', '<<', '...', 12) AS snip "
            "FROM repo_fts WHERE repo_fts MATCH ?"
        )
        params: list = [query]
        if path_prefix:
            sql += " AND path LIKE ?"
            # Stored paths are posix (as_posix at index time); normalize a
            # Windows-style prefix so the filter matches on every platform.
            params.append(f"{path_prefix.replace('\\', '/')}%")
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(0, limit))
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "path": path,
                "title": title,
                "score": -rank,
                "snippet": snip.replace("\n", " ").strip(),
            }
            for path, title, rank, snip in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
