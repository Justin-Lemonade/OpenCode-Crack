"""Parse `knowledge/K-*.md` files: the content layer for the knowledge board.

Mirrors concern_parser.py's split between content (this file — one file
per knowledge entry under `knowledge/`) and state (`knowledge_board.py` +
`orchestration/knowledge.yaml`). A knowledge entry's title/status/
category/tags/body is the immutable record of what was reported; if the
situation evolves, that goes in a status transition's notes via
knowledge_board (verify/retract), not by editing the .md file after the
fact.

`Status` in the file header is informational only — `knowledge.yaml`,
not the file header, is authoritative for `status` (same split as
concerns where `concerns.yaml` is authoritative). It is parsed into the
dataclass field without validation.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

KNOWLEDGE_DIR = Path("knowledge")

HEADER_RE = re.compile(r"^##\s+(K-\d{3})\s+—\s+(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^\*\*([A-Za-z ]+?):\*\*\s*(.+?)\s*$", re.MULTILINE)

VALID_CATEGORIES = frozenset(
    {"environment", "technique", "gotcha", "architecture", "tooling", "process", "other"}
)


@dataclass(frozen=True)
class FiledKnowledge:
    id: str
    title: str
    status: str
    category: str
    tags: list[str] = field(default_factory=list)
    submitted_by: str = "unknown"
    related_task: Optional[str] = None
    supersedes: Optional[str] = None
    body: str = ""


def _parse_document(path: Path) -> list[FiledKnowledge]:
    text = path.read_text(encoding="utf-8")
    headers = list(HEADER_RE.finditer(text))
    entries = []
    for i, match in enumerate(headers):
        knowledge_id, title = match.group(1), match.group(2)
        section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[match.end():section_end]

        fields: dict[str, str] = {}
        last_field_end = 0
        for fmatch in FIELD_RE.finditer(section):
            fields[fmatch.group(1).strip().lower()] = fmatch.group(2).strip()
            last_field_end = max(last_field_end, fmatch.end())

        status = fields.get("status", "")

        category = fields.get("category", "").lower()
        if category not in VALID_CATEGORIES:
            # Explicit, documented fallback rather than silently dropping
            # an entry for a formatting slip in a bold-field line.
            category = "other"

        raw_tags = fields.get("tags", "")
        tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()] if raw_tags else []

        submitted_by = fields.get("submitted by", "unknown")
        related_task = fields.get("related task") or None
        supersedes = fields.get("supersedes") or None

        # Body = everything after the last recognized **Field:** line, so
        # free narrative text isn't accidentally swallowed by FIELD_RE.
        body = section[last_field_end:].strip()

        entries.append(FiledKnowledge(
            id=knowledge_id, title=title, status=status, category=category,
            tags=tags, submitted_by=submitted_by, related_task=related_task,
            supersedes=supersedes, body=body,
        ))
    return entries


def parse_knowledge(directory: Optional[Path] = None) -> list[FiledKnowledge]:
    """Read every knowledge/K-*.md file in deterministic (sorted-by-path)
    order. Raises ValueError on a duplicate K-### ID across files — same
    board-integrity guard task_parser.parse_roadmap() applies to H/D/C
    IDs, and for the same reason (this project has hit real duplicate-ID
    pain before; see docs/AGENTS.md)."""
    directory = directory or KNOWLEDGE_DIR
    if not directory.exists():
        return []
    files = sorted(directory.glob("K-*.md"))
    entries = [e for f in files for e in _parse_document(f)]
    _validate_unique_ids(entries)
    return entries


def _validate_unique_ids(entries: list[FiledKnowledge]) -> None:
    ids = [e.id for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"Duplicate knowledge ID(s): {dupes}")


def get_knowledge_section_text(knowledge_id: str, directory: Optional[Path] = None) -> str:
    """Return the raw, unparsed section text (header line + fields + body)
    for one knowledge entry — for callers that want the original write-up
    rather than just the parsed fields."""
    directory = directory or KNOWLEDGE_DIR
    files = sorted(directory.glob("K-*.md")) if directory.exists() else []
    for f in files:
        text = f.read_text(encoding="utf-8")
        headers = list(HEADER_RE.finditer(text))
        for i, match in enumerate(headers):
            if match.group(1) == knowledge_id:
                section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
                return text[match.start():section_end].strip()
    raise ValueError(f"No such knowledge entry: {knowledge_id}")
