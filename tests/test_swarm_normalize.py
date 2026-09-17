"""
Swarm command/result serialization fixtures + normalizers (roadmap D-142,
broken out of C-074).

Pins that fixtures exist for successful swarms, partial/failed swarms, and
agent-turn-done / agent-settled events, and that the pure normalizers in
src/runtime/swarm_normalize.py preserve swarm ID, agent name, status, model,
tokens/cost and error text, degrade malformed input without crashing, and
stay deterministic and offline.
"""
import json

import pytest

from opencode_crack.runtime.swarm_normalize import (
    AgentRecord,
    SwarmEventRecord,
    SwarmRunRecord,
    normalize_agent,
    normalize_event,
    normalize_swarm_result,
    parse_event_line,
    parse_events_text,
)
from tests.swarm_fixtures import (
    build_events_text,
    build_events_text_malformed,
    build_partial_failed_swarm,
    build_successful_swarm,
)


# --- fixtures exist and are deterministic -------------------------------------

class TestFixtures:
    def test_successful_swarm_fixture(self):
        raw = build_successful_swarm()
        assert raw["swarmId"] == "sw_fixture_success"
        assert raw["status"] == "completed"
        assert [a["name"] for a in raw["agents"]] == ["manager", "worker", "tester"]
        assert all(a["status"] == "completed" for a in raw["agents"])

    def test_partial_failed_swarm_fixture(self):
        raw = build_partial_failed_swarm()
        assert raw["status"] == "failed"
        by_name = {a["name"]: a for a in raw["agents"]}
        assert by_name["worker"]["status"] == "failed"
        assert "error" in by_name["worker"]
        assert by_name["tester"]["status"] == "never_started"

    def test_event_fixture_has_turn_done_and_settled(self):
        events = parse_events_text(build_events_text())
        assert [e.event_type for e in events] == ["agent-turn-done", "agent-settled"]

    def test_fixtures_are_deterministic(self):
        assert build_successful_swarm() == build_successful_swarm()
        assert build_partial_failed_swarm() == build_partial_failed_swarm()
        assert build_events_text() == build_events_text()

    def test_fixtures_are_pure_dicts_and_text(self):
        assert isinstance(build_successful_swarm(), dict)
        assert isinstance(build_partial_failed_swarm(), dict)
        assert isinstance(build_events_text(), str)


# --- normalize_swarm_result ---------------------------------------------------

class TestNormalizeSwarmResult:
    def test_successful_swarm_preserves_swarm_id_and_status(self):
        rec = normalize_swarm_result(build_successful_swarm())
        assert rec.swarm_id == "sw_fixture_success"
        assert rec.status == "completed"
        assert rec.error is None

    def test_agents_are_normalized(self):
        rec = normalize_swarm_result(build_successful_swarm())
        assert len(rec.agents) == 3
        worker = rec.agents[1]
        assert worker.name == "worker"
        assert worker.status == "completed"
        assert worker.model == "openai/gpt-4o-mini"
        assert worker.tokens == 18400
        assert worker.cost == 0.31

    def test_partial_failed_preserves_error_text(self):
        rec = normalize_swarm_result(build_partial_failed_swarm())
        worker = next(a for a in rec.agents if a.name == "worker")
        assert worker.status == "failed"
        assert "max rounds" in worker.error

    def test_snake_case_aliases_accepted(self):
        rec = normalize_swarm_result(
            {"swarm_id": "sw_x", "state": "failed",
             "agents": [{"agent": "worker", "status": "completed", "model_id": "m1",
                         "token_count": 5, "cost_usd": 0.1, "error_text": "boom"}]}
        )
        assert rec.swarm_id == "sw_x"
        assert rec.status == "failed"
        worker = rec.agents[0]
        assert worker.name == "worker"
        assert worker.tokens == 5
        assert worker.cost == 0.1
        assert worker.error == "boom"

    def test_missing_agents_is_empty_not_crash(self):
        rec = normalize_swarm_result({"swarmId": "sw_y"})
        assert rec.agents == ()
        assert rec.status == "unknown"

    def test_malformed_non_dict_result_never_raises(self):
        rec = normalize_swarm_result("garbage")
        assert isinstance(rec, SwarmRunRecord)
        assert rec.error is not None


class TestNormalizeAgent:
    def test_absent_fields_are_none(self):
        agent = normalize_agent({"name": "worker"})
        assert agent.model is None
        assert agent.tokens is None
        assert agent.cost is None
        assert agent.error is None
        assert agent.status is None

    def test_non_dict_is_empty_record(self):
        assert normalize_agent([1, 2, 3]) == AgentRecord()

    def test_camel_and_snake_tokens_cost(self):
        assert normalize_agent({"tokenCount": "42"}).tokens == 42
        assert normalize_agent({"costUSD": "0.50"}).cost == 0.5


# --- normalize_event / parse_events_text -------------------------------------

class TestNormalizeEvent:
    def test_turn_done_preserves_attribution_and_cost(self):
        rec = normalize_event(json.loads(
            '{"id": "evt-0001", "type": "agent-turn-done", "agent": "worker",'
            ' "sessionId": "ses-worker-1", "taskId": "D-142", "model": "openai/gpt-4o-mini",'
            ' "tokens": 7200, "cost": 0.12}'
        ))
        assert rec.event_id == "evt-0001"
        assert rec.event_type == "agent-turn-done"
        assert rec.agent == "worker"
        assert rec.session_id == "ses-worker-1"
        assert rec.task_id == "D-142"
        assert rec.tokens == 7200
        assert rec.cost == 0.12

    def test_settled_preserves_terminal_status(self):
        rec = normalize_event({"id": "e2", "type": "agent-settled", "agent": "worker",
                               "session_id": "s1", "status": "settled"})
        assert rec.event_type == "agent-settled"
        assert rec.status == "settled"

    def test_event_with_no_usable_type_is_explicit_unknown(self):
        rec = normalize_event({"some": "junk"})
        assert rec.event_type == "unknown"
        assert isinstance(rec, SwarmEventRecord)

    def test_non_dict_event_is_explicit_unknown_never_crash(self):
        rec = normalize_event("not a dict")
        assert rec.event_type == "unknown"
        assert rec.error is not None


class TestParseEventsText:
    def test_valid_stream_parses_all_events(self):
        events = parse_events_text(build_events_text())
        assert len(events) == 2
        assert all(isinstance(e, SwarmEventRecord) for e in events)

    def test_malformed_lines_are_ignored_not_crash(self):
        events = parse_events_text(build_events_text_malformed())
        assert len(events) == 3  # two valid + one {"type": ...}-only object
        assert all(e.event_type != "unknown" for e in events)

    def test_blank_input_is_empty(self):
        assert parse_events_text("") == []
        assert parse_events_text("\n\n\n") == []

    def test_deterministic_across_calls(self):
        assert parse_events_text(build_events_text()) == parse_events_text(build_events_text())


class TestParseEventLine:
    def test_blank_line_is_none(self):
        assert parse_event_line("") is None
        assert parse_event_line("   ") is None

    def test_bad_json_is_none(self):
        assert parse_event_line("this is not json {{{") is None

    def test_non_object_json_is_none(self):
        assert parse_event_line('["an", "array"]') is None
        assert parse_event_line("42") is None

    def test_object_line_returns_record(self):
        rec = parse_event_line('{"id": "e", "type": "agent-settled", "agent": "tester"}')
        assert rec is not None
        assert rec.event_type == "agent-settled"


# --- offline guarantee --------------------------------------------------------

def test_normalizers_never_touch_io(monkeypatch):
    """The pure normalizers must not read files, sockets, or subprocesses."""
    monkeypatch.setattr(
        "builtins.open",
        lambda *a, **k: pytest.fail("open must not be used by normalizers"),
    )
    for name in ("subprocess", "socket"):
        monkeypatch.setattr(
            f"opencode_crack.runtime.swarm_normalize.{name}",
            lambda *a, **k: pytest.fail(f"{name} must not be used by normalizers"),
            raising=False,
        )
    rec = normalize_swarm_result(build_successful_swarm())
    events = parse_events_text(build_events_text())
    assert rec.swarm_id == "sw_fixture_success"
    assert len(events) == 2
