"""
Evidence-first handoff format for the worker -> manager -> tester pipeline
(roadmap D-157).

A handoff is the compact, structured message one role passes to the next.
It references artifacts and tests instead of repeating implementation
narrative: the reader (manager or tester) should be able to decide the
requested action from the fields alone, then open the referenced report or
run the referenced test command for detail. This deliberately mirrors the
report discipline from find_work.md — facts, paths, and git/test state —
narrowed to the supervision handoff case.

Pure and deterministic: no I/O, no DB, no agent runtime. The JSON and
Markdown renderers share one field source of truth (``handoff_fields``) so
the two forms never drift. Optional fields are omitted when empty, never
emitted as nulls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# The two supervised handoff directions this format supports. Reversing or
# inventing a new direction (e.g. tester -> worker) is out of scope.
ALLOWED_HANDOFFS = frozenset({("worker", "manager"), ("manager", "tester")})

# Every handoff ends with one requested outcome; this is what the reader
# must act on. Free-form text, not a fixed enum, because the requested
# decision depends on the task (approve, rework, test, verify, ...).
OUTCOMES = ("success", "failure", "blocked")


@dataclass(frozen=True)
class TestRun:
    """One test command plus its observed result, verbatim."""
    command: str
    result: str

    # Keep pytest from collecting this data class as a test (name starts
    # with "Test"); it's a value type, not a test suite.
    __test__ = False


@dataclass(frozen=True)
class Handoff:
    """Compact structured handoff between supervised agent roles.

    Fields map 1:1 to the evidence a manager/tester needs to act without
    re-reading the implementation narrative: what task, what changed,
    what was run, where the evidence lives, what risks remain, and the
    explicit decision requested. Optional list fields default to empty and
    are omitted from both render forms when empty.
    """
    task_id: str
    sender: str
    recipient: str
    outcome: str
    decision_requested: str
    changed_paths: list[str] = field(default_factory=list)
    tests: list[TestRun] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    known_risks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if (self.sender, self.recipient) not in ALLOWED_HANDOFFS:
            raise ValueError(
                f"sender/recipient must be one of {sorted(ALLOWED_HANDOFFS)}, "
                f"got ({self.sender!r}, {self.recipient!r})"
            )
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {self.outcome!r}")
        if not self.decision_requested or not self.decision_requested.strip():
            raise ValueError("decision_requested must not be empty")
        for path in self.changed_paths:
            if not path or not path.strip():
                raise ValueError("changed_paths entries must not be empty")
        for artifact in self.artifacts:
            if not artifact or not artifact.strip():
                raise ValueError("artifacts entries must not be empty")


def handoff_fields(h: Handoff) -> dict:
    """Structured handoff content shared by the JSON and Markdown forms."""
    return {
        "task_id": h.task_id,
        "sender": h.sender,
        "recipient": h.recipient,
        "outcome": h.outcome,
        "changed_paths": list(h.changed_paths),
        "tests": [{"command": t.command, "result": t.result} for t in h.tests],
        "artifacts": list(h.artifacts),
        "known_risks": list(h.known_risks),
        "decision_requested": h.decision_requested,
    }


def render_handoff_json(h: Handoff) -> str:
    """Machine-readable handoff form (same fields as the Markdown form)."""
    return json.dumps(handoff_fields(h), indent=2, sort_keys=True)


def render_handoff_markdown(h: Handoff) -> str:
    """Compact human-readable handoff form.

    Kept deliberately terse: one line per field, artifact paths and test
    commands verbatim, and no narrative. Empty optional sections are
    omitted so a handoff with no known risks doesn't grow a "none" line.
    """
    lines = [
        f"# Handoff {h.task_id} ({h.sender} -> {h.recipient})",
        f"Outcome: {h.outcome}",
        f"Decision requested: {h.decision_requested}",
    ]
    if h.changed_paths:
        lines.append("")
        lines.append("Changed paths:")
        lines += [f"- {p}" for p in h.changed_paths]
    if h.tests:
        lines.append("")
        lines.append("Tests:")
        lines += [f"- `{t.command}` — {t.result}" for t in h.tests]
    if h.artifacts:
        lines.append("")
        lines.append("Artifacts:")
        lines += [f"- {a}" for a in h.artifacts]
    if h.known_risks:
        lines.append("")
        lines.append("Known risks:")
        lines += [f"- {r}" for r in h.known_risks]
    return "\n".join(lines)


def example_handoff(outcome: str) -> Handoff:
    """Example handoffs for the success, failure, and blocked cases.

    These exist so the three documented shapes are concrete, renderable,
    and testable rather than prose in the roadmap. The paths/artifacts are
    illustrative, not real repo state.
    """
    base = dict(
        task_id="D-157",
        changed_paths=["src/runtime/handoff.py", "tests/test_handoff.py"],
        tests=[
            TestRun(
                command="python -m pytest tests/test_handoff.py",
                result="12 passed",
            ),
        ],
        artifacts=["reports/D-157_report.md"],
    )
    if outcome == "success":
        return Handoff(
            sender="worker", recipient="manager", outcome="success",
            decision_requested="Approve and forward to tester",
            known_risks=[], **base,
        )
    if outcome == "failure":
        return Handoff(
            sender="worker", recipient="manager", outcome="failure",
            decision_requested="Redirect: implementation did not pass its own tests",
            known_risks=["Handoff schema still under review"],
            **base,
        )
    if outcome == "blocked":
        return Handoff(
            sender="worker", recipient="manager", outcome="blocked",
            decision_requested="Block: dependency C-013 has not landed",
            changed_paths=[], tests=[], artifacts=[], known_risks=[],
            **{k: v for k, v in base.items() if k not in ("changed_paths", "tests", "artifacts")},
        )
    raise ValueError(f"unknown outcome for example: {outcome!r}")