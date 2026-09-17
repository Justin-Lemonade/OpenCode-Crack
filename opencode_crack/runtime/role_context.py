"""Role-specific compact context fixtures for delegated agents (D-155).

Each fixture describes what one role needs to know by default:
responsibilities, allowed evidence sources, escalation boundary, and
default output format. Shared system context is factored out so the
fixtures themselves contain no duplicated prose.
"""
from __future__ import annotations

_SHARED_CONTEXT = {
    "system": "multi-agent coordination system",
    "task_scope": "bounded task",
}

ROLE_CONTEXT_FIXTURES: dict[str, dict] = {
    "manager": {
        **_SHARED_CONTEXT,
        "role": "manager",
        "responsibilities": [
            "Coordinate bounded tasks",
            "Assign work to workers",
            "Merge reports and keep the loop moving",
        ],
        "allowed_evidence_sources": [
            "worker reports",
            "test results",
            "artifact diffs",
        ],
        "escalation_boundary": (
            "Do not redesign architecture or implement changes yourself; "
            "delegate to workers."
        ),
        "default_output_format": "concise instructions + evidence-based merge decisions",
    },
    "worker": {
        **_SHARED_CONTEXT,
        "role": "worker",
        "responsibilities": [
            "Implement one bounded task",
            "Report concrete files/tests/evidence",
            "Finish before starting the next task",
        ],
        "allowed_evidence_sources": [
            "task contract",
            "existing code",
            "test output",
        ],
        "escalation_boundary": (
            "Do not redesign architecture; "
            "ask manager before changing shared interfaces."
        ),
        "default_output_format": (
            "concrete file paths, test commands, "
            "and evidence of completion"
        ),
    },
    "tester": {
        **_SHARED_CONTEXT,
        "role": "tester",
        "responsibilities": [
            "Independently test/review work",
            "Reject unsupported completion claims",
            "Report evidence to the manager",
        ],
        "allowed_evidence_sources": [
            "test results",
            "code diffs",
            "task acceptance criteria",
        ],
        "escalation_boundary": (
            "Do not fix issues yourself; "
            "report findings to the manager with evidence."
        ),
        "default_output_format": "pass/fail verdict with evidence citations",
    },
    "monitor": {
        **_SHARED_CONTEXT,
        "role": "monitor",
        "responsibilities": [
            "Observe agent and system health",
            "Report anomalies without intervening",
        ],
        "allowed_evidence_sources": [
            "event logs",
            "lease status",
            "resource metrics",
        ],
        "escalation_boundary": (
            "Do not modify state or interfere with active tasks; "
            "report only."
        ),
        "default_output_format": "structured health report with anomaly details",
    },
}


def get_role_context(role: str) -> dict:
    """Return the compact context fixture for a role, or {} if unknown."""
    return dict(ROLE_CONTEXT_FIXTURES.get(role, {}))
