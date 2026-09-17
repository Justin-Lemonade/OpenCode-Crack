"""
Deterministic fixture builders for the four manager-loop lifecycle cases
(roadmap D-147, broken out of C-074).

Mirrors the `tests/swarm_fixtures.py` / `tests/ingest_fixtures.py` pattern:
fixtures are generated at test time, byte-for-byte reproducible, and carry
explicit task ID, agent ID, session ID, status, and evidence fields:

    build_manager_assignment()   manager hands the task to a worker
    build_worker_completion()    worker reports the task done, with tests
    build_reviewer_rejection()   reviewer rejects, with a reason
    build_successful_approval()  reviewer approves, with an artifact

The values are illustrative (not real repo state), same as the handoff
examples in src/runtime/handoff.py.
"""
from __future__ import annotations

from opencode_crack.runtime.lifecycle_record import EvidenceItem, LifecycleRecord


def build_manager_assignment(task_id: str = "D-147") -> LifecycleRecord:
    """Case 1: manager assigns the task to a worker."""
    return LifecycleRecord(
        task_id=task_id,
        agent_id="worker",
        session_id="ses-manager-1",
        status="assigned",
        evidence=[
            EvidenceItem("task", task_id),
            EvidenceItem("instructions", "Implement per contract; report to manager"),
        ],
        notes="Assignment sent through the swarm config.",
    )


def build_worker_completion(task_id: str = "D-147") -> LifecycleRecord:
    """Case 2: worker completes the task, with test evidence."""
    return LifecycleRecord(
        task_id=task_id,
        agent_id="worker",
        session_id="ses-worker-1",
        status="completed",
        evidence=[
            EvidenceItem("path", "src/runtime/lifecycle_record.py"),
            EvidenceItem("path", "tests/test_lifecycle_record.py"),
            EvidenceItem("test", "python -m pytest tests/test_lifecycle_record.py"),
        ],
        notes="All targeted tests pass.",
    )


def build_reviewer_rejection(task_id: str = "D-147") -> LifecycleRecord:
    """Case 3: reviewer rejects the submitted work, with a reason."""
    return LifecycleRecord(
        task_id=task_id,
        agent_id="reviewer",
        session_id="ses-review-1",
        status="rejected",
        evidence=[
            EvidenceItem("reason", "Evidence does not reference the full diff"),
            EvidenceItem("path", "reports/D-147_report.md"),
        ],
    )


def build_successful_approval(task_id: str = "D-147") -> LifecycleRecord:
    """Case 4: reviewer approves the work, with the approved artifact."""
    return LifecycleRecord(
        task_id=task_id,
        agent_id="reviewer",
        session_id="ses-review-1",
        status="approved",
        evidence=[
            EvidenceItem("artifact", "reports/D-147_report.md"),
            EvidenceItem("commit", "abc1234"),
        ],
        notes="Approved on review.",
    )