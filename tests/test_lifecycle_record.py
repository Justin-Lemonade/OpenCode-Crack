"""
Manager-loop result/report fixtures (roadmap D-147, broken out of C-074).

Pins that all four lifecycle cases — manager assignment, worker completion,
reviewer rejection, successful approval — are represented by deterministic
fixtures with explicit task/agent/session IDs, status, and evidence fields,
and that the pure JSON/Markdown serializers in lifecycle_record.py produce
stable output. No model calls, no I/O, no DB.
"""
import json

import pytest

from opencode_crack.runtime.lifecycle_record import (
    STATUSES,
    EvidenceItem,
    LifecycleRecord,
    lifecycle_fields,
    render_lifecycle_json,
    render_lifecycle_markdown,
)
from tests.lifecycle_fixtures import (
    build_manager_assignment,
    build_reviewer_rejection,
    build_successful_approval,
    build_worker_completion,
)


# --- all four lifecycle cases represented -------------------------------------

class TestFourLifecycleCases:
    def test_manager_assignment(self):
        record = build_manager_assignment()
        assert record.status == "assigned"
        assert record.agent_id == "worker"
        assert record.task_id == "D-147"
        assert record.session_id == "ses-manager-1"
        assert any(e.kind == "task" for e in record.evidence)

    def test_worker_completion(self):
        record = build_worker_completion()
        assert record.status == "completed"
        assert record.agent_id == "worker"
        assert any(e.kind == "test" for e in record.evidence)
        assert any(e.kind == "path" for e in record.evidence)

    def test_reviewer_rejection(self):
        record = build_reviewer_rejection()
        assert record.status == "rejected"
        assert record.agent_id == "reviewer"
        assert any(e.kind == "reason" for e in record.evidence)

    def test_successful_approval(self):
        record = build_successful_approval()
        assert record.status == "approved"
        assert record.agent_id == "reviewer"
        assert any(e.kind == "artifact" for e in record.evidence)

    def test_all_four_statuses_covered(self):
        statuses = {r.status for r in (
            build_manager_assignment(),
            build_worker_completion(),
            build_reviewer_rejection(),
            build_successful_approval(),
        )}
        assert statuses == {"assigned", "completed", "rejected", "approved"}

    def test_fixtures_are_deterministic(self):
        for build in (build_manager_assignment, build_worker_completion,
                      build_reviewer_rejection, build_successful_approval):
            assert build() == build()
            assert render_lifecycle_json(build()) == render_lifecycle_json(build())


# --- explicit IDs, status, evidence ------------------------------------------

class TestExplicitFields:
    def test_ids_and_status_are_explicit_in_fields(self):
        record = build_worker_completion()
        fields = lifecycle_fields(record)
        assert fields["task_id"] == "D-147"
        assert fields["agent_id"] == "worker"
        assert fields["session_id"] == "ses-worker-1"
        assert fields["status"] == "completed"
        assert isinstance(fields["evidence"], list)
        assert fields["evidence"][0]["kind"] == "path"

    def test_notes_omitted_when_empty(self):
        fields = lifecycle_fields(build_reviewer_rejection())
        assert "notes" not in fields

    def test_notes_present_when_set(self):
        fields = lifecycle_fields(build_manager_assignment())
        assert fields["notes"] != ""


# --- stable serialization -----------------------------------------------------

class TestSerialization:
    def test_json_is_stable_and_round_trippable(self):
        for build in (build_manager_assignment, build_worker_completion,
                      build_reviewer_rejection, build_successful_approval):
            rendered = render_lifecycle_json(build())
            parsed = json.loads(rendered)
            assert parsed["task_id"] == "D-147"
            assert parsed["status"] == build().status

    def test_json_and_markdown_share_fields(self):
        record = build_worker_completion()
        md = render_lifecycle_markdown(record)
        for key in ("task_id", "agent_id", "session_id", "status"):
            assert lifecycle_fields(record)[key] in md
        for item in record.evidence:
            assert item.value in md

    def test_markdown_renders_all_cases(self):
        md = render_lifecycle_markdown(build_reviewer_rejection())
        assert "Status: rejected" in md
        assert "[reason] Evidence does not reference the full diff" in md


# --- validation ---------------------------------------------------------------

class TestValidation:
    def test_bad_status_rejected(self):
        with pytest.raises(ValueError):
            LifecycleRecord("D-1", "a", "s", "bogus")

    def test_empty_ids_rejected(self):
        with pytest.raises(ValueError):
            LifecycleRecord("", "a", "s", "assigned")
        with pytest.raises(ValueError):
            LifecycleRecord("D-1", "", "s", "assigned")
        with pytest.raises(ValueError):
            LifecycleRecord("D-1", "a", "", "assigned")

    def test_empty_evidence_rejected(self):
        with pytest.raises(ValueError):
            LifecycleRecord("D-1", "a", "s", "assigned",
                            evidence=[EvidenceItem("", "x")])
        with pytest.raises(ValueError):
            LifecycleRecord("D-1", "a", "s", "assigned",
                            evidence=[EvidenceItem("kind", "")])

    def test_statuses_constant(self):
        assert STATUSES == ("assigned", "completed", "rejected", "approved")


# --- offline guarantee --------------------------------------------------------

def test_no_model_calls_or_io(monkeypatch):
    """The record + serializers must not import or call a model backend."""
    for mod in ("opencode_crack.runtime.lifecycle_record", "tests.lifecycle_fixtures"):
        assert "model" not in getattr(__import__(mod, fromlist=["x"]), "__dict__", {})
    record = build_successful_approval()
    assert "approved" in render_lifecycle_markdown(record)