"""
Project self-understanding (C-083 / P8).

The roadmap's goal is that a new agent can answer, for any piece of
work: what happened, why, what failed before, what was decided, and
what lessons apply. Every individual source of that story already
exists and is solid on its own -- the board (`orchestration/tasks.yaml`
via `task_board.get_task()`), the original task spec
(`delegated_tasks/<id>.md`), the completion report
(`reports/<id>_report.md`), git commit history (which consistently
references task ids in commit messages), recorded decisions
(`src.memory.decisions`), and the knowledge board
(`knowledge_board.search_relevant()`). Nothing connected them for a
given task id before this module.

`explain()` returns a structured dict rather than free text on
purpose: a caller (a CLI command, a future briefing generator, or an
LLM-backed summarizer) decides how to present it. This module does not
format anything into prose, and does not call a model -- it is a pure
assembly/lookup step.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DELEGATED_TASKS_DIR = REPO_ROOT / "delegated_tasks"


def _read_task_spec(task_id: str) -> Optional[str]:
    """The original task specification, if one was ever filed as a
    standalone delegated_tasks/<id>.md (typically D-tier). C-tier tasks
    usually live only in the roadmap doc instead -- their absence here
    is normal, not an error."""
    path = DELEGATED_TASKS_DIR / f"{task_id}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_report(report_path: Optional[str]) -> Optional[str]:
    """The completion report, using the board's own report_path field
    when present rather than re-guessing the reports/<id>_report.md
    convention -- report_path is what orchestrate submit actually
    wrote, so it is the authoritative pointer."""
    if not report_path:
        return None
    path = REPO_ROOT / report_path
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _find_commits(task_id: str, *, limit: int = 20) -> list[dict[str, str]]:
    """Commits whose message references this task id, oldest first.
    Read-only (`git log`, never a mutating command) and tolerant of any
    git failure (a shallow clone, no .git present, git not installed):
    returns an empty list rather than raising, since a missing commit
    history should degrade this function's output, not break it."""
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--reverse", "--grep", task_id,
             "--pretty=format:%H\t%ad\t%s", "--date=iso-strict",
             f"-n{limit}"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "message": parts[2]})
    return commits


def _find_decisions(task_id: str) -> list[dict[str, Any]]:
    """Recorded decisions whose title mentions this task id.
    decisions.list_decisions() has no task-id filter of its own, so
    this filters client-side rather than adding one to that module
    (keeping decisions.py's own contract unchanged, per this task's own
    scope)."""
    from src.memory import decisions

    try:
        all_decisions = decisions.list_decisions()
    except Exception:
        return []
    return [d for d in all_decisions if task_id.lower() in d.get("title", "").lower()]


def _find_related_knowledge(task_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Knowledge board entries related to this task, via the existing
    search_relevant() used for automatic contract injection (C-75) --
    reused rather than reimplemented."""
    from opencode_crack.orchestrator import knowledge_board

    try:
        return knowledge_board.search_relevant(task_id, limit=limit)
    except Exception:
        return []


def explain(task_id: str) -> dict[str, Any]:
    """Assemble everything known about one task id across every source
    this project keeps. Missing pieces (no filed task spec, no report
    yet, no commits found, no decisions, no related knowledge) are
    normal for a task at an early stage and are represented as
    empty/None, not errors -- only a genuinely unknown task_id (no
    board entry at all) is treated as a real "not found" case.
    """
    from opencode_crack.orchestrator import task_board

    task = task_board.get_task(task_id)
    if task is None:
        return {
            "task_id": task_id,
            "found": False,
            "board": None,
            "task_spec": None,
            "report": None,
            "commits": [],
            "decisions": [],
            "related_knowledge": [],
        }

    return {
        "task_id": task_id,
        "found": True,
        "board": {
            "tier": task.tier,
            "title": task.title,
            "priority": task.priority,
            "status": task.state.status,
            "assignee": task.state.assignee,
            "notes": task.state.notes,
            "report_path": task.state.report_path,
        },
        "task_spec": _read_task_spec(task_id),
        "report": _read_report(task.state.report_path),
        "commits": _find_commits(task_id),
        "decisions": _find_decisions(task_id),
        "related_knowledge": _find_related_knowledge(task_id),
    }
