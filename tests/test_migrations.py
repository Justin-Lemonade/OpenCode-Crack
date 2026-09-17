"""
Tests for the shared SQLite migration runner (D-230, slice of C-070).

All schemas here are synthetic fixtures — this task deliberately makes no
production schema change, so nothing imports facts_db/control_db/
usage_ledger. Offline and tmp_path-only per repo convention.
"""
import sqlite3
from pathlib import Path

import pytest

from opencode_crack.storage.migrations import Migration, MigrationError, migrate


def make_v1_db(path: Path) -> None:
    """A fake subsystem baseline: one business table + the richer
    (version, migrated_at) tracking shape used by facts_db/control_db."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE schema_version (
            version INTEGER NOT NULL,
            migrated_at TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (1, '2026-01-01T00:00:00+00:00');
        INSERT INTO items (name) VALUES ('alpha');
        """
    )
    conn.commit()
    conn.close()


def current_version(db_path: Path) -> int | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return None if row is None or row["v"] is None else int(row["v"])
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def backups_in(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.pre-migration-*.bak"))


def add_notes_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes (body) VALUES ('migrated')")


# --- no-op path ----------------------------------------------------------


def test_already_current_is_noop_without_backup(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    result = migrate(db, [Migration(1, "baseline", lambda c: None)])

    assert result == []
    assert current_version(db) == 1
    assert backups_in(tmp_path) == []


def test_fresh_db_with_no_migrations_is_noop(tmp_path):
    db = tmp_path / "empty.sqlite"
    db.touch()

    assert migrate(db, []) == []
    assert list(tmp_path.glob("*")) == [db]


# --- happy path ----------------------------------------------------------


def test_applies_pending_migration_and_backs_up(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    result = migrate(db, [Migration(2, "add notes table", add_notes_table)])

    assert result == [2]
    assert current_version(db) == 2

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT body FROM notes").fetchall()
    items = conn.execute("SELECT name FROM items").fetchall()
    conn.close()
    assert rows == [("migrated",)]
    assert items == [("alpha",)]  # pre-existing data preserved

    made = backups_in(tmp_path)
    assert len(made) == 1
    assert "pre-migration-v1-v2-" in made[0].name
    assert made[0].read_bytes() != db.read_bytes()


def test_multiple_migrations_apply_in_order(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    def add_col_a(conn):
        conn.execute("ALTER TABLE items ADD COLUMN flag_a INTEGER DEFAULT 0")

    def backfill_flag_a(conn):
        conn.execute("UPDATE items SET flag_a = 1")

    result = migrate(
        db,
        [
            Migration(2, "add flag_a", add_col_a),
            Migration(3, "backfill flag_a", backfill_flag_a),
        ],
    )

    assert result == [2, 3]
    assert current_version(db) == 3
    conn = sqlite3.connect(db)
    flags = [r[0] for r in conn.execute("SELECT flag_a FROM items")]
    conn.close()
    assert flags == [1]


def test_migrations_below_current_are_skipped(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        INSERT INTO schema_version VALUES (2, '2026-01-02T00:00:00+00:00');
        INSERT INTO schema_version VALUES (3, '2026-01-03T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    applied_calls = []

    def spy(conn):
        applied_calls.append(True)

    result = migrate(
        db,
        [
            Migration(2, "old v2", spy),
            Migration(3, "old v3", spy),
            Migration(4, "only this runs", spy),
        ],
    )

    assert result == [4]
    assert len(applied_calls) == 1
    assert current_version(db) == 4


def test_backup_dir_override(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)
    elsewhere = tmp_path / "backups"

    migrate(db, [Migration(2, "add notes", add_notes_table)], backup_dir=elsewhere)

    assert backups_in(tmp_path) == []
    assert len(backups_in(elsewhere)) == 1


# --- validation failures (before anything is touched) --------------------


@pytest.mark.parametrize(
    "bad",
    [
        [Migration(2, "a", lambda c: None), Migration(2, "dup", lambda c: None)],
        [Migration(2, "a", lambda c: None), Migration(1, "descends", lambda c: None)],
        [Migration(0, "zero", lambda c: None)],
    ],
)
def test_invalid_version_lists_raise_value_error_untouched(tmp_path, bad):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    with pytest.raises(ValueError):
        migrate(db, bad)

    assert current_version(db) == 1
    assert backups_in(tmp_path) == []


# --- failing migration ---------------------------------------------------


def test_failed_migration_rolls_back_and_keeps_previous_version(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    def doomed(conn):
        conn.execute("CREATE TABLE partial (id INTEGER)")
        conn.execute("INSERT INTO partial VALUES (1)")
        raise RuntimeError("boom mid-migration")

    with pytest.raises(MigrationError) as excinfo:
        migrate(db, [Migration(2, "doomed step", doomed)])

    err = excinfo.value
    assert err.version == 2
    assert err.description == "doomed step"
    assert isinstance(err.original, RuntimeError)
    assert str(err.original) == "boom mid-migration"

    assert current_version(db) == 1
    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "partial" not in tables  # partial writes rolled back
    assert "items" in tables  # baseline untouched
    assert len(backups_in(tmp_path)) == 1  # backup kept for manual restore


def test_earlier_success_survives_later_failure(tmp_path):
    db = tmp_path / "db.sqlite"
    make_v1_db(db)

    def ok_step(conn):
        add_notes_table(conn)

    def failing_step(conn):
        raise ValueError("nope")

    with pytest.raises(MigrationError) as excinfo:
        migrate(
            db,
            [
                Migration(2, "good step", ok_step),
                Migration(3, "bad step", failing_step),
            ],
        )

    assert excinfo.value.version == 3
    assert current_version(db) == 2  # stays at last SUCCESSFUL version

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT body FROM notes").fetchall()
    conn.close()
    assert rows == [("migrated",)]  # v2's work is committed, not discarded


# --- refuse-to-guess -----------------------------------------------------


def test_populated_db_without_version_table_refuses(tmp_path):
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE mystery (id INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(MigrationError):
        migrate(db, [Migration(1, "baseline", add_notes_table)])

    assert current_version(db) is None  # nothing was written


# --- alternate tracking-table shapes -------------------------------------


def test_usage_ledger_shape_without_migrated_at(tmp_path):
    db = tmp_path / "ledger.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE usage_events (id INTEGER PRIMARY KEY);
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (1);
        """
    )
    conn.commit()
    conn.close()

    result = migrate(db, [Migration(2, "index it", lambda c: c.execute("CREATE INDEX idx_usage_id ON usage_events(id)"))])

    assert result == [2]
    assert current_version(db) == 2


def test_empty_version_table_on_fresh_db_treats_as_zero(tmp_path):
    db = tmp_path / "fresh.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, migrated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    result = migrate(db, [Migration(1, "first", add_notes_table)])

    assert result == [1]
    assert current_version(db) == 1


def test_fresh_file_migrates_from_zero_without_backup(tmp_path):
    db = tmp_path / "brand-new.sqlite"  # does not exist yet

    result = migrate(
        db,
        [
            Migration(1, "base", lambda c: c.execute("CREATE TABLE t (x INTEGER)")),
            Migration(2, "seed", lambda c: c.execute("INSERT INTO t VALUES (7)")),
        ],
    )

    assert result == [1, 2]
    assert current_version(db) == 2
    assert backups_in(tmp_path) == []  # nothing existed to back up
