"""
Runtime-state fixtures for the manager loop (roadmap D-164).

Pins that the six runtime states the future manager loop must handle —
worker success, worker failure, timeout, unavailable runtime, reviewer
rejection, and stale session — are deterministic fixtures that carry
explicit task/agent/session identifiers and evidence/status, serialize
stably, have no ambiguous terminal states, and are consumable without live
OpenCode (each carries its underlying normalized source record).
"""
import pytest

from opencode_crack.runtime.lifecycle_record import LifecycleRecord
from opencode_crack.runtime.swarm_normalize import SwarmRunRecord
from tests.runtime_state_fixtures import (
    CANONICAL_STATES,
    BUILDERS,
    RuntimeState,
    build_all,
    build_worker_success,
)


# --- coverage: all six states present ---------------------------------------------

class TestCoverage:
    def test_all_canonical_states_are_buildable(self):
        states = build_all()
        assert set(states.keys()) == set(CANONICAL_STATES)
        assert len(states) == 6

    def test_every_state_has_identifiers(self):
        for name, state in build_all().items():
            assert state.task_id, name
            assert state.agent_id, name
            assert state.session_id, name

    def test_every_state_has_evidence(self):
        for name, state in build_all().items():
            assert state.evidence, name


# --- explicit, unambiguous status ---------------------------------------------------

class TestExplicitStatus:
    def test_no_ambiguous_statuses(self):
        # Every state's status is explicit and distinct from the "unknown"
        # sentinel and from empty.
        states = build_all()
        for name, state in states.items():
            assert state.status, name
            assert state.status != "unknown", name

    def test_statuses_are_distinct_per_state(self):
        statuses = {state.status for state in build_all().values()}
        assert len(statuses) == len(CANONICAL_STATES)
        assert statuses == {"completed", "failed", "timed_out", "unavailable",
                            "rejected", "stale"}

    def test_state_constructor_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            RuntimeState(state="timeout", task_id="T-1", agent_id="a",
                         session_id="s", status="unknown", evidence=())

    def test_state_constructor_rejects_empty_status(self):
        with pytest.raises(ValueError):
            RuntimeState(state="timeout", task_id="T-1", agent_id="a",
                         session_id="s", status="", evidence=())

    def test_state_constructor_rejects_unknown_state_name(self):
        with pytest.raises(ValueError):
            RuntimeState(state="nonsense", task_id="T-1", agent_id="a",
                         session_id="s", status="x", evidence=())

    def test_state_constructor_rejects_missing_identifier(self):
        with pytest.raises(ValueError):
            RuntimeState(state="timeout", task_id="", agent_id="a",
                         session_id="s", status="timed_out", evidence=())


# --- stable serialization ------------------------------------------------------------

class TestStableSerialization:
    def test_serialization_is_byte_stable(self):
        for name in CANONICAL_STATES:
            first = BUILDERS[name]().to_json()
            second = BUILDERS[name]().to_json()
            assert first == second, name

    def test_serialization_round_trips_through_dict(self):
        import json
        for name in CANONICAL_STATES:
            state = BUILDERS[name]()
            parsed = json.loads(state.to_json())
            assert parsed["state"] == name
            assert parsed["status"] == state.status
            assert parsed["task_id"] == state.task_id

    def test_evidence_serializes_with_kind_and_value(self):
        for name in CANONICAL_STATES:
            parsed = BUILDERS[name]().as_dict()
            assert all({"kind", "value"} <= set(e) for e in parsed["evidence"]), name


# --- consumable without live OpenCode ------------------------------------------------

class TestNoLiveRuntime:
    def test_swarm_states_carry_normalized_source(self):
        for name in ("worker_success", "worker_failure", "timeout", "unavailable_runtime"):
            source = BUILDERS[name]().source
            assert isinstance(source, SwarmRunRecord)

    def test_reviewer_rejection_carries_lifecycle_record(self):
        source = build_all()["reviewer_rejection"].source
        assert isinstance(source, LifecycleRecord)

    def test_stale_session_carries_lease_snapshot(self):
        source = build_all()["stale_session"].source
        assert source["expires_at"]
        assert source["task_id"]

    def test_worker_success_has_green_test_evidence(self):
        state = build_worker_success()
        assert any(e.kind == "test" and "pytest" in e.value for e in state.evidence)