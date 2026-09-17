"""
Deterministic validator for delegated agent reports (roadmap D-085).

Checks that `reports/<TASK_ID>_report.md` contains the sections the
find-work protocol (step 7) requires: the five-point format (Summary /
Files changed / Tests run / Failures / Questions & blockers) plus the
Environment line added in protocol v3.

Read-only by design: it reports which required sections are missing and
never rewrites the report. It does not judge content quality, and it is
NOT part of task approval — approving/rejecting stays with the review
gate. The git-state wording check (D-086) lives here too: it only detects
whether a report states its git state in one of the three canonical
phrasings; it never runs git and never infers state from other prose.
"""
import re
from pathlib import Path
from typing import Optional

# Canonical section names, in the order the find-work protocol lists them.
REQUIRED_SECTIONS = [
    "Environment",
    "Summary",
    "Files changed",
    "Tests run",
    "Failures",
    "Questions/blockers",
]

# Repo docs spell this section two ways ("Questions & blockers" in
# reports/README.md vs "Questions/blockers" in the protocol) — accept both,
# plus "Questions and blockers".
_SECTION_ALIASES = {
    "Questions/blockers": [
        r"Questions\s*/\s*blockers",
        r"Questions\s*&\s*blockers",
        r"Questions\s+and\s+blockers",
    ],
}

# A section counts as present when a Markdown heading (one or more `#`) has
# an optional `N.` number prefix and an optional `**bold**` wrapper around
# its name, e.g. "## 2. Files changed" or "### 0. **Environment**".
_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(?:\d+\.\s*)?(?:\*\*)?(?P<name>.+?)(?:\*\*)?\s*$"
)


def missing_sections(report_text: str) -> list[str]:
    """Return the REQUIRED_SECTIONS absent from the given report text.

    Presence is decided by the section heading alone; content depth is not
    judged here (that's the reviewer's call, not a deterministic check).
    """
    present = set()
    for line in report_text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip().rstrip(":")
        for canonical in REQUIRED_SECTIONS:
            if _name_matches(name, canonical):
                present.add(canonical)
    return [section for section in REQUIRED_SECTIONS if section not in present]


def duplicate_required_sections(report_text: str) -> list[str]:
    """Return REQUIRED_SECTIONS whose heading occurs more than once.

    A required section duplicated must not let a malformed report look
    complete: the same heading appearing twice is a formatting defect even
    when every required section name is present at least once, so this
    check is deliberately separate from (and complementary to)
    missing_sections. Returns [] when no required heading is duplicated.
    """
    counts: dict[str, int] = {}
    for line in report_text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip().rstrip(":")
        for canonical in REQUIRED_SECTIONS:
            if _name_matches(name, canonical):
                counts[canonical] = counts.get(canonical, 0) + 1
    return [section for section in REQUIRED_SECTIONS if counts.get(section, 0) > 1]


def _name_matches(heading_name: str, canonical: str) -> bool:
    if canonical in _SECTION_ALIASES:
        return any(
            re.fullmatch(pattern, heading_name, re.IGNORECASE)
            for pattern in _SECTION_ALIASES[canonical]
        )
    return re.fullmatch(re.escape(canonical), heading_name, re.IGNORECASE) is not None


# A task ID looks like "D-120" / "H-001" / "C-003": one letter, hyphen,
# digits. Reports open with a heading carrying it ("# D-085 Report — ...",
# "# D-051 — ..."), so a deterministic check can tie a report to its task.
_TASK_ID_RE = re.compile(r"\b([A-Z]-\d+)\b")


def report_task_id(report_text: str) -> Optional[str]:
    """Return the task ID (e.g. "D-120") from the report's first heading.

    Scans only heading lines (same matching used for section detection) so
    a task ID mentioned in body prose never counts. Returns None when no
    heading contains a task-ID-shaped token.
    """
    for line in report_text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue
        found = _TASK_ID_RE.search(match.group("name"))
        if found:
            return found.group(1)
    return None


def task_id_matches_filename(report_text: str, expected_task_id: str) -> bool:
    """True if the task ID in the report header equals the expected ID.

    The expected ID is the one implied by the report's filename
    (`reports/D-120_report.md` -> "D-120"). Wording-only and
    deterministic: it never consults git or the task board.
    """
    return report_task_id(report_text) == expected_task_id


# Canonical git-state phrasings the find-work protocol (step 7) requires
# a report to state. Detection is wording-only: matching a phrase proves
# the agent SAID it, never that it is true (no git operations here).
GIT_STATE_PATTERNS = {
    "pushed in commit": re.compile(
        r"\bpushed\s+in\s+commit\s+[`]?[0-9a-fA-F]+[`]?", re.IGNORECASE
    ),
    # Requires BOTH canonical tokens within a bounded window so real-world
    # variants like "committed locally as `13d68a3` (...), **not pushed**"
    # count, while a bare "committed locally" (no "not pushed" anywhere)
    # stays ambiguous and does NOT count. The 200-char cap stops tokens in
    # far-apart sections from being joined.
    "committed locally, not pushed": re.compile(
        r"\bcommitted\s+locally\b.{0,200}?\bnot\s+pushed\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "working tree only, no commit": re.compile(
        r"\bworking\s+tree\s+only\b.{0,120}?\bno\s+commit\b",
        re.IGNORECASE | re.DOTALL,
    ),
}


def git_state_wording(report_text: str) -> list[str]:
    """Return which canonical git-state phrasings appear in the report
    text, in canonical order.

    An empty result means the report states no explicit git state — i.e.
    missing/ambiguous wording (a report claiming to have changed files
    should say pushed / committed-locally / working-tree-only). This only
    detects the wording; it never verifies the claimed state (no git
    operations) and never infers state from other prose, so "I pushed it"
    or "committed" on their own do NOT count.
    """
    return [name for name, pattern in GIT_STATE_PATTERNS.items() if pattern.search(report_text)]


def validate_report(report_path: str | Path) -> list[str]:
    """Convenience wrapper: read the report file and return its missing
    sections ([] means the required sections are all present)."""
    return missing_sections(Path(report_path).read_text(encoding="utf-8"))


def validate_report_id(report_path: str | Path) -> bool:
    """True if the task ID in the report header matches the one implied by
    its filename (`reports/D-120_report.md` -> "D-120").

    Deterministic and file-local: no git, no task board. A report whose
    filename doesn't carry a task ID in the expected position returns False
    (there is nothing to compare against).
    """
    path = Path(report_path)
    expected = path.stem.split("_report")[0] if path.stem.endswith("_report") else path.stem
    return task_id_matches_filename(path.read_text(encoding="utf-8"), expected)