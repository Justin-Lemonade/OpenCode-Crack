"""
Swarm run result reconciliation with control-plane state (roadmap D-161).

Pins that a SwarmResult's agent fields are reconciled against registered
agents: matched agents get status reconciled + a deduplicated
`swarm_agent_reconciled` event; unknown agents are reported as unmatched
and never inserted; reconciliation is idempotent; and only temporary SQLite
databases and synthetic SwarmResult values are used.
"""
import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_reconcile import (
    RECONCILE_EVENT_TYPE,
    reconcile_swarm_result,
)
from opencode_crack.runtime.swarm_runtime import SwarmResult


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


def _registered(db, *agents):
    for agent_id, role in agents:
        control_db.register_agent(AgentProfile(agent_id, role, "openai/gpt-4o-mini",
                                               tool_permissions=["read"]), db_path=db)


def _result(swarm_id="sw_reconcile_1", *agents):
    return SwarmResult(swarm_id=swarm_id, status="completed", agents=list(agents))


def _agent(name, status="completed", model="openai/gpt-4o-mini", error=None):
    entry = {"name": name, "status": status, "model": model}
    if error:
        entry["error"] = error
    return entry


# --- matched agents reconciled -------------------------------------------------

class TestMatchedAgents:
    def test_registered_agents_are_matched(self, db):
        _registered(db, ("worker", "worker"), ("tester", "tester"))
        report = reconcile_swarm_result(_result("sw_x", _agent("worker"), _agent("tester")), db)
        assert report.registered_count == 2
        assert report.unmatched_count == 0
        assert {e.agent for e in report.matched} == {"worker", "tester"}
        assert all(e.matched for e in report.matched)
        assert all(e.registered_agent_id == e.agent for e in report.matched)

    def test_status_is_reconciled_on_registered_agent(self, db):
        _registered(db, ("worker", "worker"))
        reconcile_swarm_result(_result("sw_y", _agent("worker", status="failed")), db)
        row = control_db.list_agents(db_path=db)[0]
        assert row["agent_id"] == "worker"
        assert row["status"] == "failed"

    def test_reconcile_event_records_status_model_error(self, db):
        _registered(db, ("worker", "worker"))
        reconcile_swarm_result(
            _result("sw_z", _agent("worker", status="failed", model="m2", error="boom")), db
        )
        events = [e for e in control_db.get_recent_events(db_path=db)
                  if e["event_type"] == RECONCILE_EVENT_TYPE]
        assert len(events) == 1
        import json
        payload = json.loads(events[0]["payload"])
        assert payload["status"] == "failed"
        assert payload["model"] == "m2"
        assert payload["error"] == "boom"
        assert payload["source_event_id"] == "sw_z:worker"
        assert events[0]["agent_id"] == "worker"


# --- unmatched agents reported, never inserted ----------------------------------

class TestUnmatchedAgents:
    def test_unknown_agent_is_reported_not_inserted(self, db):
        _registered(db, ("worker", "worker"))
        report = reconcile_swarm_result(_result("sw_u", _agent("ghost")), db)
        assert report.registered_count == 0
        assert report.unmatched_count == 1
        assert report.unmatched[0].agent == "ghost"
        assert report.unmatched[0].matched is False
        # never silently inserted into the registry
        assert control_db.get_agent("ghost", db_path=db) is None

    def test_partial_match_reports_both_sides(self, db):
        _registered(db, ("worker", "worker"))
        report = reconcile_swarm_result(
            _result("sw_p", _agent("worker"), _agent("mystery")), db
        )
        assert [e.agent for e in report.matched] == ["worker"]
        assert [e.agent for e in report.unmatched] == ["mystery"]

    def test_unmatched_agent_gets_no_reconcile_event(self, db):
        _registered(db, ("worker", "worker"))
        reconcile_swarm_result(_result("sw_n", _agent("ghost")), db)
        events = [e for e in control_db.get_recent_events(db_path=db)
                  if e["event_type"] == RECONCILE_EVENT_TYPE]
        assert events == []


# --- idempotency ----------------------------------------------------------------

class TestIdempotency:
    def test_rerun_same_result_no_duplicate_events(self, db):
        _registered(db, ("worker", "worker"))
        result = _result("sw_i", _agent("worker", status="completed"))
        first = reconcile_swarm_result(result, db)
        second = reconcile_swarm_result(result, db)
        events = [e for e in control_db.get_recent_events(db_path=db)
                  if e["event_type"] == RECONCILE_EVENT_TYPE]
        assert len(events) == 1
        assert first.as_dict() == second.as_dict()

    def test_rerun_different_status_does_not_duplicate_event(self, db):
        _registered(db, ("worker", "worker"))
        reconcile_swarm_result(_result("sw_i2", _agent("worker", status="completed")), db)
        reconcile_swarm_result(_result("sw_i2", _agent("worker", status="failed")), db)
        events = [e for e in control_db.get_recent_events(db_path=db)
                  if e["event_type"] == RECONCILE_EVENT_TYPE]
        assert len(events) == 1  # same source event id -> no duplicate
        import json
        assert json.loads(events[0]["payload"])["status"] == "completed"  # first write wins


# --- no git state, no schema change ---------------------------------------------

class TestIsolation:
    def test_report_shape_is_stable(self, db):
        _registered(db, ("worker", "worker"))
        report = reconcile_swarm_result(_result("sw_s", _agent("worker"), _agent("x")), db)
        d = report.as_dict()
        assert d["swarm_id"] == "sw_s"
        assert d["registered_count"] == 1
        assert d["unmatched_count"] == 1
        assert "matched" in d and "unmatched" in d

    def test_empty_agents_result_reconciles_cleanly(self, db):
        report = reconcile_swarm_result(_result("sw_empty"), db)
        assert report.registered_count == 0
        assert report.unmatched_count == 0

    def test_uses_existing_control_db_tables(self, db):
        # No new tables are created beyond the standard schema.
        tables_before = {r["name"] for r in _tables(db)}
        reconcile_swarm_result(_result("sw_t", _agent("nobody")), db)
        tables_after = {r["name"] for r in _tables(db)}
        assert tables_before == tables_after


def _tables(db):
    with control_db._connect(db) as conn:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
