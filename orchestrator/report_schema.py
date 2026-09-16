"""Machine-readable compact agent report schema (roadmap D-153).

Companion to the human-readable `reports/<TASK_ID>_report.md`: a bounded
JSON file (`reports/<TASK_ID>_report.json`) carrying the execution-critical
facts reviewers and manager agents need without parsing Markdown prose.

The schema is deliberately strict:

- Required fields (task ID, outcome, files changed, tests, failures,
  blockers, confidence, source, git state) must all be present;
- Free-text fields are bounded — a field or list item longer than the cap
  makes the report invalid rather than being silently accepted;
- The git-state vocabulary is shared with report_validator's canonical
  phrasings (D-086) so the JSON and Markdown forms never disagree.

Validation is deterministic and read-only; writing refuses invalid reports
instead of emitting garbage. It is NOT part of task approval — the review
gate stays with Primary Claude/human.
"""
import json
from pathlib import Path

from src.orchestrator.report_validator import GIT_STATE_PATTERNS

# Canonical vocabulary, in the order agents see them. Reusing
# report_validator.GIT_STATE_PATTERNS keys keeps one git-state vocabulary
# for both the Markdown wording check (D-086) and the JSON schema.
OUTCOMES = ("submitted", "blocked", "released")
CONFIDENCE_LEVELS = ("high", "medium", "low")
SOURCES = ("tests", "inspection", "manual")
GIT_STATES = tuple(GIT_STATE_PATTERNS)

REQUIRED_FIELDS = [
    "task_id",
    "outcome",
    "files_changed",
    "tests",
    "failures",
    "blockers",
    "confidence",
    "source",
    "git_state",
]

# Size bounds: free-text lists (files/tests/failures/blockers) and the
# total document. Nothing here is a judgement of quality — just a hard cap
# so an agent cannot accidentally emit a megabyte of prose as "structured"
# status and defeat the compactness goal.
MAX_LIST_ITEMS = 200
MAX_LIST_ITEM_LENGTH = 2000
MAX_TOTAL_BYTES = 200_000

# Fields that hold free text and are therefore subject to the item cap.
_LIST_FIELDS = ("files_changed", "tests", "failures", "blockers")

# Default report filename for a task, mirroring the Markdown naming
# convention (`D-120_report.md` -> `D-120_report.json`).
def report_json_path(task_id: str, reports_dir: "Path" = Path("reports")) -> Path:
    return reports_dir / f"{task_id}_report.json"


def build_report(
    task_id: str,
    outcome: str = "submitted",
    files_changed: "list[str] | None" = None,
    tests: "list[str] | None" = None,
    failures: "list[str] | None" = None,
    blockers: "list[str] | None" = None,
    confidence: str = "medium",
    source: str = "inspection",
    git_state: str = "committed locally, not pushed",
) -> dict:
    """Assemble a report dict matching the schema with sane defaults."""
    return {
        "task_id": task_id,
        "outcome": outcome,
        "files_changed": list(files_changed or []),
        "tests": list(tests or []),
        "failures": list(failures or []),
        "blockers": list(blockers or []),
        "confidence": confidence,
        "source": source,
        "git_state": git_state,
    }


def validate_report(data: dict) -> list[str]:
    """Return a deterministic list of problems with a report dict.

    Empty result means the report is schema-valid. Checks, in order:
    required fields present, enum membership, list shape, size bounds,
    and total serialized size. Read-only — never rewrites the report.
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        return ["report must be a JSON object"]

    for field in REQUIRED_FIELDS:
        if field not in data:
            problems.append(f"missing required field: {field}")

    if data.get("task_id") not in ("", None) and not _is_task_id(data["task_id"]):
        problems.append(f"task_id must look like D-120: {data['task_id']!r}")
    if data.get("outcome") not in OUTCOMES:
        problems.append(f"outcome must be one of {', '.join(OUTCOMES)}: {data.get('outcome')!r}")
    if data.get("confidence") not in CONFIDENCE_LEVELS:
        problems.append(
            f"confidence must be one of {', '.join(CONFIDENCE_LEVELS)}: {data.get('confidence')!r}"
        )
    if data.get("source") not in SOURCES:
        problems.append(f"source must be one of {', '.join(SOURCES)}: {data.get('source')!r}")
    if data.get("git_state") not in GIT_STATES:
        problems.append(
            f"git_state must be one of {', '.join(GIT_STATES)}: {data.get('git_state')!r}"
        )

    for field in _LIST_FIELDS:
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            problems.append(f"{field} must be a list, got {type(value).__name__}")
            continue
        if len(value) > MAX_LIST_ITEMS:
            problems.append(f"{field} exceeds {MAX_LIST_ITEMS} items ({len(value)})")
        for item in value:
            if not isinstance(item, str):
                problems.append(f"{field} items must be strings, got {type(item).__name__}")
            elif len(item) > MAX_LIST_ITEM_LENGTH:
                problems.append(f"{field} item exceeds {MAX_LIST_ITEM_LENGTH} chars")

    try:
        size = len(json.dumps(data, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        problems.append("report contains non-serializable values")
    else:
        if size > MAX_TOTAL_BYTES:
            problems.append(f"report exceeds {MAX_TOTAL_BYTES} bytes ({size})")

    return problems


def _is_task_id(value) -> bool:
    import re

    return re.fullmatch(r"[HDC]-\d{3}", str(value)) is not None


def write_report_json(task_id: str, report: dict, reports_dir: "Path" = Path("reports")) -> Path:
    """Validate and write `reports/<TASK_ID>_report.json`.

    Raises ValueError listing every problem if the report is invalid —
    invalid structured reports are rejected rather than written, so a
    malformed JSON companion never silently defeats the compactness goal.
    """
    problems = validate_report(report)
    if problems:
        raise ValueError(f"refusing to write invalid report:\n- " + "\n- ".join(problems))
    path = report_json_path(task_id, reports_dir)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def validate_report_file(report_path: "str | Path") -> list[str]:
    """Read a report JSON file and return its schema problems ([] = valid).

    Mirror of report_validator.validate_report(): deterministic and
    file-local, no task board, no git.
    """
    path = Path(report_path)
    if not path.exists():
        return [f"no such report file: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"unreadable report file: {exc}"]
    return validate_report(data)
