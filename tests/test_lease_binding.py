"""
Session-to-lease binding (roadmap D-162).

Pins that an existing OpenCode session can be bound to the AI-Brain task
lease model: task/agent/session stay attributable together (lease row
carries session_id, session row linked to task), acquisition failure means
no owned work and no dangling lease, release/terminal paths leave no live
lease, and stale leases behave exactly as control_db defines them. All
tests use temporary SQLite databases.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.lease_binding import (
    SessionLeaseBinding,
    bind_session_to_lease,
    release_session_binding,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


@pytest.fixture
def worker(db):
    profile = AgentProfile("worker", "worker", "openai/gpt-4o-mini", tool_permissions=["read"])
    control_db.register_agent(profile, db_path=db)
    return profile


def _start_session(db, session_id, agent_id, task_id=None):
    control_db.record_session_start(session_id, agent_id, task_id=task_id, db_path=db)


def _lease_row(db, task_id):
    with control_db._connect(db) as conn:
        row = conn.execute("SELECT * FROM leases WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def _session_row(db, session_id):
    with control_db._connect(db) as conn:
        row = conn.execute("SELECT * FROM oc_sessions WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def _insert_stale_lease(db, task_id, agent_id, session_id=None):
    now = datetime.now(timezone.utc)
    expired = now - timedelta(minutes=30)
    with control_db._connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO leases (task_id,agent_id,session_id,claimed_at,expires_at,heartbeat_at) VALUES (?,?,?,?,?,?)",
            (task_id, agent_id, session_id, now.isoformat(), expired.isoformat(), now.isoformat()))


# --- successful binding --------------------------------------------------------

class TestSuccessfulBinding:
    def test_binding_acquired(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        assert b.acquired is True
        assert b.owned_work is True
        assert b.reason is None

    def test_lease_carries_session_id(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        row = _lease_row(db, "T-1")
        assert row is not None
        assert row["session_id"] == "sess-1"
        assert row["agent_id"] == worker.agent_id

    def test_session_linked_to_task(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        row = _session_row(db, "sess-1")
        assert row["task_id"] == "T-1"
        assert row["agent_id"] == worker.agent_id

    def test_existing_task_attribution_is_preserved(self, db, worker):
        # A session already attributed by ingestion keeps its task; linking is a no-op.
        _start_session(db, "sess-2", worker.agent_id, task_id="T-orig")
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-2", db_path=db)
        assert b.acquired is True
        assert _session_row(db, "sess-2")["task_id"] == "T-orig"


# --- contention / acquisition failure -------------------------------------------

class TestContention:
    def test_contention_prevents_owned_work(self, db, worker):
        other = AgentProfile("other", "worker", "openai/gpt-4o-mini", tool_permissions=["read"])
        control_db.register_agent(other, db_path=db)
        _start_session(db, "sess-1", worker.agent_id)
        control_db.acquire_lease("T-1", other.agent_id, db_path=db)  # someone else holds it
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        assert b.acquired is False
        assert b.owned_work is False
        assert b.reason == "lease_not_acquired"

    def test_contention_does_not_link_session(self, db, worker):
        other = AgentProfile("other", "worker", "openai/gpt-4o-mini", tool_permissions=["read"])
        control_db.register_agent(other, db_path=db)
        _start_session(db, "sess-1", worker.agent_id, task_id="T-other")
        control_db.acquire_lease("T-1", other.agent_id, db_path=db)
        bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        assert _session_row(db, "sess-1")["task_id"] == "T-other"

    def test_unknown_session_releases_lease(self, db, worker):
        # Lease got acquired, but the session row is missing -> not owned work,
        # and the just-taken lease is released so nothing dangles.
        b = bind_session_to_lease("T-1", worker.agent_id, "no-such-session", db_path=db)
        assert b.acquired is False
        assert b.owned_work is False
        assert b.reason == "session_not_found"
        assert _lease_row(db, "T-1") is None


# --- release / terminal paths ----------------------------------------------------

class TestReleaseAndTerminal:
    def test_release_leaves_no_live_lease(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        release_session_binding(b, db_path=db)
        assert _lease_row(db, "T-1") is None
        assert _session_row(db, "sess-1")["status"] == "finished"

    def test_terminal_failure_leaves_no_live_lease(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        release_session_binding(b, status="failed", db_path=db)
        assert _lease_row(db, "T-1") is None
        assert _session_row(db, "sess-1")["status"] == "failed"

    def test_release_is_idempotent(self, db, worker):
        _start_session(db, "sess-1", worker.agent_id)
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        release_session_binding(b, db_path=db)
        release_session_binding(b, db_path=db)  # no-op, must not raise
        assert _lease_row(db, "T-1") is None

    def test_release_of_never_acquired_binding_is_safe(self, db, worker):
        b = SessionLeaseBinding("T-1", worker.agent_id, "sess-1", acquired=False, reason="lease_not_acquired")
        release_session_binding(b, db_path=db)  # must not raise
        assert _lease_row(db, "T-1") is None


# --- stale-lease semantics --------------------------------------------------------

class TestStaleLeaseSemantics:
    def test_expired_unreleased_lease_blocks_binding(self, db, worker):
        other = AgentProfile("other", "worker", "openai/gpt-4o-mini", tool_permissions=["read"])
        control_db.register_agent(other, db_path=db)
        _start_session(db, "sess-1", worker.agent_id)
        _insert_stale_lease(db, "T-1", other.agent_id, session_id="old-sess")
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        assert b.acquired is False
        assert b.owned_work is False
        assert b.reason == "lease_not_acquired"  # existing control_db semantics

    def test_release_reclaims_stale_lease(self, db, worker):
        other = AgentProfile("other", "worker", "openai/gpt-4o-mini", tool_permissions=["read"])
        control_db.register_agent(other, db_path=db)
        _start_session(db, "sess-1", worker.agent_id)
        _insert_stale_lease(db, "T-1", other.agent_id, session_id="old-sess")
        control_db.release_lease("T-1", db_path=db)  # explicit reclaim path
        b = bind_session_to_lease("T-1", worker.agent_id, "sess-1", db_path=db)
        assert b.acquired is True
        assert _lease_row(db, "T-1")["session_id"] == "sess-1"

    def test_stale_lease_listing_uses_existing_semantics(self, db, worker):
        _insert_stale_lease(db, "T-1", worker.agent_id, session_id="old-sess")
        stale = control_db.get_stale_leases(db_path=db)
        assert [s["task_id"] for s in stale] == ["T-1"]