"""BM25 search index for durable knowledge entries (KNOW-*.md).

Why this exists (C-075 / D-317): `durable_knowledge.search_entries()`
previously ranked keyword matches with a hand-rolled substring scan
(TITLE_HIT/TAGKW_HIT/BODY_HIT constants) re-run over every entry, on
every call, with no persistent index. That's O(n) file reads per
search and no real relevance model (no term frequency, no field
weighting beyond a flat per-hit bonus).

This module builds a local SQLite FTS5 index (bundled with Python's
stdlib `sqlite3` -- no new dependency, no new toolchain, works in
every agent lane the same way `durable_knowledge.py` already does)
and exposes BM25-ranked scores keyed by entry ID. `search_entries()`
uses those scores instead of the substring loop, but keeps its
existing hard-filter pipeline (tags/type/task/file/status) untouched,
so the CLI and call signature are unaffected.

We evaluated Grepdown (Rust CLI, SQLite FTS5 under the hood) as an
external tool -- it's a real, actively maintained project -- but it
would add a Rust toolchain / prebuilt-binary dependency across every
agent lane (Primary Claude sessions, OpenCode, Kilo, Justin's
machine) for a system that is otherwise pure Python. Building the same
FTS5 index directly in Python gets equivalent ranking quality with
zero new deployment surface. Revisit Grepdown/QMD/Fabryk if we later
need semantic (embedding) search -- BM25 keyword search does not
cover synonyms or paraphrase.

The index is a local build artifact (`orchestration/.knowledge_search_index.db`,
gitignored via the existing `*.db` rule) -- never committed, rebuilt
per-lane on demand.
"""
import sqlite3
from pathlib import Path
from typing import Optional

from . import durable_knowledge as dk

INDEX_PATH = Path("orchestration/.knowledge_search_index.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    id UNINDEXED,
    title,
    tags,
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


def _source_signature(directory: Optional[Path] = None) -> str:
    """Cheap staleness fingerprint: newest mtime + file count.

    Good enough to catch adds/edits without re-reading every file's
    content on every search call -- that's the whole point of having
    an index instead of `durable_knowledge.discover()`'s linear scan.
    """
    base = dk._resolve_dir(directory)
    if not base.exists():
        return "0:0"
    paths = sorted(base.rglob("KNOW-*.md"))
    if not paths:
        return "0:0"
    newest = max(p.stat().st_mtime for p in paths)
    return f"{newest}:{len(paths)}"


def build_index(directory: Optional[Path] = None, db_path: Optional[Path] = None,
                 force: bool = False) -> int:
    """(Re)build the FTS5 index if the source files changed since the last
    build. Returns the number of entries indexed. Set force=True to
    rebuild unconditionally (e.g. after changing tokenization/schema).
    """
    conn = _connect(db_path)
    try:
        signature = _source_signature(directory)
        if not force:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = 'signature'"
            ).fetchone()
            if row and row[0] == signature:
                count = conn.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
                return count

        entries = dk.discover(directory)
        conn.execute("DELETE FROM knowledge_fts")
        conn.executemany(
            "INSERT INTO knowledge_fts (id, title, tags, body) VALUES (?, ?, ?, ?)",
            [
                (e.id, e.title, " ".join(e.tags + e.keywords), e.body or "")
                for e in entries
            ],
        )
        conn.execute(
            "INSERT INTO index_meta (key, value) VALUES ('signature', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (signature,),
        )
        conn.commit()
        return len(entries)
    finally:
        conn.close()


def bm25_scores(keywords: list, directory: Optional[Path] = None,
                 db_path: Optional[Path] = None) -> dict:
    """Return {entry_id: score} for entries matching ANY keyword, best
    first by score (higher is better -- we negate FTS5's bm25(), which
    is lower-is-better). Entries with no keyword match are simply
    absent from the dict, matching search_entries()'s "keywords given
    but nothing matched -> excluded" contract.

    Field weights (title, tags, body) mirror the old TITLE_HIT/
    TAGKW_HIT/BODY_HIT ordering: title matches rank highest.
    """
    kws = [k for k in keywords if k]
    if not kws:
        return {}

    build_index(directory, db_path)
    conn = _connect(db_path)
    try:
        # FTS5 MATCH query: OR together per-column phrase probes so any
        # single keyword hit qualifies, same as the old substring "any
        # keyword anywhere" behavior.
        match_expr = " OR ".join(f'"{kw}"' for kw in kws)
        rows = conn.execute(
            "SELECT id, bm25(knowledge_fts, 10.0, 5.0, 1.0) AS rank "
            "FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank",
            (match_expr,),
        ).fetchall()
        # bm25() is lower-is-better and unbounded; flip sign so callers
        # can treat higher-is-better like the rest of the scoring code.
        return {row[0]: -row[1] for row in rows}
    except sqlite3.OperationalError:
        # Malformed query (e.g. a keyword that's pure FTS5 syntax like
        # a lone hyphen) -- fail soft to "no matches" rather than
        # crashing a search.
        return {}
    finally:
        conn.close()
