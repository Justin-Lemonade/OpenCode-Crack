"""Deterministic integrity check for the roadmap documents (roadmap D-094).

Scans the main roadmap plus the delegated-task extension files and reports,
with the exact task ID and file, every violation of four invariants:

1. every parsed task ID is unique (a duplicate must never silently become
   two board entries under one key),
2. every task header uses a recognized tier prefix (H/D/C),
3. every task has an explicit priority,
4. every declared D-task batch (a file named D-0XX-0YY.md) contains every
   task ID in its declared range.

Read-only and deterministic: it never mutates the roadmap, the board, or git,
and the same inputs always yield the same findings (documents and results are
processed in sorted order). It reports violations instead of raising, so the
caller decides how to act — this is a check, not a gate.
"""
import re
from pathlib import Path

ROADMAP_PATH = Path("docs/iteration improvement ideas.md")
DELEGATED_TASKS_DIR = Path("delegated_tasks")

TIER_PREFIXES = ("H", "D", "C")

# Broader than task_parser.HEADER_RE on purpose: an unrecognized tier prefix
# (e.g. "E-001") must be caught and reported, not silently ignored. The
# em-dash task-title separator is still required so non-task headings (plain
# "## Some section") never count as tasks.
_HEADER_RE = re.compile(r"^##\s+([A-Z]+-\d+)\s+—\s+(.+?)\s*$", re.MULTILINE)
_PRIORITY_RE = re.compile(r"\*\*Priority:\s*([A-Z0-9\- ]+?)(?:\.|—|\*\*)")
# Declared batch file: "D-095-099.md" declares tasks D-095 through D-099.
_BATCH_FILENAME_RE = re.compile(r"^D-(\d+)-(\d+)\.md$")


def _documents(path: "Path | str | None" = None) -> list[Path]:
    """Return roadmap documents in deterministic order.

    A custom path is treated as a single fixture/document for tests. The
    default scans the main roadmap plus every Markdown file under
    `delegated_tasks/`, sorted by name.
    """
    if path is not None:
        return [Path(path)]
    extensions = sorted(DELEGATED_TASKS_DIR.glob("*.md")) if DELEGATED_TASKS_DIR.exists() else []
    return [ROADMAP_PATH, *extensions]


def check_roadmap(path: "Path | str | None" = None) -> list[str]:
    """Return every roadmap-integrity violation as a message string.

    Each message names the exact offending task ID and the file it was found
    in so a violation is actionable. Returns [] when the roadmap is clean.
    Findings are deterministic: per-task checks are emitted in document order
    (main roadmap first, then delegated files sorted by name), and duplicate
    reports are grouped and sorted by task ID.
    """
    violations: list[str] = []
    occurrences: dict[str, list[str]] = {}  # task ID -> basenames it appears in

    for document in _documents(path):
        text = document.read_text(encoding="utf-8")
        matches = list(_HEADER_RE.finditer(text))
        for i, match in enumerate(matches):
            task_id = match.group(1)
            occurrences.setdefault(task_id, []).append(document.name)

            prefix = task_id.split("-", 1)[0]
            if prefix not in TIER_PREFIXES:
                violations.append(
                    f"{task_id} ({document.name}): unrecognized tier prefix '{prefix}'"
                )

            section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[match.end():section_end]
            if not _PRIORITY_RE.search(section):
                violations.append(f"{task_id} ({document.name}): missing explicit priority")

    for task_id in sorted(occurrences):
        seen = occurrences[task_id]
        if len(seen) > 1:
            if len(set(seen)) == 1:
                violations.append(
                    f"duplicate task ID {task_id} ({seen[0]}): appears {len(seen)} times"
                )
            else:
                locations = ", ".join(sorted(set(seen)))
                violations.append(f"duplicate task ID {task_id}: {locations}")

    return violations


def _delegated_files(delegated_dir: "Path | str | None" = None) -> list[Path]:
    """Return the delegated-task extension files, sorted by name."""
    directory = Path(delegated_dir) if delegated_dir is not None else DELEGATED_TASKS_DIR
    if not directory.exists():
        return []
    return sorted(directory.glob("*.md"))


def check_delegated_batch_contiguity(delegated_dir: "Path | str | None" = None) -> list[str]:
    """Report every task ID missing from a declared D-task batch.

    A file named ``D-0XX-0YY.md`` declares that its contents must cover
    every task ID from ``D-0XX`` through ``D-0YY`` inclusive. This check
    verifies that claim against the task headers actually present in the
    file, without requiring the global task namespace to be contiguous —
    only the IDs *inside* a declared batch are checked (D-124).

    Returns [] when every declared batch is complete.
    """
    violations: list[str] = []

    for document in _delegated_files(delegated_dir):
        match = _BATCH_FILENAME_RE.match(document.name)
        if match is None:
            continue
        start = int(match.group(1))
        end = int(match.group(2))

        text = document.read_text(encoding="utf-8")
        present = {m.group(1) for m in _HEADER_RE.finditer(text)}

        for task_id in range(start, end + 1):
            expected = f"D-{task_id:03d}"
            if expected not in present:
                violations.append(
                    f"{expected} ({document.name}): missing from declared batch D-{start:03d}-D-{end:03d}"
                )

    return violations


def check_roadmap(
    path: "Path | str | None" = None,
    delegated_dir: "Path | str | None" = None,
) -> list[str]:
    """Return every roadmap-integrity violation as a message string.

    Each message names the exact offending task ID and the file it was found
    in so a violation is actionable. Returns [] when the roadmap is clean.
    Findings are deterministic: per-task checks are emitted in document order
    (main roadmap first, then delegated files sorted by name), and duplicate
    reports are grouped and sorted by task ID. With ``path`` given, only that
    document is scanned (fixture/test mode); otherwise the main roadmap plus
    every file under ``delegated_dir`` (default ``delegated_tasks/``) is
    scanned, and declared-batch contiguity (D-124) is checked too.
    """
    violations: list[str] = []
    occurrences: dict[str, list[str]] = {}  # task ID -> basenames it appears in

    for document in _documents(path):
        text = document.read_text(encoding="utf-8")
        matches = list(_HEADER_RE.finditer(text))
        for i, match in enumerate(matches):
            task_id = match.group(1)
            occurrences.setdefault(task_id, []).append(document.name)

            prefix = task_id.split("-", 1)[0]
            if prefix not in TIER_PREFIXES:
                violations.append(
                    f"{task_id} ({document.name}): unrecognized tier prefix '{prefix}'"
                )

            section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[match.end():section_end]
            if not _PRIORITY_RE.search(section):
                violations.append(f"{task_id} ({document.name}): missing explicit priority")

    for task_id in sorted(occurrences):
        seen = occurrences[task_id]
        if len(seen) > 1:
            if len(set(seen)) == 1:
                violations.append(
                    f"duplicate task ID {task_id} ({seen[0]}): appears {len(seen)} times"
                )
            else:
                locations = ", ".join(sorted(set(seen)))
                violations.append(f"duplicate task ID {task_id}: {locations}")

    if path is None:
        violations.extend(check_delegated_batch_contiguity(delegated_dir))

    return violations


if __name__ == "__main__":
    findings = check_roadmap()
    if findings:
        for line in findings:
            print(line)
        raise SystemExit(1)
    print("Roadmap integrity check passed.")
