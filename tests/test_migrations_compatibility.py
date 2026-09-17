"""
Compatibility test for the shared SQLite migration runner against
control_db, the one subsystem in this framework that uses it (C-076).

AI-Brain's original version of this test also covers facts_db and
usage_ledger — both domain-specific subsystems that live in AI-Brain,
not here, so those sections are dropped along with their imports. See
migrations.py's module docstring for the "every SQLite subsystem in
this repo" note, which now refers only to control_db from this
package's point of view.

These tests exercise `migrate()` against a temporary database
initialized by control_db's real `init_db()`, proving:
- the schema-version shape works with the generic runner
- a synthetic v2 migration applies transactionally and records the new version
- idempotency: running a second time with the same v2 does NOT reapply
- failure path: a failing migration leaves DB at prior version, backup preserved
"""
import sqlite3
from pathlib import Path

import pytest

from opencode_crack.storage.migrations import Migration, MigrationError, migrate
from opencode_crack.runtime.control_db import init_db as init_control_db


# --- helpers ---------------------------------------------------------------

def current_version_control(db_path: Path) -> int | None:
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


# --- synthetic v2 migration ---

def control_db_v2_add_agent_metadata_column(conn: sqlite3.Connection) -> None:
    """Add a metadata_json column to agents table (control_db shape)."""
    conn.execute("ALTER TABLE agents ADD COLUMN metadata_json TEXT DEFAULT '{}'")


def failing_migration(conn: sqlite3.Connection) -> None:
    """A migration that always fails mid-way."""
    conn.execute("CREATE TABLE should_not_exist (id INTEGER)")
    raise RuntimeError("intentional failure for test")


# --- control_db compatibility ----------------------------------------------

def test_control_db_v2_migration_applies_transactionally(tmp_path):
    """control_db init + v2 migration applies and records version."""
    db = tmp_path / "control.sqlite"
    init_control_db(db)

    assert current_version_control(db) == 1

    result = migrate(db, [Migration(2, "add agent metadata_json", control_db_v2_add_agent_metadata_column)])

    assert result == [2]
    assert current_version_control(db) == 2

    # backup created
    assert len(backups_in(tmp_path)) == 1
    assert "pre-migration-v1-v2-" in backups_in(tmp_path)[0].name


def test_control_db_v2_idempotent_on_rerun(tmp_path):
    """Running the same v2 migration a second time does NOT reapply."""
    db = tmp_path / "control.sqlite"
    init_control_db(db)

    result1 = migrate(db, [Migration(2, "add agent metadata_json", control_db_v2_add_agent_metadata_column)])
    assert result1 == [2]
    assert current_version_control(db) == 2

    result2 = migrate(db, [Migration(2, "add agent metadata_json", control_db_v2_add_agent_metadata_column)])
    assert result2 == []
    assert current_version_control(db) == 2


def test_control_db_v2_failed_migration_rolls_back(tmp_path):
    """Failed v2 migration leaves DB at v1, backup preserved."""
    db = tmp_path / "control.sqlite"
    init_control_db(db)

    with pytest.raises(MigrationError) as excinfo:
        migrate(db, [Migration(2, "failing step", failing_migration)])

    err = excinfo.value
    assert err.version == 2

    assert current_version_control(db) == 1

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "should_not_exist" not in tables
    assert "agents" in tables
    assert "schema_version" in tables

    assert len(backups_in(tmp_path)) == 1


def test_control_db_v2_earlier_success_survives_later_failure(tmp_path):
    """v2 succeeds, v3 fails -> DB stays at v2."""
    db = tmp_path / "control.sqlite"
    init_control_db(db)

    with pytest.raises(MigrationError) as excinfo:
        migrate(db, [
            Migration(2, "add agent metadata_json", control_db_v2_add_agent_metadata_column),
            Migration(3, "failing step", failing_migration),
        ])

    assert excinfo.value.version == 3
    assert current_version_control(db) == 2

    # v2's work committed
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)")}
    conn.close()
    assert "metadata_json" in cols
