"""Single source of truth for where per-task reports live.

Task reports (`<TASK_ID>_report.md`, `<TASK_ID>_report.json`, and extras such as
`<TASK_ID>_followup_report.md`) live in `reports/tasks/`. Before the 2026-10 repo
sort they sat flat in `reports/`; the resolver below still finds those legacy
copies so older checkouts, branches and in-flight agents keep working.

`reports/` itself keeps only `README.md`, `activity.log` and topic folders.
"""
from __future__ import annotations

from pathlib import Path

REPORTS_DIR = Path("reports")
TASK_REPORTS_DIR = REPORTS_DIR / "tasks"


def task_report_path(task_id: str, ext: str = "md", reports_dir: Path | None = None) -> Path:
    """Path of a task's report.

    An explicit `reports_dir` is honoured as-is (tests and callers that manage their own
    folder). Otherwise the new `reports/tasks/` location wins; the legacy flat location
    is returned only when the file exists there and not in the new place.
    """
    name = f"{task_id}_report.{ext}"
    if reports_dir is not None:
        return Path(reports_dir) / name
    new, legacy = TASK_REPORTS_DIR / name, REPORTS_DIR / name
    return legacy if (legacy.exists() and not new.exists()) else new
