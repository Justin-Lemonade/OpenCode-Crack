"""
Critic gate for smoke-test bypass (D-318).

Production plans require critic approval before coder dispatch
(PLAN_CRITIC_GATE_VIOLATION). Smoke-test plans (disposable repo
or explicit smoke-test marker) bypass the gate to allow D-226/D-305
validation without weakening production safety.

This module is the bounded, testable decision point. The external
opencode-swarm plugin enforces the gate; this repo decides *when*
to enforce it by checking the task marker before invoking the swarm.
"""
from __future__ import annotations

from typing import Any


SMOKE_MARKERS = {"smoke-test", "smoke_test", "smoke", "disposable"}


def is_smoke_test_task(task: Any) -> bool:
    """Return True if task should bypass critic_pre_plan gate.

    Checks (in order):
    1. task has attribute `tags` containing a smoke marker (case-insensitive)
    2. task title contains a smoke marker
    3. task id starts with SMOKE- or contains SMOKE
    4. explicit `is_smoke_test` flag in task dict
    """
    # Check explicit flag
    if isinstance(task, dict) and task.get("is_smoke_test"):
        return True
    if hasattr(task, "is_smoke_test") and getattr(task, "is_smoke_test"):
        return True

    # Check tags
    tags = []
    if isinstance(task, dict):
        tags = task.get("tags") or task.get("Tags") or []
    elif hasattr(task, "tags"):
        tags = getattr(task, "tags") or []
    elif hasattr(task, "metadata"):
        tags = getattr(task, "metadata", {}).get("tags", [])

    if tags:
        for t in tags if isinstance(tags, (list, tuple)) else [tags]:
            if str(t).strip().lower() in SMOKE_MARKERS:
                return True
            # also check if tag string contains marker
            if any(m in str(t).lower() for m in SMOKE_MARKERS):
                return True

    # Check title
    title = ""
    if isinstance(task, dict):
        title = task.get("title") or task.get("Title") or ""
    elif hasattr(task, "title"):
        title = getattr(task, "title") or ""
    if title and any(m in title.lower() for m in SMOKE_MARKERS):
        return True

    # Check id
    tid = ""
    if isinstance(task, dict):
        tid = task.get("id") or task.get("task_id") or ""
    elif hasattr(task, "id"):
        tid = getattr(task, "id") or ""
    if tid and ("smoke" in tid.lower()):
        return True

    return False


def should_enforce_critic_gate(task: Any) -> bool:
    """True if production gate should be enforced (i.e., not smoke-test)."""
    return not is_smoke_test_task(task)
