"""
Lease heartbeat and stale-detection edge case tests (D-133, Phase B — C-072).
Extends test_control_db.py happy-path coverage with stale/expiry edge cases.
All tests use tmp_path-isolated DB. No OpenCode server needed.
"""
import time
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

@pytest.fixture()
def tester_profile():
    return AgentProfile("test-tester-01", "tester", "gpt-5")


class TestLeaseEdgeCases:
    def test_zero_duration_is_immediately_stale(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        assert control_db.acquire_lease("D-0", worker_profile.agent_id, duration_minutes=0, db_path=db) is True
        time.sleep(0.01)
        stale = control_db.get_stale_leases(db_path=db)
        assert any(s["task_id"] == "D-0" for s in stale)

    def test_heartbeat_on_missing_task_does_not_raise(self, db):
        control_db.heartbeat_lease("no-such-task", db_path=db)

    def test_release_on_missing_task_does_not_raise(self, db):
        control_db.release_lease("no-such-task", db_path=db)

    def test_two_agents_race_only_first_wins(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        assert control_db.acquire_lease("D-race", worker_profile.agent_id, db_path=db) is True
        assert control_db.acquire_lease("D-race", manager_profile.agent_id, db_path=db) is False
        with control_db._connect(db) as conn:
            holder = conn.execute("SELECT agent_id FROM leases WHERE task_id='D-race'").fetchone()
        assert holder["agent_id"] == worker_profile.agent_id

    def test_stale_lease_blocked_until_released(self, db, worker_profile, manager_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        with control_db._connect(db) as conn:
            conn.execute(
                "INSERT INTO leases (task_id,agent_id,claimed_at,expires_at) VALUES ('D-old',?,'2020-01-01T00:00:00+00:00','2020-01-01T00:01:00+00:00')",
                (worker_profile.agent_id,))
        assert any(s["task_id"] == "D-old" for s in control_db.get_stale_leases(db_path=db))
        assert control_db.acquire_lease("D-old", manager_profile.agent_id, db_path=db) is False

    def test_third_agent_reacquires_after_stale_release(self, db, worker_profile, manager_profile, tester_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.register_agent(manager_profile, db_path=db)
        control_db.register_agent(tester_profile, db_path=db)
        with control_db._connect(db) as conn:
            conn.execute(
                "INSERT INTO leases (task_id,agent_id,claimed_at,expires_at) VALUES ('D-old2',?,'2020-01-01T00:00:00+00:00','2020-01-01T00:01:00+00:00')",
                (worker_profile.agent_id,))
        stale_ids = {s["task_id"] for s in control_db.get_stale_leases(db_path=db)}
        assert "D-old2" in stale_ids
        control_db.release_lease("D-old2", db_path=db)
        assert control_db.acquire_lease("D-old2", tester_profile.agent_id, db_path=db) is True
        with control_db._connect(db) as conn:
            holder = conn.execute("SELECT agent_id FROM leases WHERE task_id='D-old2'").fetchone()
        assert holder["agent_id"] == tester_profile.agent_id

    def test_no_stale_leases_when_all_valid(self, db, worker_profile):
        control_db.register_agent(worker_profile, db_path=db)
        control_db.acquire_lease("D-valid", worker_profile.agent_id, db_path=db)
        assert control_db.get_stale_leases(db_path=db) == []

    def test_no_stale_leases_when_empty_table(self, db):
        assert control_db.get_stale_leases(db_path=db) == []