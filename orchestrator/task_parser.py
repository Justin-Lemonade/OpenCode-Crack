"""Parse the roadmap and its delegated-task extension files.

`iteration improvement ideas.md` remains the primary roadmap. The
`delegated_tasks/` directory is a mechanically included extension for
bounded D-tier tasks created after the main roadmap was established; it
uses the same task-header/priority format and is parsed into the same board.
"""
import re
from dataclasses import dataclass
from pathlib import Path

ROADMAP_PATH = Path("docs/iteration improvement ideas.md")
DELEGATED_TASKS_DIR = Path("delegated_tasks")

HEADER_RE = re.compile(r"^##\s+([HDC]-\d{3})\s+—\s+(.+?)\s*$", re.MULTILINE)
PRIORITY_RE = re.compile(r"\*\*Priority:\s*([A-Z0-9\- ]+?)(?:\.|—|\*\*)")
ORIGIN_LINE_RE = re.compile(r"^\s*\*\*Origin:", re.MULTILINE)
OUT_OF_SCOPE_LINE_RE = re.compile(r"^\s*\*\*Out\s+of\s+scope:", re.MULTILINE)

# The documented priority vocabulary (matches status_report's ordering dict).
# "MEDIUM-LOW" is a variant used by live roadmap tasks (H-027, H-028, D-022,
# D-023) and is retained so releasing/parsing never silently downgrades real
# board entries; anything else the regex captures is malformed and must fall
# back to the explicit "UNSPECIFIED" sentinel instead of being returned as if
# it were a real priority.
VALID_PRIORITIES = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW-MEDIUM", "LOW", "MEDIUM-LOW"}
)

TIER_NAMES = {
    "H": "human",
    "D": "delegate",
    "C": "primary_claude",
}


@dataclass(frozen=True)
class RoadmapTask:
    id: str
    tier: str
    title: str
    priority: str


def _documents(path: "Path | None" = None) -> list[Path]:
    """Return roadmap documents in deterministic order.

    A custom path is treated as a single fixture/document for tests. The
    default roadmap additionally loads every Markdown file under
    `delegated_tasks/`, which keeps the main roadmap readable while making
    those tasks first-class board entries.
    """
    if path is not None:
        return [path]
    extensions = sorted(DELEGATED_TASKS_DIR.glob("*.md")) if DELEGATED_TASKS_DIR.exists() else []
    return [ROADMAP_PATH, *extensions]


def _parse_document(path: Path) -> list[RoadmapTask]:
    text = path.read_text(encoding="utf-8")
    headers = list(HEADER_RE.finditer(text))
    tasks: list[RoadmapTask] = []
    for i, match in enumerate(headers):
        task_id, title = match.group(1), match.group(2)
        section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[match.end():section_end]
        priority_match = PRIORITY_RE.search(section)
        priority = priority_match.group(1).strip() if priority_match else "UNSPECIFIED"
        if priority not in VALID_PRIORITIES:
            # Genuinely malformed (e.g. "HIGHX" or "MEDIUM IMPORTANT"): fall
            # back explicitly rather than silently returning the garbage the
            # regex happened to capture as if it were a real priority.
            priority = "UNSPECIFIED"
        tasks.append(RoadmapTask(task_id, TIER_NAMES[task_id[0]], title, priority))
    return tasks


def parse_roadmap(path: "Path | None" = None) -> list[RoadmapTask]:
    """Read all applicable roadmap documents in deterministic order.

    Raises ValueError if two tasks share the same H/D/C task ID, so a
    duplicate never silently becomes two board entries under one key.
    """
    tasks = [task for document in _documents(path) for task in _parse_document(document)]
    _validate_unique_ids(tasks)
    return tasks


def _validate_unique_ids(tasks: list[RoadmapTask]) -> None:
    """Explicitly reject duplicate task IDs instead of letting them
    produce two board entries with the same key."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for task in tasks:
        if task.id in seen:
            duplicates.add(task.id)
        seen.add(task.id)
    if duplicates:
        listed = ", ".join(sorted(duplicates))
        raise ValueError(f"Duplicate task ID(s) in roadmap: {listed}")


def _delegated_documents(path: "Path | None" = None) -> list[Path]:
    """Return the delegated-task documents to scan, honoring an explicit
    path (single fixture file) or the extension directory."""
    if path is not None:
        return [path]
    if DELEGATED_TASKS_DIR.exists():
        return sorted(DELEGATED_TASKS_DIR.glob("*.md"))
    return []


def _missing_in_delegated(pattern: "re.Pattern", path: "Path | None" = None) -> list[str]:
    """Shared scan: return `TASKID (filename)` for every D-task in the
    delegated extension files whose section lacks a line matching `pattern`."""
    offenders: list[str] = []
    for document in _delegated_documents(path):
        text = document.read_text(encoding="utf-8")
        headers = list(HEADER_RE.finditer(text))
        for i, match in enumerate(headers):
            if not match.group(1).startswith("D-"):
                continue
            section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            section = text[match.end():section_end]
            if not pattern.search(section):
                offenders.append(f"{match.group(1)} ({document.name})")
    return offenders


def delegated_tasks_missing_origin(path: "Path | None" = None) -> list[str]:
    """Return labels for every D-task whose section has no `**Origin:**` line.

    Scans the delegated-task extension files only (those are the "new"
    D-tasks added via the task-breakdown process, where an Origin line is
    required); legacy tasks in the main roadmap predate the requirement and
    are intentionally out of scope. Each returned label names the exact
    task ID and the file it lives in so a violation is actionable. Returns
    [] when every delegated task carries an Origin line.
    """
    return _missing_in_delegated(ORIGIN_LINE_RE, path)


def delegated_tasks_missing_out_of_scope(path: "Path | None" = None) -> list[str]:
    """Return labels for every D-task whose section has no explicit
    `**Out of scope:**` boundary.

    Same scan and reporting shape as delegated_tasks_missing_origin(): a
    delegated brief without a stated scope boundary is too vague to
    delegate, so this flags it with the exact task ID and file. Returns []
    when every delegated task declares one.
    """
    return _missing_in_delegated(OUT_OF_SCOPE_LINE_RE, path)


def get_section_text(task_id: str, path: "Path | None" = None) -> str:
    """Return the complete Markdown section for a parsed task."""
    for document in _documents(path):
        text = document.read_text(encoding="utf-8")
        headers = list(HEADER_RE.finditer(text))
        for i, match in enumerate(headers):
            if match.group(1) == task_id:
                section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
                return text[match.start():section_end].strip()
    raise ValueError(f"No such task in roadmap: {task_id}")


if __name__ == "__main__":
    for t in parse_roadmap():
        print(f"{t.id:8} [{t.tier:14}] ({t.priority:12}) {t.title}")
