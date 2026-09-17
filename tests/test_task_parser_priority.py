"""Tests for src/orchestrator/task_parser.py priority parsing edge cases
(roadmap D-081).

Pins the documented priority formats — including the compound priorities
MEDIUM-HIGH / LOW-MEDIUM and explanatory text after the priority — and the
explicit fallback contract: a genuinely malformed priority (something the
regex captures that isn't in the documented vocabulary) must parse to the
"UNSPECIFIED" sentinel rather than silently being returned as if it were a
real priority.
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


def _roadmap(priority_line):
    return (
        "## D-001 — Run the tests\n\n"
        f"{priority_line}\n\n"
        "Narrow, testable.\n"
    )


def test_medium_high_priority_parses(parse):
    assert parse(_roadmap("**Priority: MEDIUM-HIGH.**"))[0].priority == "MEDIUM-HIGH"


def test_low_medium_priority_parses(parse):
    assert parse(_roadmap("**Priority: LOW-MEDIUM.**"))[0].priority == "LOW-MEDIUM"


def test_priority_with_explanatory_text_after_period(parse):
    assert parse(_roadmap("**Priority: HIGH.** Critical path, do first."))[0].priority == "HIGH"


def test_priority_with_explanatory_text_after_em_dash(parse):
    assert parse(_roadmap("**Priority: MEDIUM — do after HIGHs.**"))[0].priority == "MEDIUM"


def test_priority_with_explanatory_text_after_bold(parse):
    assert parse(_roadmap("**Priority: HIGH** is the team's call."))[0].priority == "HIGH"


def test_priority_stripped_of_whitespace(parse):
    assert parse(_roadmap("**Priority:  MEDIUM .**"))[0].priority == "MEDIUM"


def test_malformed_priority_falls_back_to_unspecified(parse):
    # Captured by the regex but not a documented priority — must NOT be
    # returned verbatim as a silent guess.
    assert parse(_roadmap("**Priority: HIGHX.**"))[0].priority == "UNSPECIFIED"


def test_two_word_malformed_priority_falls_back_to_unspecified(parse):
    assert parse(_roadmap("**Priority: MEDIUM IMPORTANT.**"))[0].priority == "UNSPECIFIED"


def test_hyphenated_unknown_priority_falls_back_to_unspecified(parse):
    assert parse(_roadmap("**Priority: HIGH-IMPORTANT.**"))[0].priority == "UNSPECIFIED"


def test_missing_priority_falls_back_to_unspecified(parse):
    assert parse(_roadmap("Some body text with no priority line."))[0].priority == "UNSPECIFIED"


def test_valid_priorities_all_parse(parse):
    for priority in ("CRITICAL", "HIGH", "MEDIUM-HIGH", "MEDIUM", "LOW-MEDIUM", "LOW"):
        tasks = parse(_roadmap(f"**Priority: {priority}.**"))
        assert tasks[0].priority == priority