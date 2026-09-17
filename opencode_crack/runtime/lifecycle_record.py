"""
Manager-loop lifecycle records (roadmap D-147, broken out of C-074).

Captures the four lifecycle cases a first manager -> worker loop must be
able to represent — manager assignment, worker completion, reviewer
rejection, and successful approval — as one stable, explicitly-typed record
so the pure formatting/serialization helpers have a single source of truth:

    LifecycleRecord:
        task_id     task being supervised
        agent_id    agent the record concerns (manager/worker/reviewer)
        session_id  runtime session the work ran in
        status      one of: assigned | completed | rejected | approved
        evidence    structured evidence backing the status
                    (paths, tests, artifacts, rejection reasons)
        notes       optional free-text line

Rules (mirroring the handoff discipline from D-157):

  * pure and deterministic — no I/O, no DB, no model calls;
  * JSON and Markdown renderers share one field source
    (``lifecycle_fields``) so the two forms never drift;
  * optional fields are omitted when empty, never emitted as nulls;
  * task/agent/session IDs and status are always explicit.

Out of scope (per the roadmap): task-board status transitions and live
orchestration — this module only formats records, it never transitions state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# The four lifecycle cases this record supports.
STATUSES = ("assigned", "completed", "rejected", "approved")


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence backing a lifecycle status.

    ``kind`` is a short label (path / test / artifact / reason); ``value``
    is the verbatim path, command, or sentence. Free-form by design — the
    evidence an approval needs differs from the evidence a rejection needs.
    """
    kind: str
    value: str

    __test__ = False  # value type, not a pytest test


@dataclass(frozen=True)
class LifecycleRecord:
    task_id: str
    agent_id: str
    session_id: str
    status: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.agent_id or not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {self.status!r}")
        for item in self.evidence:
            if not item.kind or not item.kind.strip():
                raise ValueError("evidence kinds must not be empty")
            if not item.value or not item.value.strip():
                raise ValueError("evidence values must not be empty")


def lifecycle_fields(record: LifecycleRecord) -> dict:
    """Structured record content shared by the JSON and Markdown forms."""
    return {
        "task_id": record.task_id,
        "agent_id": record.agent_id,
        "session_id": record.session_id,
        "status": record.status,
        "evidence": [{"kind": e.kind, "value": e.value} for e in record.evidence],
        **({"notes": record.notes} if record.notes else {}),
    }


def render_lifecycle_json(record: LifecycleRecord) -> str:
    """Machine-readable form (stable field order + sorted keys)."""
    return json.dumps(lifecycle_fields(record), indent=2, sort_keys=True)


def render_lifecycle_markdown(record: LifecycleRecord) -> str:
    """Compact human-readable form: one line per field, evidence verbatim.

    Empty evidence/notes are omitted so a rejection with only a reason line
    stays terse. IDs and status are always present."""
    lines = [
        f"# Lifecycle {record.task_id}",
        f"Agent: {record.agent_id}",
        f"Session: {record.session_id}",
        f"Status: {record.status}",
    ]
    if record.evidence:
        lines.append("")
        lines.append("Evidence:")
        lines += [f"- [{e.kind}] {e.value}" for e in record.evidence]
    if record.notes:
        lines.append("")
        lines.append(f"Notes: {record.notes}")
    return "\n".join(lines)
