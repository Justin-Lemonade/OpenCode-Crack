"""
Tests for src/runtime/control_db.py (Phase B — C-072).
All tests use tmp_path-isolated DB. No OpenCode server needed.
"""
import time
from pathlib import Path
import pytest
from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile

@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p

@pytest.fixture()
def worker_profile():
    return AgentProfile("test-worker-01", "worker", "claude-sonnet-4-6", ["read","write"], personality="Careful.")

@pytest.fixture()
def manager_profile():
    return AgentProfile("test-manager-01", "manager", "claude-opus-4-6")

class TestSchemaInit:
    def test_init_creates_tables(self, tmp_path):
        p = tmp_path / "fresh.db"
        control_db.init_db(p)
        with control_db._connect(p) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"agents","oc_sessions","leases","messages","events","schema_version"} <= tables

    def test_schema_version_is_1(self, tmp_path):
        p = tmp_path / "ver.db"; control_db.init_db(p)
        assert control_db.get_schema_version(p) == 1

    def test_init_is_idempotent(self, tmp_path):
        p = tmp_path / "idem.db"
        control_db.init_db(p); control_db.init_db(p)
        assert control_db.get_schema_version(p) == 1

    def test_get_schema_version_raises_on_empty_table(self, tmp_path):
        p = tmp_path / "empty.db"
        with control_db._connect(p) as conn:
            conn.executescript(control_db.SCHEMA)
        with pytest.raises(ValueError, match="empty"):
            control_db.get_schema_version(p)

class TestAgentRegistry:
    def test_register_and_retrieve(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        result = control_db.get_agent(worker_profile.agent_id, db_path=db)
        assert result is not None
        assert result.agent_id == worker_profile.agent_id
        assert result.role == "worker"
        assert "read" in result.tool_permissions

    def test_get_unknown_returns_none(self, db):
        assert control_db.get_agent("no-such-agent", db_path=db) is None

    def test_register_is_upsert(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        updated = AgentProfile(worker_profile.agent_id, "worker", "claude-haiku-4-5")
        control_db.register_agent(updated, db_path=db)
        assert control_db.get_agent(worker_profile.agent_id, db_path=db).model == "claude-haiku-4-5"

    def test_list_agents_empty(self, db):
        assert control_db.list_agents(db_path=db) == []

    def test_list_agents_filter_by_role(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        workers = control_db.list_agents(role="worker", db_path=db)
        assert len(workers) == 1
        assert workers[0]["agent_id"] == "test-worker-01"

    def test_set_agent_status(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.set_agent_status(worker_profile.agent_id, "running", db_path=db)
        running = control_db.list_agents(status="running", db_path=db)
        assert any(a["agent_id"] == worker_profile.agent_id for a in running)

    def test_deregister_removes_agent(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        removed = control_db.deregister_agent(worker_profile.agent_id, db_path=db)
        assert removed is True
        assert control_db.get_agent(worker_profile.agent_id, db_path=db) is None

    def test_deregister_unknown_agent_returns_false(self, db):
        assert control_db.deregister_agent("no-such-agent", db_path=db) is False

    def test_deregister_releases_active_leases_first(self, db, worker_profile):
        """leases.agent_id has a real FK constraint (unlike sessions/
        events/messages) — deregistering an agent with an active lease
        must not raise sqlite3.IntegrityError."""
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-dereg-01", worker_profile.agent_id, db_path=db)

        removed = control_db.deregister_agent(worker_profile.agent_id, db_path=db)

        assert removed is True
        assert control_db.get_agent(worker_profile.agent_id, db_path=db) is None
        # The lease is gone too — a removed agent can't still hold work.
        assert control_db.get_stale_leases(db_path=db) == []

    def test_deregister_does_not_touch_other_agents_leases(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.acquire_lease("D-dereg-02", manager_profile.agent_id, db_path=db)

        control_db.deregister_agent(worker_profile.agent_id, db_path=db)

        # manager's lease and registration must be untouched.
        assert control_db.get_agent(manager_profile.agent_id, db_path=db) is not None

class TestSessionTracking:
    def test_record_start_and_retrieve(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.record_session_start("oc-001", worker_profile.agent_id, task_id="D-043", db_path=db)
        active = control_db.get_active_session(worker_profile.agent_id, db_path=db)
        assert active is not None
        assert active["session_id"] == "oc-001"
        assert active["status"] == "active"

    def test_record_end(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.record_session_start("oc-002", worker_profile.agent_id, db_path=db)
        control_db.record_session_end("oc-002", db_path=db)
        assert control_db.get_active_session(worker_profile.agent_id, db_path=db) is None

    def test_no_active_session_returns_none(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        assert control_db.get_active_session(worker_profile.agent_id, db_path=db) is None

class TestLeases:
    def test_acquire_succeeds(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        assert control_db.acquire_lease("D-100", worker_profile.agent_id, db_path=db) is True

    def test_acquire_fails_when_held(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.acquire_lease("D-100", worker_profile.agent_id, db_path=db)
        assert control_db.acquire_lease("D-100", manager_profile.agent_id, db_path=db) is False

    def test_release_allows_reacquire(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-100", worker_profile.agent_id, db_path=db)
        control_db.release_lease("D-100", db_path=db)
        assert control_db.acquire_lease("D-100", worker_profile.agent_id, db_path=db) is True

    def test_get_stale_leases(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        with control_db._connect(db) as conn:
            conn.execute("INSERT INTO leases (task_id,agent_id,claimed_at,expires_at) VALUES ('D-stale',?,'2020-01-01T00:00:00+00:00','2020-01-01T00:01:00+00:00')", (worker_profile.agent_id,))
        stale = control_db.get_stale_leases(db_path=db)
        assert any(s["task_id"] == "D-stale" for s in stale)

    def test_heartbeat_extends_lease(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-hb", worker_profile.agent_id, db_path=db)
        with control_db._connect(db) as conn:
            before = conn.execute("SELECT expires_at FROM leases WHERE task_id='D-hb'").fetchone()["expires_at"]
        time.sleep(0.01)
        control_db.heartbeat_lease("D-hb", db_path=db)
        with control_db._connect(db) as conn:
            after = conn.execute("SELECT expires_at FROM leases WHERE task_id='D-hb'").fetchone()["expires_at"]
        assert after >= before

    def test_heartbeat_agent_leases_extends_all_held_leases(self, db, worker_profile):
        """C-073's invokable heartbeat: one call refreshes every lease
        the agent holds, not just one task_id at a time."""
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-hb-multi-1", worker_profile.agent_id, db_path=db)
        control_db.acquire_lease("D-hb-multi-2", worker_profile.agent_id, db_path=db)
        with control_db._connect(db) as conn:
            before = {
                r["task_id"]: r["expires_at"]
                for r in conn.execute("SELECT task_id, expires_at FROM leases WHERE agent_id=?", (worker_profile.agent_id,))
            }
        time.sleep(0.01)

        count = control_db.heartbeat_agent_leases(worker_profile.agent_id, db_path=db)

        assert count == 2
        with control_db._connect(db) as conn:
            after = {
                r["task_id"]: r["expires_at"]
                for r in conn.execute("SELECT task_id, expires_at FROM leases WHERE agent_id=?", (worker_profile.agent_id,))
            }
        for task_id in before:
            assert after[task_id] >= before[task_id]

    def test_heartbeat_agent_leases_returns_zero_when_no_leases_held(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        assert control_db.heartbeat_agent_leases(worker_profile.agent_id, db_path=db) == 0

    def test_heartbeat_agent_leases_does_not_touch_other_agents(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.acquire_lease("D-hb-other", manager_profile.agent_id, db_path=db)
        with control_db._connect(db) as conn:
            before = conn.execute("SELECT expires_at FROM leases WHERE task_id='D-hb-other'").fetchone()["expires_at"]
        time.sleep(0.01)

        control_db.heartbeat_agent_leases(worker_profile.agent_id, db_path=db)

        with control_db._connect(db) as conn:
            after = conn.execute("SELECT expires_at FROM leases WHERE task_id='D-hb-other'").fetchone()["expires_at"]
        assert after == before

class TestMessages:
    def test_send_and_receive(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        msg_id = control_db.send_message(manager_profile.agent_id, worker_profile.agent_id, "Implement D-043", db_path=db)
        assert isinstance(msg_id, int)
        unread = control_db.get_unread_messages(worker_profile.agent_id, db_path=db)
        assert len(unread) == 1 and unread[0]["content"] == "Implement D-043"

    def test_mark_messages_read(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.send_message(manager_profile.agent_id, worker_profile.agent_id, "msg1", db_path=db)
        control_db.send_message(manager_profile.agent_id, worker_profile.agent_id, "msg2", db_path=db)
        assert control_db.mark_messages_read(worker_profile.agent_id, db_path=db) == 2
        assert control_db.get_unread_messages(worker_profile.agent_id, db_path=db) == []

    def test_messages_ordered_oldest_first(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        for i in range(3):
            control_db.send_message(manager_profile.agent_id, worker_profile.agent_id, f"msg{i}", db_path=db)
        msgs = control_db.get_unread_messages(worker_profile.agent_id, db_path=db)
        assert [m["content"] for m in msgs] == ["msg0","msg1","msg2"]

class TestEvents:
    def test_events_on_session_start(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.record_session_start("oc-ev-001", worker_profile.agent_id, db_path=db)
        types = [e["event_type"] for e in control_db.get_recent_events(db_path=db)]
        assert "session_started" in types

    def test_events_on_lease_acquire(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-ev", worker_profile.agent_id, db_path=db)
        types = [e["event_type"] for e in control_db.get_recent_events(db_path=db)]
        assert "lease_claimed" in types

    def test_get_recent_events_limit(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        for i in range(5):
            control_db.acquire_lease(f"D-ev-{i}", worker_profile.agent_id, db_path=db)
            control_db.release_lease(f"D-ev-{i}", db_path=db)
        assert len(control_db.get_recent_events(limit=3, db_path=db)) == 3
        assert len(control_db.get_recent_events(limit=100, db_path=db)) >= 5


class TestControlPlaneSummary:
    """C-073: brain.get_status() surfaces this summary. Covered here at
    the control_db level since that's where the counting logic lives —
    brain.get_status() just attaches whatever this returns."""

    def test_summary_when_db_does_not_exist(self, tmp_path):
        missing = tmp_path / "does-not-exist.db"
        summary = control_db.get_control_plane_summary(db_path=missing)
        assert summary == {
            "db_initialized": False,
            "agent_count": 0,
            "active_sessions": 0,
            "stale_leases": 0,
            "recent_event_count": 0,
        }

    def test_summary_on_freshly_initialized_empty_db(self, db):
        summary = control_db.get_control_plane_summary(db_path=db)
        assert summary["db_initialized"] is True
        assert summary["agent_count"] == 0
        assert summary["active_sessions"] == 0
        assert summary["stale_leases"] == 0
        assert summary["recent_event_count"] == 0

    def test_summary_counts_registered_agents(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        summary = control_db.get_control_plane_summary(db_path=db)
        assert summary["agent_count"] == 2

    def test_summary_counts_active_sessions(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.record_session_start("oc-summary-01", worker_profile.agent_id, db_path=db)
        summary = control_db.get_control_plane_summary(db_path=db)
        assert summary["active_sessions"] == 1

    def test_summary_counts_stale_leases(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        # A lease with a negative duration is already expired the
        # instant it's created — the simplest deterministic way to
        # produce a stale lease without sleeping in a test.
        control_db.acquire_lease("D-stale-01", worker_profile.agent_id, duration_minutes=-1, db_path=db)
        summary = control_db.get_control_plane_summary(db_path=db)
        assert summary["stale_leases"] == 1

    def test_summary_matches_list_agents_count_after_changes(self, db, worker_profile, manager_profile):
        """The summary's counts must always agree with what `brain agents`
        would show — this guards against the summary logic drifting out
        of sync with the underlying list_agents()/list_active_sessions()
        queries it wraps."""
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.set_agent_status(worker_profile.agent_id, "running", db_path=db)

        summary = control_db.get_control_plane_summary(db_path=db)
        assert summary["agent_count"] == len(control_db.list_agents(db_path=db))
