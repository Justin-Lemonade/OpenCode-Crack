"""
Shared SQLite schema-migration runner (D-230, bounded slice of C-070).

Every SQLite subsystem in this repo (facts_db, control_db, the usage
ledger) records a ``schema_version`` but, before this module, none of
them could actually migrate: two warn-and-run on mismatch, one hard-raises.
This runner is the mechanism C-070 requires for any future real schema
change ("reversible where practical / backed up / tested / versioned"):

- Migrations are applied strictly in ascending version order, each inside
  its own transaction, and the new version is appended to
  ``schema_version`` afterwards (append-only history — readers take
  MAX(version), matching the house pattern).
- Before the FIRST applied migration of a run, the DB file is copied
  aside (``shutil.copy2``, taken before any write connection is opened).
  That backup IS the reversibility story — there are deliberately no
  down-migrations.
- Validation happens before anything is touched: bad version lists raise
  ValueError without opening the database or writing a backup.
- A populated database with no readable version raises MigrationError
  instead of guessing.

Deliberately generic: this module imports nothing from the subsystems it
serves and knows nothing about their schemas. Wiring any subsystem onto
it — and authoring any real production migration — belongs to C-070's
owner, not here.
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


class MigrationError(RuntimeError):
    """A migration failed, or the DB state is not safely migratable.

    Carries the target ``version``, its ``description``, and the
    ``original`` exception (when failure came from inside ``apply``) so
    callers can report/report-forward without parsing message strings.
    """

    def __init__(
        self,
        message: str,
        *,
        version: int | None = None,
        description: str | None = None,
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.version = version
        self.description = description
        self.original = original


@dataclass(frozen=True)
class Migration:
    """One forward schema step. ``version`` is the version AFTER applying."""

    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _validate(migrations: list[Migration]) -> None:
    """Reject bad version lists before opening any connection."""
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise ValueError(f"Duplicate migration versions: {sorted(versions)}")
    if versions != sorted(versions):
        raise ValueError(
            f"Migration versions must be strictly ascending, got {versions}"
        )
    if any(v < 1 for v in versions):
        raise ValueError(f"Migration versions must be >= 1, got {versions}")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def _read_current_version(conn: sqlite3.Connection) -> int | None:
    """Max recorded version, or None when unreadable (missing/empty table)."""
    has_table = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not has_table:
        return None
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None:
        return None
    return int(row["v"])


def _current_version_or_zero(db_path: Path) -> int:
    """Resolve the starting version, honoring the refuse-to-guess rule.

    A missing/empty ``schema_version`` table means version 0 ONLY for a
    fresh database (no user tables yet). Anything else is ambiguous state
    we decline to migrate blind. A not-yet-existing file is version 0
    without opening a connection — sqlite3.connect would create the file,
    which would both defeat the no-backup-for-new-DB rule and leave a
    stray empty file behind on validation failures.
    """
    if not db_path.exists():
        return 0
    with _connect(db_path) as conn:
        current = _read_current_version(conn)
        if current is not None:
            return current
        if _user_tables(conn) - {"schema_version"}:
            raise MigrationError(
                f"{db_path} has user tables but no readable schema_version "
                "record — refusing to guess its version; initialize it with "
                "the owning subsystem's init_db() first."
            )
        return 0


def _make_backup(
    db_path: Path, current: int, target: int, backup_dir: Path | None
) -> Path | None:
    """Copy the DB file aside before the first applied migration.

    Returns the backup path, or None when there is no file to back up
    (brand-new database). The copy happens BEFORE any write connection is
    opened so it captures a quiescent file.
    """
    if not db_path.exists():
        return None
    dest_dir = backup_dir if backup_dir is not None else db_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = dest_dir / f"{db_path.stem}.pre-migration-v{current}-v{target}-{stamp}.bak"
    shutil.copy2(db_path, dest)
    return dest


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    """Create the tracking table if the DB predates having one.

    Only reachable for fresh databases migrating from 0; existing
    subsystems create their own (either shape) in their init paths. The
    created shape is the richer facts_db/control_db one.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER NOT NULL, migrated_at TEXT NOT NULL)"
    )


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    """Append the new version, handling both existing table shapes.

    facts_db/control_db use (version, migrated_at); usage_ledger uses
    (version) only. PRAGMA table_info tells us which we're facing.
    """
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(schema_version)").fetchall()
    }
    if not cols:
        _ensure_version_table(conn)
        cols = {"version", "migrated_at"}
    if "migrated_at" in cols:
        conn.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
    else:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def migrate(
    db_path: Path | str,
    migrations: Iterable[Migration],
    *,
    backup_dir: Path | str | None = None,
) -> list[int]:
    """Bring ``db_path`` up to date by applying pending migrations.

    Returns the list of versions actually applied ([] = already current,
    in which case no backup is written and no connection writes happen).
    Raises ValueError for malformed version lists (before touching the
    DB) and MigrationError for unsafe state or a failing migration (DB
    left at its previous version; backup left on disk for manual restore).
    """
    db_path = Path(db_path)
    planned = list(migrations)
    _validate(planned)

    current = _current_version_or_zero(db_path)
    pending = [m for m in planned if m.version > current]
    if not pending:
        return []

    target = pending[-1].version
    # Existence of this file on disk is the reversibility contract; the
    # runner deliberately keeps no handle to it beyond creation.
    _make_backup(
        db_path,
        current,
        target,
        Path(backup_dir) if backup_dir is not None else None,
    )

    # Autocommit mode: transaction boundaries are explicit per migration,
    # which keeps DDL and DML inside the same BEGIN..COMMIT scope.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    applied: list[int] = []
    try:
        for migration in pending:
            try:
                conn.execute("BEGIN")
                migration.apply(conn)
                _record_version(conn, migration.version)
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(
                    f"migration to v{migration.version} failed "
                    f"({migration.description}): {exc}",
                    version=migration.version,
                    description=migration.description,
                    original=exc,
                ) from exc
            applied.append(migration.version)
    finally:
        conn.close()
    return applied
