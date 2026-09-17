"""
Offline manager -> worker -> tester vertical-slice harness (roadmap D-167).

Pins the deterministic offline harness that future manager-loop tests will
drive without model calls: it creates manager/worker/tester profiles, assigns
one bounded task (session + lease via the D-162 binding primitive), records
the worker result (normalized SwarmRunRecord) and the manager -> tester
Handoff (D-163 adapter), records tester success/rejection LifecycleRecords,
and exposes every identifier + evidence through one SliceRecord. Uses fake
runtime behavior only - no subprocess, no network, no model.
"""
import json
from pathlib import Path

import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import VALID_ROLES
from opencode_crack.runtime.handoff import Handoff
from opencode_crack.runtime.lease_binding import SessionLeaseBinding
from opencode_crack.runtime.lifecycle_record import LifecycleRecord
from opencode_crack.runtime.swarm_normalize import SwarmRunRecord
from opencode_crack.runtime.swarm_runtime import SwarmResult
from opencode_crack.runtime.vertical_slice import (
    DEFAULT_SESSION_ID,
    DEFAULT_SWARM_ID,
    BoundedTask,
    FakeSwarmRuntime,
    SliceRecord,
    VerticalSliceHarness,
    WorkerResult,
    create_profiles,
)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "control.db"


@pytest.fixture
def task():
    return BoundedTask(task_id="D-167", title="Offline vertical-slice harness")


@pytest.fixture
def harness(task, db):
    return VerticalSliceHarness(task, db_path=db)


# --- profiles ------------------------------------------------------------------


class TestProfiles:
    def test_creates_three_roles(self):
        manager, worker, tester = create_profiles(scope="slice")
        assert (manager.role, worker.role, tester.role) == ("manager", "worker", "tester")

    def test_ids_are_deterministic_per_scope(self):
        a = create_profiles(scope="abc")
        b = create_profiles(scope="abc")
        assert [p.agent_id for p in a] == [p.agent_id for p in b] == [
            "manager-abc", "worker-abc", "tester-abc",
        ]

    def test_worker_and_tester_report_to_manager(self):
        manager, worker, tester = create_profiles(scope="x")
        assert worker.manager_id == manager.agent_id
        assert tester.manager_id == manager.agent_id

    def test_empty_scope_rejected(self):
        with pytest.raises(ValueError):
            create_profiles(scope="")

    def test_profiles_are_validated_agentprofiles(self):
        for profile in create_profiles():
            assert profile.role in VALID_ROLES


# --- bounded task --------------------------------------------------------------


class TestBoundedTask:
    def test_valid_task_constructs(self):
        t = BoundedTask(task_id="T-1", title="Do a thing")
        assert (t.task_id, t.title) == ("T-1", "Do a thing")

    def test_empty_task_id_rejected(self):
        with pytest.raises(ValueError):
            BoundedTask(task_id="", title="x")

    def test_empty_title_rejected(self):
        with pytest.raises(ValueError):
            BoundedTask(task_id="T-1", title="   ")


# --- fake swarm runtime --------------------------------------------------------


class TestFakeSwarmRuntime:
    def test_run_returns_scripted_result(self, tmp_path):
        runtime = FakeSwarmRuntime(status="completed", swarm_id="sw_1", task_id="D-167")
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        result = runtime.run(cfg)
        assert isinstance(result, SwarmResult)
        assert result.swarm_id == "sw_1"
        assert result.status == "completed"
        assert result.error is None

    def test_run_records_the_invocation(self, tmp_path):
        runtime = FakeSwarmRuntime(task_id="D-167")
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        runtime.run(cfg, max_concurrent=3, budget_usd=1.0)
        assert len(runtime.calls) == 1
        assert runtime.calls[0][1]["max_concurrent"] == 3

    def test_failure_script_carries_error(self, tmp_path):
        runtime = FakeSwarmRuntime(status="failed", error="boom", task_id="D-167")
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        result = runtime.run(cfg)
        assert result.status == "failed"
        assert result.error == "boom"

    def test_ingest_events_writes_control_events(self, tmp_path):
        db_path = tmp_path / "control.db"
        control_db.init_db(db_path)
        runtime = FakeSwarmRuntime(task_id="D-167", db_path=db_path)
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        result = runtime.run(cfg)
        count = runtime.ingest_events(result)
        assert count == 2
        types = [e["event_type"] for e in control_db.get_recent_events(db_path=db_path)]
        assert "swarm:agent-turn-done" in types
        assert "swarm:agent-settled" in types

    def test_ingest_events_is_idempotent(self, tmp_path):
        db_path = tmp_path / "control.db"
        control_db.init_db(db_path)
        runtime = FakeSwarmRuntime(task_id="D-167", db_path=db_path)
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}", encoding="utf-8")
        result = runtime.run(cfg)
        assert runtime.ingest_events(result) == 2
        assert runtime.ingest_events(result) == 0  # deduped by source_event_id


# --- harness: end-to-end slice --------------------------------------------------


class TestSliceEndToEnd:
    def test_run_slice_records_full_slice(self, harness):
        record = harness.run_slice()
        assert isinstance(record, SliceRecord)
        assert record.lease_acquired is True
        assert record.worker_status == "completed"
        assert record.task_id == "D-167"

    def test_run_slice_registers_three_agents(self, harness):
        harness.run_slice()
        agents = control_db.list_agents(db_path=harness.db_path)
        assert {a["role"] for a in agents} == {"manager", "worker", "tester"}

    def test_run_slice_binds_lease_and_session(self, harness):
        record = harness.run_slice()
        with control_db._connect(harness.db_path) as conn:
            lease = conn.execute("SELECT * FROM leases WHERE task_id=?", ("D-167",)).fetchone()
            session = conn.execute("SELECT * FROM oc_sessions WHERE session_id=?",
                                   (DEFAULT_SESSION_ID,)).fetchone()
        assert lease is not None
        assert lease["agent_id"] == record.worker_id
        assert lease["session_id"] == DEFAULT_SESSION_ID
        assert session is not None
        assert session["task_id"] == "D-167"
        assert session["status"] == "settled"  # agent-settled ingestion

    def test_worker_result_is_normalized_record(self, harness):
        record = harness.run_slice()
        assert isinstance(record.worker_result, WorkerResult)
        assert isinstance(record.worker_result.record, SwarmRunRecord)
        assert record.worker_result.status == "completed"
        assert record.worker_result.swarm_id == DEFAULT_SWARM_ID
        assert any(e.kind == "swarm" for e in record.worker_result.evidence)

    def test_manager_to_tester_handoff_recorded(self, harness):
        record = harness.run_slice()
        assert isinstance(record.handoff, Handoff)
        # D-157 identifies roles, not concrete agent instances.
        assert (record.handoff.sender, record.handoff.recipient) == ("manager", "tester")
        assert record.handoff.outcome == "success"
        assert f"swarm:{DEFAULT_SWARM_ID}" in record.handoff.artifacts

    def test_tester_success_and_rejection_fixtures(self, harness):
        record = harness.run_slice()
        assert isinstance(record.tester_success, LifecycleRecord)
        assert record.tester_success.status == "approved"
        assert record.tester_success.agent_id == record.tester_id
        assert isinstance(record.tester_rejection, LifecycleRecord)
        assert record.tester_rejection.status == "rejected"
        assert record.tester_rejection.agent_id == record.tester_id

    def test_slice_exposes_all_identifiers(self, harness):
        record = harness.run_slice()
        ids = record.identifiers()
        assert ids["task_id"] == "D-167"
        assert ids["manager_id"].startswith("manager-")
        assert ids["worker_id"].startswith("worker-")
        assert ids["tester_id"].startswith("tester-")
        assert ids["session_id"] == DEFAULT_SESSION_ID
        assert ids["swarm_id"] == DEFAULT_SWARM_ID

    def test_slice_exposes_evidence(self, harness):
        record = harness.run_slice()
        assert len(record.evidence()) >= 5
        assert all(hasattr(e, "kind") and hasattr(e, "value") for e in record.evidence())

    def test_control_plane_events_recorded(self, harness):
        harness.run_slice()
        types = [e["event_type"] for e in control_db.get_recent_events(db_path=harness.db_path)]
        assert "lease_claimed" in types
        assert "session_started" in types
        assert "swarm:agent-settled" in types
        assert "vertical_slice:worker_result" in types
        assert "vertical_slice:handoff" in types

    def test_no_ephemeral_config_left_behind(self, harness, tmp_path):
        harness.run_slice()
        assert not list(tmp_path.glob("ai_brain_slice_*.json"))

    def test_swarm_config_renders_three_roles(self, harness):
        harness.run_slice()
        payload = json.loads(harness.runtime.config_seen)
        assert {a["role"] for a in payload["agents"]} == {"manager", "worker", "tester"}


# --- failure branch -------------------------------------------------------------


class TestFailureBranch:
    def test_failed_worker_maps_to_failure_handoff(self, task, db):
        runtime = FakeSwarmRuntime(status="failed", error="worker hit max rounds", task_id="D-167")
        harness = VerticalSliceHarness(task, db_path=db, runtime=runtime)
        record = harness.run_slice()
        assert record.worker_status == "failed"
        assert record.handoff.outcome == "failure"
        assert "runtime error: worker hit max rounds" in record.handoff.known_risks

    def test_lease_contention_reports_no_owned_work(self, task, db):
        control_db.init_db(db)
        other = create_profiles(scope="other")
        control_db.register_agent(other[1], db_path=db)
        assert control_db.acquire_lease("D-167", other[1].agent_id, db_path=db)
        harness = VerticalSliceHarness(task, db_path=db)
        harness.register()
        binding = harness.assign_task()
        assert isinstance(binding, SessionLeaseBinding)
        assert binding.owned_work is False
        assert binding.reason == "lease_not_acquired"


# --- determinism ----------------------------------------------------------------


class TestDeterminism:
    def test_slice_record_json_is_byte_stable(self, harness):
        first = harness.run_slice().to_json()
        second = harness.run_slice().to_json()
        assert first == second

    def test_slice_record_json_round_trips(self, harness):
        parsed = json.loads(harness.run_slice().to_json())
        assert parsed["task_id"] == "D-167"
        assert parsed["worker_status"] == "completed"
        assert parsed["handoff"]["outcome"] == "success"
        assert parsed["tester_success"]["status"] == "approved"
        assert parsed["tester_rejection"]["status"] == "rejected"