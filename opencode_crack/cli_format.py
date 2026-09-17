"""Shared human-readable output helpers for CLI commands (roadmap D-186).

Before this module every command rolled its own formatting: ad-hoc table
headers, raw ``Path`` objects printed with Windows backslashes, inline
``"(s)"`` pluralization, and scattered ``log.error(...)`` + ``sys.exit(1)``
pairs. These helpers give every command one vocabulary so the terminal
output stays consistent, readable, and useful to a person:

- :func:`heading` — stable section headings across all commands;
- :func:`display_path` — paths render with forward slashes on every OS,
  so Windows output is readable and copy-pasteable like Unix output;
- :func:`count_str` — real singular/plural forms instead of ``file(s)``;
- :func:`status_line` — the canonical ``name: OK - detail`` check line
  (the format `brain status` has always printed, now defined once);
- :func:`error_and_exit` — one exit semantics: errors go to the log,
  exit code is explicit, usage errors always exit 1.

JSON output modes are intentionally untouched: helpers here are for the
human-readable channel only. Commands keep printing machine-readable
JSON via ``json.dumps`` exactly as before.
"""
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

HEADING_MARK = "== "


def heading(title: str) -> str:
    """Render a stable section heading, e.g. ``== Folder intake ==``.

    Every command that prints a block of results starts it with one of
    these, so scripts and eyes can find section boundaries reliably."""
    return f"{HEADING_MARK}{title} =="


def display_path(path) -> str:
    """Format a path for human output on any OS.

    Forward slashes everywhere: ``data/intake/notes.txt`` instead of
    ``data\\intake\\notes.txt`` on Windows. Counts stay counts; this only
    changes how paths *read*."""
    return Path(path).as_posix()


def count_str(n: int, singular: str, plural: str | None = None) -> str:
    """Render a count with a real plural, e.g. ``1 file`` / ``3 files``."""
    if plural is None:
        plural = singular + "s"
    return f"{n} {singular if n == 1 else plural}"


def status_line(name: str, ok: bool, detail: str = "") -> str:
    """The canonical check line: ``name: OK - detail`` / ``name: FAIL - detail``.

    Pinned by tests and relied on by scripts; do not change the shape."""
    mark = "OK" if ok else "FAIL"
    return f"{name}: {mark} - {detail}" if detail else f"{name}: {mark}"


def error_and_exit(message: str, code: int = 1) -> None:
    """Report an error through the log and exit with an explicit code.

    One place for the exit semantics: user-facing failures log the reason
    (so it lands in data/ai-brain.log too) and always exit non-zero;
    usage errors use the default code 1."""
    log.error("%s", message)
    sys.exit(code)
