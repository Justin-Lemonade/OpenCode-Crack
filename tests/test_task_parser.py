"""Tests for src/orchestrator/task_parser.py heading discrimination
(roadmap D-082).

Ordinary Markdown headings and prose that merely contains H-/D-/C-style
identifiers must never be parsed as tasks — only the exact task-header
format (`## <H|D|C>-<digits> — <title>`) counts. The parser is deliberately
NOT broadened here; these tests pin the existing strict matching.
"""
import pytest

from opencode_crack.orchestrator import task_parser


@pytest.fixture
def parse(tmp_path):
    """Parse the given roadmap text via a temp file and return the tasks."""

    def _parse(text: str):
        roadmap = tmp_path / "roadmap.md"
        roadmap.write_text(text, encoding="utf-8")
        return task_parser.parse_roadmap(roadmap)

    return _parse


def test_plain_heading_is_ignored(parse):
    assert parse("# Roadmap\n\n## Overview\n\nBody.\n") == []


def test_numbered_section_heading_is_ignored(parse):
    assert parse("## 1. Background\n\nBody.\n") == []


def test_task_like_id_in_prose_is_ignored(parse):
    assert parse("We finished D-001 and started D-002 yesterday.\n") == []


def test_task_like_id_in_list_item_is_ignored(parse):
    assert parse("- D-001: run the tests\n- H-001: human decision\n") == []


def test_hyphen_separator_heading_is_ignored(parse):
    # "D-001 - Title" uses a hyphen, not the em-dash — must NOT parse.
    assert parse("## D-001 - Something\n\nBody.\n") == []


def test_colon_separator_heading_is_ignored(parse):
    assert parse("## D-001: Something\n\nBody.\n") == []


def test_numbered_prefix_before_task_id_heading_is_ignored(parse):
    assert parse("## 5. D-001 — Something\n\nBody.\n") == []


def test_short_task_id_is_ignored(parse):
    # D-1 (not D-001) does not match the \d{3} format.
    assert parse("## D-1 — Something\n\nBody.\n") == []


def test_real_task_parses_alongside_noise_headings(parse):
    text = (
        "# Roadmap\n\n"
        "## Overview\n\n"
        "Intro text mentioning D-999.\n\n"
        "## D-001 — Run the tests\n\n"
        "**Priority: HIGH.**\n\n"
        "Narrow, testable.\n\n"
        "## 1. Background\n\n"
        "More prose.\n"
    )
    tasks = parse(text)
    assert [t.id for t in tasks] == ["D-001"]
    assert tasks[0].title == "Run the tests"


def test_prose_duplicate_of_real_id_does_not_cause_error(parse):
    """A real task plus a prose mention of the same ID elsewhere must not
    trip the duplicate-ID guard (prose never registers a task)."""
    text = (
        "## D-001 — Run the tests\n\n"
        "**Priority: HIGH.**\n\n"
        "See D-001 for details.\n"
    )
    assert [t.id for t in parse(text)] == ["D-001"]