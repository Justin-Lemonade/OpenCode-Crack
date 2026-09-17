"""
Swarm results -> evidence-first handoffs (roadmap D-163).

Pins the pure adapter that converts a normalized SwarmRunRecord plus known
task/report/test evidence into the existing D-157 Handoff format: only
worker->manager and manager->tester directions; no narrative duplication
(task id, changed artifacts, test evidence, runtime result, requested
decision each represented once); success/failure/blocked render
deterministically as JSON and Markdown; no model calls, DB writes, or
subprocesses.
"""
import json

import pytest

from opencode_crack.runtime.handoff import Handoff, TestRun, render_handoff_json, render_handoff_markdown
from opencode_crack.runtime.swarm_handoff import build_swarm_handoff, outcome_for_status
from opencode_crack.runtime.swarm_normalize import AgentRecord, SwarmRunRecord


def _run(status="completed", swarm_id="sw_handoff_1", error=None):
    return SwarmRunRecord(
        swarm_id=swarm_id,
        status=status,
        agents=(AgentRecord(name="worker"),),
        error=error,
    )


def _handoff(result, sender="worker", recipient="manager", decision="Approve and forward to tester"):
    return build_swarm_handoff(
        result,
        task_id="D-163",
        sender=sender,
        recipient=recipient,
        decision_requested=decision,
        changed_paths=["src/runtime/handoff.py", "tests/test_handoff.py"],
        tests=[TestRun(command="python -m pytest tests/test_handoff.py", result="12 passed")],
        artifacts=["reports/D-163_report.md"],
        known_risks=["integration not yet run"],
    )


# --- outcome mapping ------------------------------------------------------------

class TestOutcomeMapping:
    def test_completed_maps_to_success(self):
        assert outcome_for_status("completed") == "success"

    def test_failed_maps_to_failure(self):
        assert outcome_for_status("failed") == "failure"

    def test_unknown_maps_to_blocked(self):
        assert outcome_for_status("unknown") == "blocked"

    def test_empty_status_maps_to_blocked(self):
        assert outcome_for_status("") == "blocked"

    def test_cancelled_maps_to_blocked(self):
        assert outcome_for_status("cancelled") == "blocked"


# --- directions -----------------------------------------------------------------

class TestDirections:
    def test_worker_to_manager_supported(self):
        h = _handoff(_run(), sender="worker", recipient="manager")
        assert (h.sender, h.recipient) == ("worker", "manager")

    def test_manager_to_tester_supported(self):
        h = _handoff(_run(), sender="manager", recipient="tester",
                     decision="Verify on main")
        assert (h.sender, h.recipient) == ("manager", "tester")

    def test_unsupported_direction_rejected(self):
        with pytest.raises(ValueError):
            _handoff(_run(), sender="tester", recipient="worker")


# --- no narrative duplication ---------------------------------------------------

class TestNoDuplication:
    def test_runtime_result_represented_as_reference_not_narrative(self):
        h = _handoff(_run(swarm_id="sw_ref"))
        assert h.outcome == "success"
        assert "swarm:sw_ref" in h.artifacts
        # the swarm id appears only once as a reference artifact
        assert sum(a == "swarm:sw_ref" for a in h.artifacts) == 1

    def test_evidence_fields_pass_through_once(self):
        h = _handoff(_run())
        assert h.task_id == "D-163"
        assert h.changed_paths == ["src/runtime/handoff.py", "tests/test_handoff.py"]
        assert h.tests == [TestRun("python -m pytest tests/test_handoff.py", "12 passed")]
        assert "reports/D-163_report.md" in h.artifacts
        assert h.decision_requested == "Approve and forward to tester"

    def test_error_is_single_verbatim_risk_line(self):
        h = _handoff(_run(status="failed", error="boom"))
        assert h.outcome == "failure"
        assert "runtime error: boom" in h.known_risks
        assert sum(r == "runtime error: boom" for r in h.known_risks) == 1

    def test_no_error_no_runtime_risk_line(self):
        h = _handoff(_run())
        assert all(not r.startswith("runtime error:") for r in h.known_risks)

    def test_no_swarm_id_no_reference_artifact(self):
        h = _handoff(_run(swarm_id=None))
        assert all(not a.startswith("swarm:") for a in h.artifacts)


# --- deterministic rendering -----------------------------------------------------

class TestDeterministicRendering:
    @pytest.mark.parametrize("status", ["completed", "failed", "unknown"])
    def test_json_renders_deterministically(self, status):
        h1 = _handoff(_run(status=status, error="x" if status == "failed" else None))
        h2 = _handoff(_run(status=status, error="x" if status == "failed" else None))
        assert render_handoff_json(h1) == render_handoff_json(h2)
        parsed = json.loads(render_handoff_json(h1))
        assert parsed["outcome"] == outcome_for_status(status)

    @pytest.mark.parametrize("status", ["completed", "failed", "unknown"])
    def test_markdown_renders_deterministically(self, status):
        h1 = _handoff(_run(status=status, error="x" if status == "failed" else None))
        h2 = _handoff(_run(status=status, error="x" if status == "failed" else None))
        assert render_handoff_markdown(h1) == render_handoff_markdown(h2)
        assert f"Outcome: {outcome_for_status(status)}" in render_handoff_markdown(h1)

    def test_three_outcomes_are_distinct(self):
        outs = {_handoff(_run(status=s)).outcome for s in ("completed", "failed", "unknown")}
        assert outs == {"success", "failure", "blocked"}


# --- purity ----------------------------------------------------------------------

class TestPurity:
    def test_returns_handoff_value_type(self):
        h = _handoff(_run())
        assert isinstance(h, Handoff)

    def test_no_side_effects_on_inputs(self):
        result = _run(status="failed", error="boom")
        changed = ["a.py"]
        tests = [TestRun("pytest x", "ok")]
        arts = ["reports/r.md"]
        risks = ["r"]
        build_swarm_handoff(result, "T-1", "worker", "manager", "dec",
                            changed_paths=changed, tests=tests,
                            artifacts=arts, known_risks=risks)
        # inputs untouched, no mutation of the frozen record
        assert result.status == "failed"
        assert result.error == "boom"
        assert changed == ["a.py"] and tests and arts == ["reports/r.md"] and risks == ["r"]
        assert result.agents == (AgentRecord(name="worker"),)