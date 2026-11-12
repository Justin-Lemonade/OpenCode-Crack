"""Read-only, evidence-aware diagnostics for task selection.

"Open delegate" is only a board-level candidate, not proof that a task is
safe or feasible to claim. The final environment/scope check belongs to the
agent in find_work.md, after inspecting the current task contract.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable

from opencode_crack.orchestrator.task_board import Task
from opencode_crack.orchestrator.task_parser import get_section_text

_DEPENDENCIES = re.compile(r"(?im)^\s*\*\*Depends[- ]on:\*\*\s*([^\n]+)")
_TASK_ID = re.compile(r"\b[HDC]-\d{3}\b")
_HUMAN_GATE = re.compile(r"\bHUMAN[- ]GATE\b", re.IGNORECASE)
_REVIEW_NOTE = re.compile(
    r"\b(?:superseded by|duplicate of|already completed|no longer actionable)\b",
    re.IGNORECASE,
)
_REJECT_FEEDBACK = re.compile(
    r"(?im)^\s*(?:\[[^\]\r\n]+\]\s*)?REJECT(?:ED)?\s*[:.!—-]"
)


def has_explicit_rejection_feedback(notes: str | None) -> bool:
    """Return True only when notes contain the explicit review rejection marker."""
    return isinstance(notes, str) and bool(_REJECT_FEEDBACK.search(notes))
_PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM-HIGH": 2, "MEDIUM": 3,
                   "LOW-MEDIUM": 4, "MEDIUM-LOW": 4, "LOW": 5}


def explain_eligibility(
    tasks: list[Task],
    *,
    section_loader: Callable[[str], str] = get_section_text,
) -> list[dict]:
    """Explain board exclusions and statically declared gates, without side effects.

    Only inspect specification text for open D-tier candidates. The result
    deliberately never claims that runtime suitability or supersession checks
    have been performed. Unknown means unknown, not eligible.
    """
    lookup = {task.id: task for task in tasks}
    id_counts = Counter(task.id for task in tasks)
    duplicate_ids = {task_id for task_id, count in id_counts.items() if count > 1}
    output = []
    ordered = sorted(tasks, key=lambda t: (
        0 if t.tier == "delegate" else 1,
        _PRIORITY_ORDER.get(t.priority, 9), t.id,
    ))
    for task in ordered:
        blockers = []
        warnings = []
        if task.tier != "delegate":
            blockers.append("tier_restricted:" + task.tier)
        if task.id in duplicate_ids:
            blockers.append("duplicate_task_id")
        if task.state.status != "open":
            blockers.append("status_not_open:" + task.state.status)
        if task.tier == "delegate" and task.state.status == "open":
            if _HUMAN_GATE.search(task.title):
                blockers.append("explicit_human_gate")
            try:
                section = section_loader(task.id)
            except (ValueError, OSError):
                blockers.append("missing_task_spec")
            else:
                if _HUMAN_GATE.search(section) and "explicit_human_gate" not in blockers:
                    blockers.append("explicit_human_gate")
                match = _DEPENDENCIES.search(section)
                if match:
                    for dep_id in sorted(set(_TASK_ID.findall(match.group(1)))):
                        dep = lookup.get(dep_id)
                        if dep is None:
                            blockers.append("unknown_dependency:" + dep_id)
                        elif dep.state.status != "done":
                            blockers.append("dependency_not_done:" + dep_id + ":" + dep.state.status)
            if task.priority == "UNSPECIFIED":
                warnings.append("priority_unspecified")
            notes = task.state.notes or ""
            if _REJECT_FEEDBACK.search(notes):
                warnings.append("rework_feedback_present")
            if _REVIEW_NOTE.search(notes):
                warnings.append("possible_duplicate_or_supersession_note")
        preflight_required = not blockers
        output.append({
            "task_id": task.id,
            "title": task.title,
            "tier": task.tier,
            "priority": task.priority,
            "status": task.state.status,
            "board_candidate": task.tier == "delegate" and task.state.status == "open",
            "static_blockers": blockers,
            "warnings": warnings,
            "selection_state": "blocked" if blockers else "requires_preflight",
            "remaining_checks": (
                ["runtime_and_resource_availability", "supersession_and_scope",
                 "fresh_claim_state_and_ownership"] if preflight_required else []
            ),
        })
    return output


def eligibility_report(tasks: list[Task], *, section_loader=get_section_text) -> dict:
    rows = explain_eligibility(tasks, section_loader=section_loader)
    candidates = [r for r in rows if r["board_candidate"]]
    return {
        "disclaimer": "Static board/spec checks only; requires_preflight is NOT permission to claim.",
        "counts": {
            "tasks": len(rows),
            "board_open_delegate": len(candidates),
            "static_blocked_open_delegate": sum(bool(r["static_blockers"]) for r in candidates),
            "requires_preflight_open_delegate": sum(
                r["selection_state"] == "requires_preflight" for r in candidates
            ),
        },
        "tasks": rows,
    }


def eligibility_report_json(tasks: list[Task], *, section_loader=get_section_text) -> str:
    return json.dumps(eligibility_report(tasks, section_loader=section_loader),
                      indent=2, sort_keys=True)
