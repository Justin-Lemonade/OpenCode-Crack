"""
Context telemetry for token-efficiency measurement (roadmap D-160).

Pins the opt-in gate, redaction (only capped integers are ever stored, no
content), bounded payload size, aggregate counts per workflow, and the
context-to-work ratio used to compare the old heavyweight workflow against
the compact one.
"""
import json
from pathlib import Path

import pytest

from opencode_crack.runtime import control_db
from opencode_crack.runtime.context_telemetry import (
    EVENT_TYPE,
    _bounded,
    estimate_tokens,
    record_context_usage,
    summarize_context_usage,
    telemetry_enabled,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("AI_BRAIN_CONTEXT_TELEMETRY", "1")


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("AI_BRAIN_CONTEXT_TELEMETRY", raising=False)


# --- opt-in gate --------------------------------------------------------------

class TestOptIn:
    def test_off_by_default(self, disabled):
        assert telemetry_enabled() is False

    def test_on_for_truthy_values(self, monkeypatch):
        for value in ("1", "true", "yes", "on", "TRUE"):
            monkeypatch.setenv("AI_BRAIN_CONTEXT_TELEMETRY", value)
            assert telemetry_enabled() is True

    def test_off_for_anything_else(self, monkeypatch):
        for value in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("AI_BRAIN_CONTEXT_TELEMETRY", value)
            assert telemetry_enabled() is False

    def test_record_is_noop_when_disabled(self, disabled, db):
        assert record_context_usage("D-160", protocol_tokens=100, db_path=db) is False
        rows = control_db.get_recent_events(limit=10, db_path=db)
        assert rows == []

    def test_record_writes_when_enabled(self, enabled, db):
        assert record_context_usage("D-160", protocol_tokens=100, db_path=db) is True
        rows = control_db.get_recent_events(limit=10, db_path=db)
        assert len(rows) == 1
        assert rows[0]["event_type"] == EVENT_TYPE


# --- estimation & bounds ------------------------------------------------------

class TestEstimation:
    def test_estimate_tokens_chars_per_four(self):
        assert estimate_tokens(100) == 25
        assert estimate_tokens(0) == 0

    def test_estimate_clamps_negative(self):
        assert estimate_tokens(-50) == 0

    def test_bounded_clamps_high(self):
        assert _bounded(10_000_000) == 1_000_000

    def test_bounded_clamps_negative_to_zero(self):
        assert _bounded(-5) == 0


# --- redaction & bounded payload ----------------------------------------------

class TestRedaction:
    def test_payload_contains_only_capped_ints_and_workflow(self, enabled, db):
        record_context_usage(
            "D-160",
            protocol_tokens=400, contract_tokens=800, briefing_tokens=300,
            handoff_tokens=500, output_tokens=1200, workflow="compact",
            db_path=db,
        )
        row = control_db.get_recent_events(limit=10, db_path=db)[0]
        payload = json.loads(row["payload"])
        assert payload == {
            "workflow": "compact",
            "context": {"protocol": 400, "contract": 800, "briefing": 300, "handoff": 500},
            "output": 1200,
            "total_context": 2000,
        }

    def test_no_content_fields_in_schema(self, enabled, db):
        # The payload schema is structurally incapable of carrying content:
        # every key is a category with an int value, plus a workflow label.
        record_context_usage("D-160", protocol_tokens=1, db_path=db)
        payload = json.loads(control_db.get_recent_events(limit=10, db_path=db)[0]["payload"])
        for key, value in payload.items():
            if key == "workflow":
                assert isinstance(value, str)
            elif key == "context":
                assert isinstance(value, dict)
                assert all(isinstance(v, int) for v in value.values())
            else:
                assert isinstance(value, int)

    def test_event_size_bounded(self, enabled, db):
        # Even with the maximum possible category values, the serialized
        # payload stays well under a 1 KB bound.
        record_context_usage(
            "D-160",
            protocol_tokens=10**9, contract_tokens=10**9, briefing_tokens=10**9,
            handoff_tokens=10**9, output_tokens=10**9, db_path=db,
        )
        payload = json.loads(control_db.get_recent_events(limit=10, db_path=db)[0]["payload"])
        assert len(json.dumps(payload)) < 1024
        assert payload["total_context"] == 4_000_000  # each category capped at 1M


# --- aggregates & context-to-work ratio ---------------------------------------

class TestAggregates:
    def test_empty_when_nothing_recorded(self, db):
        summary = summarize_context_usage(db_path=db)
        assert summary["count"] == 0
        assert summary["workflows"] == {}

    def test_aggregates_split_by_workflow(self, enabled, db):
        # two legacy tasks: protocol/contract 400 then 800 -> totals 800, 1600
        # two compact tasks: protocol/contract 100 each -> totals 200, 200
        for i, protocol in enumerate((400, 800), start=1):
            record_context_usage(f"D-L{i}", protocol_tokens=protocol, contract_tokens=protocol,
                                 briefing_tokens=0, handoff_tokens=0, output_tokens=2000,
                                 workflow="legacy", db_path=db)
        for i, protocol in enumerate((100, 100), start=1):
            record_context_usage(f"D-C{i}", protocol_tokens=protocol, contract_tokens=protocol,
                                 briefing_tokens=0, handoff_tokens=0, output_tokens=2000,
                                 workflow="compact", db_path=db)

        summary = summarize_context_usage(db_path=db)
        assert summary["count"] == 4
        assert summary["overall"]["count"] == 4
        assert summary["overall"]["avg_total_context"] == 700  # (800+1600+200+200)/4
        assert summary["overall"]["avg_output"] == 2000
        assert summary["overall"]["context_to_work_ratio"] == pytest.approx(700 / 2000, abs=0.01)

        legacy = summary["workflows"]["legacy"]
        assert legacy["count"] == 2
        assert legacy["avg_context"]["protocol"] == 600
        assert legacy["avg_total_context"] == 1200  # (800+1600)/2

        compact = summary["workflows"]["compact"]
        assert compact["count"] == 2
        assert compact["avg_context"]["protocol"] == 100
        assert compact["avg_total_context"] == 200

    def test_ratio_is_none_when_no_output(self, enabled, db):
        record_context_usage("D-X", protocol_tokens=100, output_tokens=0, db_path=db)
        summary = summarize_context_usage(db_path=db)
        assert summary["overall"]["context_to_work_ratio"] is None
