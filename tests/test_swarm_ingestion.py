"""
Control-plane swarm event ingestion tests (roadmap D-144, broken out of
C-073/C-074).

Pins the translation contract of SwarmRuntime.ingest_events into control_db
lifecycle events: agent-turn-done becomes an attributable event, agent-settled
records terminal session state, duplicate source event ids are idempotent, and
unknown agent/session references never crash ingestion. Every database is a
tmp_path SQLite file; nothing executes a real swarm.
"""
from pathlib import Path

import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_runtime import SwarmResult, SwarmRuntime


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


@pytest.fixture
def runtime(db):
    return SwarmRuntime(binary="swarm", control_db_path=db)


def _result(*events):
    return SwarmResult(swarm_id="sw_test", status="completed", events=list(events))


class TestAgentTurnDone:
    def test_creates_attributable_event(self, db, runtime):
        event = {"id": "evt-1", "type": "agent-turn-done", "agent": "worker", "sessionId": "ses-1", "taskId": "D-999"}
        count = runtime.ingest_events(_result(event))
        assert count == 1
        rows = control_db.get_recent_events(db_path=db)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "swarm:agent-turn-done"
        assert rows[0]["agent_id"] == "worker"
        assert rows[0]["session_id"] == "ses-1"
        assert rows[0]["task_id"] == "D-999"

    def test_agent_name_aliases_are_attributed(self, db, runtime):
        for alias in ("agentName", "agent_id"):
            event = {"id": f"evt-{alias}", "type": "agent-turn-done", alias: "worker"}
            runtime.ingest_events(_result(event))
        rows = control_db.get_recent_events(db_path=db)
        assert len(rows) == 2
        assert {r["agent_id"] for r in rows} == {"worker"}


class TestAgentSettled:
    def test_records_terminal_session_state(self, db, runtime):
        control_db.register_agent(AgentProfile("worker", "worker", "model"), db_path=db)
        control_db.record_session_start("ses-1", "worker", db_path=db)
        assert control_db.get_active_session("worker", db_path=db) is not None

        event = {"id": "evt-settle", "type": "agent-settled", "agent": "worker", "sessionId": "ses-1"}
        assert runtime.ingest_events(_result(event)) == 1

        with control_db._connect(db) as conn:
            row = conn.execute("SELECT status, finished_at FROM oc_sessions WHERE session_id='ses-1'").fetchone()
        assert row["status"] == "settled"
        assert row["finished_at"] is not None
        assert control_db.get_active_session("worker", db_path=db) is None

    def test_settled_without_session_id_does_not_crash(self, db, runtime):
        event = {"id": "evt-settle-nosess", "type": "agent-settled", "agent": "worker"}
        assert runtime.ingest_events(_result(event)) == 1


class TestIdempotency:
    def test_duplicate_event_ids_are_ingested_once(self, db, runtime):
        event = {"id": "evt-dup", "type": "agent-turn-done", "agent": "worker"}
        assert runtime.ingest_events(_result(event)) == 1
        assert runtime.ingest_events(_result(event)) == 0
        rows = control_db.get_recent_events(db_path=db)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "swarm:agent-turn-done"

    def test_same_swarm_id_retry_does_not_duplicate(self, db, runtime):
        events = [
            {"id": "evt-a", "type": "agent-turn-done", "agent": "worker"},
            {"id": "evt-b", "type": "agent-turn-done", "agent": "tester"},
        ]
        result = _result(*events)
        assert runtime.ingest_events(result) == 2
        assert runtime.ingest_events(result) == 0
        assert len(control_db.get_recent_events(db_path=db)) == 2

    def test_duplicate_agent_settled_does_not_reset_terminal_state(self, db, runtime):
        control_db.register_agent(AgentProfile("worker", "worker", "model"), db_path=db)
        control_db.record_session_start("ses-1", "worker", db_path=db)
        event = {"id": "evt-settle", "type": "agent-settled", "agent": "worker", "sessionId": "ses-1"}
        assert runtime.ingest_events(_result(event)) == 1
        assert runtime.ingest_events(_result(event)) == 0
        rows = [r for r in control_db.get_recent_events(db_path=db) if r["event_type"] == "swarm:agent-settled"]
        assert len(rows) == 1


class TestUnknownReferences:
    def test_unknown_agent_does_not_crash(self, db, runtime):
        event = {"id": "evt-ghost", "type": "agent-turn-done", "agent": "no-such-agent"}
        assert runtime.ingest_events(_result(event)) == 1

    def test_unknown_session_does_not_crash(self, db, runtime):
        event = {"id": "evt-ghost-sess", "type": "agent-settled", "agent": "worker", "sessionId": "no-such-session"}
        assert runtime.ingest_events(_result(event)) == 1

    def test_missing_optional_fields_do_not_crash(self, db, runtime):
        event = {"type": "agent-turn-done"}
        assert runtime.ingest_events(_result(event)) == 1
        rows = control_db.get_recent_events(db_path=db)
        assert rows[0]["event_type"] == "swarm:agent-turn-done"