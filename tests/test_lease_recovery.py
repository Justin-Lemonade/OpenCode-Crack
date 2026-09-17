"""
Tests for the lease watchdog/reclaimer (D-280).
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

from opencode_crack.runtime import control_db


def make_control_db_with_lease(tmp_path):
    """Create a control.db with a lease for testing."""
    db = tmp_path / "control.db"
    control_db.init_db(db)

    # Register agent first (FK constraint)
    control_db.register_agent(
        control_db.AgentProfile("worker-1", "worker", "test-model", []), db
    )

    # Insert a lease that's about to expire
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    expires = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")

    with control_db._connect(db) as conn:
        conn.execute(
            """
            INSERT INTO leases (task_id, agent_id, session_id, claimed_at, expires_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("D-999", "worker-1", "sess-1", now, expires, now),
        )
    return db


def test_reclaim_stale_leases_requeues_task(tmp_path):
    """Stale lease is reclaimed and task re-queued with backoff."""
    db = make_control_db_with_lease(tmp_path)
    # Fresh DB already has columns from updated SCHEMA

    # Verify lease is stale
    stale = control_db.get_stale_leases(db)
    assert len(stale) == 1
    assert stale[0]["task_id"] == "D-999"

    # Mock task_board.release_task
    with patch("opencode_crack.runtime.control_db.release_task") as mock_release:
        results = control_db.reclaim_stale_leases(max_retries=3, backoff_base_minutes=5, db_path=db)

    assert len(results) == 1
    assert results[0]["task_id"] == "D-999"
    assert results[0]["action"] == "requeued"
    assert results[0]["retry_count"] == 1
    assert results[0]["backoff_minutes"] >= 5


def test_reclaim_stale_leases_escalates_after_max_retries(tmp_path):
    """Stale lease exceeding max_retries escalates task."""
    db = make_control_db_with_lease(tmp_path)

    # Set retry_count to max
    with control_db._connect(db) as conn:
        conn.execute(
            "UPDATE leases SET retry_count = 3, max_retries = 3 WHERE task_id = ?",
            ("D-999",),
        )

    with patch("opencode_crack.runtime.control_db.block_task") as mock_block:
        results = control_db.reclaim_stale_leases(max_retries=3, db_path=db)

    assert len(results) == 1
    assert results[0]["action"] == "escalated"
    assert results[0]["retry_count"] == 4


def test_reclaim_stale_leases_backoff_calculation(tmp_path):
    """Exponential backoff with jitter is applied."""
    db = make_control_db_with_lease(tmp_path)

    with patch("opencode_crack.runtime.control_db.release_task"), \
         patch("random.randint", return_value=0):  # No jitter for deterministic test

        # First retry: base * 2^0 = 5
        results = control_db.reclaim_stale_leases(max_retries=3, backoff_base_minutes=5, db_path=db)
        assert len(results) == 1
        assert results[0]["backoff_minutes"] == 5

        # Manually re-create the lease for second retry (first reclaim releases it)
        with control_db._connect(db) as conn:
            conn.execute("""
                INSERT INTO leases (task_id, agent_id, session_id, claimed_at, expires_at, heartbeat_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("D-999", "worker-1", "sess-1",
                  datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),
                  datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  1))

        # Second retry: base * 2^1 = 10
        results = control_db.reclaim_stale_leases(max_retries=3, backoff_base_minutes=5, db_path=db)
        assert len(results) == 1
        assert results[0]["backoff_minutes"] == 10


def test_reclaim_stale_leases_marks_escalated_in_lease(tmp_path):
    """Escalated lease has escalated=1 and escalation_reason set."""
    db = make_control_db_with_lease(tmp_path)

    with control_db._connect(db) as conn:
        conn.execute("UPDATE leases SET retry_count = 3, max_retries = 3 WHERE task_id = ?", ("D-999",))

    with patch("opencode_crack.runtime.control_db.block_task"):
        control_db.reclaim_stale_leases(max_retries=3, db_path=db)

    with control_db._connect(db) as conn:
        row = conn.execute("SELECT escalated, escalation_reason FROM leases WHERE task_id = ?", ("D-999",)).fetchone()
        assert row["escalated"] == 1
        assert "Max retries" in row["escalation_reason"]


def test_reclaim_stale_leases_emits_events(tmp_path):
    """Reclaimer emits lease_reclaimed or task_escalated events."""
    db = make_control_db_with_lease(tmp_path)

    with patch("opencode_crack.runtime.control_db.release_task"):
        control_db.reclaim_stale_leases(max_retries=3, db_path=db)

    events = control_db.get_recent_events(limit=10, db_path=db)
    reclaimed = [e for e in events if e["event_type"] == "lease_reclaimed"]
    assert len(reclaimed) == 1
    assert reclaimed[0]["task_id"] == "D-999"
    import json
    payload = json.loads(reclaimed[0]["payload"])
    assert payload["retry_count"] == 1


def test_reclaim_stale_leases_no_stale_noop(tmp_path):
    """No stale leases = no actions taken."""
    db = tmp_path / "control.db"
    control_db.init_db(db)

    results = control_db.reclaim_stale_leases(db_path=db)
    assert results == []


def test_acquire_lease_populates_retry_columns(tmp_path):
    """acquire_lease initializes new retry columns."""
    db = tmp_path / "control.db"
    control_db.init_db(db)

    # Register agent first
    control_db.register_agent(
        control_db.AgentProfile("worker-1", "worker", "test-model", []), db
    )
    control_db.acquire_lease("D-100", "worker-1", db_path=db)

    with control_db._connect(db) as conn:
        row = conn.execute("SELECT retry_count, max_retries, backoff_base_minutes FROM leases WHERE task_id = ?", ("D-100",)).fetchone()
        assert row is not None
        assert row["retry_count"] == 0
        assert row["max_retries"] == 3
        assert row["backoff_base_minutes"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])