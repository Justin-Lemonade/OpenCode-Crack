"""Tests for src/runtime/handoff.py — the evidence-first manager/tester
handoff format (roadmap D-157).

A handoff must carry task, changed paths, test commands/results, artifact
locations, known risks, and an explicit decision requested; serialize to
both JSON and compact Markdown; and be byte-stable across repeated renders
so a supervising agent can parse it deterministically. All tests are pure —
no DB, no subprocess, no agent runtime.
"""
import json

import pytest

from opencode_crack.runtime.handoff import (
    ALLOWED_HANDOFFS,
    OUTCOMES,
    Handoff,
    TestRun,
    example_handoff,
    handoff_fields,
    render_handoff_json,
    render_handoff_markdown,
)


def _handoff(**overrides):
    base = dict(
        task_id="D-157",
        sender="worker",
        recipient="manager",
        outcome="success",
        changed_paths=["src/runtime/handoff.py", "tests/test_handoff.py"],
        tests=[TestRun(command="python -m pytest tests/test_handoff.py", result="12 passed")],
        artifacts=["reports/D-157_report.md"],
        known_risks=["Schema still under review"],
        decision_requested="Approve and forward to tester",
    )
    base.update(overrides)
    return Handoff(**base)


# --- validation ----------------------------------------------------------------

def test_valid_handoff_constructs():
    h = _handoff()
    assert h.task_id == "D-157"
    assert h.sender == "worker"
    assert h.recipient == "manager"


@pytest.mark.parametrize("sender,recipient", [("worker", "manager"), ("manager", "tester")])
def test_allowed_handoff_directions(sender, recipient):
    _handoff(sender=sender, recipient=recipient)


def test_disallowed_handoff_direction_raises():
    with pytest.raises(ValueError, match="sender/recipient"):
        _handoff(sender="tester", recipient="manager")


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_valid_outcomes(outcome):
    _handoff(outcome=outcome)


def test_invalid_outcome_raises():
    with pytest.raises(ValueError, match="outcome"):
        _handoff(outcome="maybe")


def test_empty_task_id_raises():
    with pytest.raises(ValueError, match="task_id"):
        _handoff(task_id="")


def test_empty_decision_requested_raises():
    with pytest.raises(ValueError, match="decision_requested"):
        _handoff(decision_requested="")


def test_empty_changed_path_entry_raises():
    with pytest.raises(ValueError, match="changed_paths"):
        _handoff(changed_paths=["src/a.py", ""])


# --- field shape ---------------------------------------------------------------

def test_fields_include_all_required_keys():
    h = _handoff()
    f = handoff_fields(h)
    assert set(f) == {
        "task_id", "sender", "recipient", "outcome",
        "changed_paths", "tests", "artifacts", "known_risks",
        "decision_requested",
    }
    assert f["tests"] == [{"command": "python -m pytest tests/test_handoff.py", "result": "12 passed"}]
    assert f["changed_paths"] == ["src/runtime/handoff.py", "tests/test_handoff.py"]
    assert f["known_risks"] == ["Schema still under review"]


# --- JSON form ----------------------------------------------------------------

def test_json_round_trips_fields():
    h = _handoff()
    data = json.loads(render_handoff_json(h))
    assert data["task_id"] == "D-157"
    assert data["sender"] == "worker"
    assert data["recipient"] == "manager"
    assert data["decision_requested"] == "Approve and forward to tester"
    assert data["tests"][0]["command"].startswith("python -m pytest")


def test_json_is_byte_stable_across_repeated_renders():
    h = _handoff()
    first = render_handoff_json(h)
    for _ in range(5):
        assert render_handoff_json(h) == first


def test_json_deterministic_regardless_of_input_construction_order():
    a = _handoff(changed_paths=["b.py", "a.py"], artifacts=["r2.md", "r1.md"])
    b = _handoff(changed_paths=["b.py", "a.py"], artifacts=["r2.md", "r1.md"])
    assert json.loads(render_handoff_json(a)) == json.loads(render_handoff_json(b))


def test_json_omits_nothing_when_fields_present():
    h = _handoff()
    assert "null" not in render_handoff_json(h)


# --- Markdown form -------------------------------------------------------------

def test_markdown_includes_task_outcome_decision():
    md = render_handoff_markdown(_handoff())
    assert "# Handoff D-157 (worker -> manager)" in md
    assert "Outcome: success" in md
    assert "Decision requested: Approve and forward to tester" in md


def test_markdown_lists_changed_paths_tests_artifacts_risks():
    md = render_handoff_markdown(_handoff())
    assert "Changed paths:" in md
    assert "- src/runtime/handoff.py" in md
    assert "Tests:" in md
    assert "`python -m pytest tests/test_handoff.py` — 12 passed" in md
    assert "Artifacts:" in md
    assert "- reports/D-157_report.md" in md
    assert "Known risks:" in md
    assert "- Schema still under review" in md


def test_markdown_omits_empty_sections():
    md = render_handoff_markdown(_handoff(changed_paths=[], tests=[], artifacts=[], known_risks=[]))
    assert "Changed paths:" not in md
    assert "Tests:" not in md
    assert "Artifacts:" not in md
    assert "Known risks:" not in md
    assert "Decision requested:" in md


def test_markdown_is_byte_stable_across_repeated_renders():
    h = _handoff()
    first = render_handoff_markdown(h)
    for _ in range(5):
        assert render_handoff_markdown(h) == first


# --- examples for success / failure / blocked ----------------------------------

def test_example_success_fields():
    h = example_handoff("success")
    assert h.outcome == "success"
    assert h.sender == "worker" and h.recipient == "manager"
    assert h.changed_paths and h.tests and h.artifacts
    assert h.decision_requested


def test_example_failure_fields():
    h = example_handoff("failure")
    assert h.outcome == "failure"
    assert h.decision_requested


def test_example_blocked_fields():
    h = example_handoff("blocked")
    assert h.outcome == "blocked"
    assert h.decision_requested
    # A blocked handoff carries no fabricated paths/tests/artifacts.
    assert h.changed_paths == [] and h.tests == [] and h.artifacts == []


def test_example_all_outcomes_render_to_both_forms():
    for outcome in OUTCOMES:
        h = example_handoff(outcome)
        assert render_handoff_markdown(h)
        assert json.loads(render_handoff_json(h))["outcome"] == outcome


def test_example_unknown_outcome_raises():
    with pytest.raises(ValueError, match="unknown outcome"):
        example_handoff("maybe")


def test_allowed_handoffs_and_outcomes_are_frozen_docs():
    assert ("worker", "manager") in ALLOWED_HANDOFFS
    assert ("manager", "tester") in ALLOWED_HANDOFFS
    assert set(OUTCOMES) == {"success", "failure", "blocked"}