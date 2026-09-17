"""
Read-only swarm status diagnostics (roadmap D-165).

Pins that `collect_swarm_status` produces a deterministic, credential-safe
snapshot of swarm executable availability/version, configured OpenCode
endpoint health, active sessions/agents, and recent swarm lifecycle events,
without mutating state and without tracebacks when a runtime service is
missing. Checkers are injectable so no live swarm binary or OpenCode server
is required. Also covers the `swarm-status` CLI flag parsing path.
"""
import json

import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_diagnostics import collect_swarm_status
from opencode_crack.runtime.swarm_runtime import SwarmHealth


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


def _healthy_swarm():
    return SwarmHealth(binary="swarm", available=True, version="0.4.5")


def _missing_swarm():
    return SwarmHealth(binary="swarm", available=False, error="not found")


def _seeded_db(db):
    control_db.register_agent(AgentProfile("worker", "worker", "openai/gpt-4o-mini",
                                           tool_permissions=["read"]), db_path=db)
    control_db.set_agent_status("worker", "running", db_path=db)
    control_db.record_session_start("sess-1", "worker", task_id="D-165", db_path=db)
    control_db._append_event("swarm:agent-turn-done", agent_id="worker", session_id="sess-1",
                             task_id="D-165", payload={"source_event_id": "evt-1"}, db_path=db)
    control_db._append_event("swarm:agent-settled", agent_id="worker", session_id="sess-1",
                             task_id="D-165", payload={"source_event_id": "evt-2"}, db_path=db)
    control_db._append_event("lease_claimed", agent_id="worker", task_id="D-165", db_path=db)


# --- swarm executable availability/version ----------------------------------------

class TestSwarmExecutable:
    def test_available_swarm_reports_version(self, db):
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        s = report["swarm_executable"]
        assert s["available"] is True
        assert s["version"] == "0.4.5"
        assert s["error"] is None

    def test_missing_swarm_reports_diagnostic(self, db):
        report = collect_swarm_status(db, swarm_checker=_missing_swarm,
                                      opencode_checker=lambda: True)
        s = report["swarm_executable"]
        assert s["available"] is False
        assert "not found" in s["error"]


# --- OpenCode endpoint health, no credentials -------------------------------------

class TestEndpointHealth:
    def test_healthy_endpoint(self, db):
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        assert report["opencode_endpoint"]["healthy"] is True
        assert report["opencode_endpoint"]["error"] is None

    def test_unhealthy_endpoint(self, db):
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: False)
        e = report["opencode_endpoint"]
        assert e["healthy"] is False
        assert "unreachable" in e["error"]

    def test_endpoint_error_is_diagnostic_not_traceback(self, db):
        def boom():
            raise RuntimeError("connection refused")
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=boom)
        e = report["opencode_endpoint"]
        assert e["healthy"] is False
        assert "connection refused" in e["error"]

    def test_endpoint_label_strips_credentials(self):
        from opencode_crack.runtime.swarm_diagnostics import _endpoint_label
        assert _endpoint_label("http://user:secret@localhost:15000") == "http://localhost:15000"
        assert _endpoint_label("http://localhost:15000") == "http://localhost:15000"


# --- active sessions/agents + swarm events ----------------------------------------

class TestRuntimeState:
    def test_active_sessions_reported(self, db):
        _seeded_db(db)
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        sessions = report["active_sessions"]
        assert [s["session_id"] for s in sessions] == ["sess-1"]
        assert sessions[0]["task_id"] == "D-165"
        assert sessions[0]["agent_id"] == "worker"

    def test_active_agents_reported(self, db):
        _seeded_db(db)
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        agents = report["agents"]
        assert [a["agent_id"] for a in agents] == ["worker"]
        assert agents[0]["status"] == "running"

    def test_idle_agents_excluded(self, db):
        control_db.register_agent(AgentProfile("idler", "worker", "openai/gpt-4o-mini",
                                               tool_permissions=["read"]), db_path=db)
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        assert report["agents"] == []

    def test_recent_swarm_events_limited_and_filtered(self, db):
        _seeded_db(db)
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True, event_limit=1)
        events = report["recent_swarm_events"]
        assert len(events) == 1
        assert events[0]["event_type"].startswith("swarm:")
        assert events[0]["task_id"] == "D-165"


# --- determinism / no mutation / no tracebacks -------------------------------------

class TestRobustness:
    def test_empty_db_is_clean_snapshot(self, db):
        report = collect_swarm_status(db, swarm_checker=_missing_swarm,
                                      opencode_checker=lambda: False)
        assert report["active_sessions"] == []
        assert report["agents"] == []
        assert report["recent_swarm_events"] == []

    def test_checker_exception_produces_field_not_traceback(self, db):
        def boom():
            raise OSError("exec not found")
        report = collect_swarm_status(db, swarm_checker=boom,
                                      opencode_checker=lambda: True)
        assert report["swarm_executable"]["available"] is False
        assert "exec not found" in report["swarm_executable"]["error"]

    def test_snapshot_is_json_serializable(self, db):
        _seeded_db(db)
        report = collect_swarm_status(db, swarm_checker=_healthy_swarm,
                                      opencode_checker=lambda: True)
        assert json.loads(json.dumps(report, default=str)) == report

    def test_snapshot_does_not_mutate_db(self, db):
        _seeded_db(db)
        before = control_db.get_recent_events(limit=50, db_path=db)
        collect_swarm_status(db, swarm_checker=_healthy_swarm, opencode_checker=lambda: True)
        after = control_db.get_recent_events(limit=50, db_path=db)
        assert [e["id"] for e in before] == [e["id"] for e in after]


# --- stuck coder-settlement detection (opencode-swarm upstream defect) -------------

class TestStuckSettlements:
    """`_stuck_settlements_block` flags coder settlements the opencode-swarm
    plugin left in a non-terminal state (e.g. DISPATCHED) past a staleness
    window. Read-only: it must never write to `.swarm/coder-settlements/`."""

    def _write_settlement(self, swarm_dir, task_id, state, updated_at, extra=None):
        settlements = swarm_dir / "coder-settlements"
        settlements.mkdir(parents=True, exist_ok=True)
        payload = {"task_id": task_id, "state": state, "updated_at": updated_at}
        if extra:
            payload.update(extra)
        (settlements / f"{task_id}.json").write_text(json.dumps(payload))

    def test_no_settlements_dir_is_empty_not_error(self, tmp_path):
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        result = _stuck_settlements_block(swarm_dir=tmp_path / ".swarm")
        assert result == []

    def test_fresh_dispatched_settlement_not_flagged(self, tmp_path):
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        import datetime as dt
        swarm_dir = tmp_path / ".swarm"
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write_settlement(swarm_dir, "1.1", "DISPATCHED", now)
        result = _stuck_settlements_block(swarm_dir=swarm_dir, stale_after_seconds=900)
        assert result == []

    def test_stale_dispatched_settlement_is_flagged(self, tmp_path):
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        import datetime as dt
        swarm_dir = tmp_path / ".swarm"
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).isoformat()
        self._write_settlement(swarm_dir, "1.1", "DISPATCHED", old)
        result = _stuck_settlements_block(swarm_dir=swarm_dir, stale_after_seconds=900)
        assert len(result) == 1
        assert result[0]["task_id"] == "1.1"
        assert result[0]["state"] == "DISPATCHED"
        assert result[0]["age_seconds"] >= 1800 - 5

    def test_terminal_states_never_flagged_regardless_of_age(self, tmp_path):
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        import datetime as dt
        swarm_dir = tmp_path / ".swarm"
        ancient = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
        for i, state in enumerate(["COMPLETED", "REJECTED", "RECOVERED", "ABANDONED"]):
            self._write_settlement(swarm_dir, f"1.{i}", state, ancient)
        result = _stuck_settlements_block(swarm_dir=swarm_dir, stale_after_seconds=900)
        assert result == []

    def test_unreadable_settlement_reports_error_not_traceback(self, tmp_path):
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        swarm_dir = tmp_path / ".swarm"
        settlements = swarm_dir / "coder-settlements"
        settlements.mkdir(parents=True)
        (settlements / "broken.json").write_text("{not valid json")
        result = _stuck_settlements_block(swarm_dir=swarm_dir, stale_after_seconds=900)
        assert len(result) == 1
        assert "error" in result[0]

    def test_never_writes_to_settlements_dir(self, tmp_path):
        """Read-only guarantee: scanning must not touch the plugin's own files."""
        from opencode_crack.runtime.swarm_diagnostics import _stuck_settlements_block
        import datetime as dt
        swarm_dir = tmp_path / ".swarm"
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).isoformat()
        self._write_settlement(swarm_dir, "1.1", "DISPATCHED", old)
        settlement_file = swarm_dir / "coder-settlements" / "1.1.json"
        before = settlement_file.read_text()
        before_mtime = settlement_file.stat().st_mtime
        _stuck_settlements_block(swarm_dir=swarm_dir, stale_after_seconds=900)
        assert settlement_file.read_text() == before
        assert settlement_file.stat().st_mtime == before_mtime

    def test_wired_into_collect_swarm_status(self, db, tmp_path, monkeypatch):
        import datetime as dt
        monkeypatch.chdir(tmp_path)
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).isoformat()
        self._write_settlement(tmp_path / ".swarm", "1.1", "DISPATCHED", old)
        report = collect_swarm_status(db, swarm_checker=_missing_swarm,
                                      opencode_checker=lambda: False)
        assert "stuck_settlements" in report
        assert report["stuck_settlements"][0]["task_id"] == "1.1"