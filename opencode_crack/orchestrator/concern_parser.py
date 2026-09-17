"""Parse `concerns/CN-*.md` files: the content layer for the concerns board.

Mirrors task_parser.py's split between content (this file — one file per
concern under `concerns/`) and state (`concerns_board.py` +
`orchestration/concerns.yaml`). A concern's title/severity/category/body
is the immutable record of what was reported and why; if the situation
evolves, that goes in a status transition's notes via concerns_board
(acknowledge/resolve/dismiss), not by editing the .md file after the fact.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONCERNS_DIR = Path("concerns")

HEADER_RE = re.compile(r"^##\s+(CN-\d{3})\s+—\s+(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^\*\*([A-Za-z ]+?):\*\*\s*(.+?)\s*$", re.MULTILINE)

VALID_SEVERITIES = frozenset({"BLOCKER", "HIGH", "MEDIUM", "LOW"})
VALID_CATEGORIES = frozenset(
    {"process", "board-integrity", "technical", "environment", "quality", "other"}
)


@dataclass(frozen=True)
class FiledConcern:
    id: str
    title: str
    severity: str
    category: str
    raised_by: str
    related_task: Optional[str]
    body: str


def _parse_document(path: Path) -> list[FiledConcern]:
    text = path.read_text(encoding="utf-8")
    headers = list(HEADER_RE.finditer(text))
    concerns = []
    for i, match in enumerate(headers):
        concern_id, title = match.group(1), match.group(2)
        section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[match.end():section_end]

        fields: dict[str, str] = {}
        last_field_end = 0
        for fmatch in FIELD_RE.finditer(section):
            fields[fmatch.group(1).strip().lower()] = fmatch.group(2).strip()
            last_field_end = max(last_field_end, fmatch.end())

        severity = fields.get("severity", "").upper()
        if severity not in VALID_SEVERITIES:
            # Explicit, documented fallback rather than silently dropping a
            # concern for a formatting slip in a bold-field line.
            severity = "MEDIUM"

        category = fields.get("category", "").lower()
        if category not in VALID_CATEGORIES:
            category = "other"

        raised_by = fields.get("raised by", "unknown")
        related_task = fields.get("related task") or None

        # Body = everything after the last recognized **Field:** line, so
        # free narrative text isn't accidentally swallowed by FIELD_RE.
        body = section[last_field_end:].strip()

        concerns.append(FiledConcern(
            id=concern_id, title=title, severity=severity, category=category,
            raised_by=raised_by, related_task=related_task, body=body,
        ))
    return concerns


def parse_concerns(directory: Optional[Path] = None) -> list[FiledConcern]:
    """Read every concerns/CN-*.md file in deterministic (sorted-by-path)
    order. Raises ValueError on a duplicate CN-### ID across files — same
    board-integrity guard task_parser.parse_roadmap() applies to H/D/C
    IDs, and for the same reason (this project has hit real duplicate-ID
    pain before; see docs/AGENTS.md)."""
    directory = directory or CONCERNS_DIR
    if not directory.exists():
        return []
    files = sorted(directory.glob("CN-*.md"))
    concerns = [c for f in files for c in _parse_document(f)]
    _validate_unique_ids(concerns)
    return concerns


def _validate_unique_ids(concerns: list[FiledConcern]) -> None:
    ids = [c.id for c in concerns]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"Duplicate concern ID(s): {dupes}")


def get_concern_section_text(concern_id: str, directory: Optional[Path] = None) -> str:
    """Return the raw, unparsed section text (header line + fields + body)
    for one concern — for callers that want the original write-up rather
    than just the parsed fields."""
    directory = directory or CONCERNS_DIR
    files = sorted(directory.glob("CN-*.md")) if directory.exists() else []
    for f in files:
        text = f.read_text(encoding="utf-8")
        headers = list(HEADER_RE.finditer(text))
        for i, match in enumerate(headers):
            if match.group(1) == concern_id:
                section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
                return text[match.start():section_end].strip()
    raise ValueError(f"No such concern: {concern_id}")
