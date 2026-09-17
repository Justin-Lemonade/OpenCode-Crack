"""Tests for src/orchestrator/concern_parser.py."""
import pytest

from opencode_crack.orchestrator import concern_parser


def _write(directory, filename, text):
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_concerns_extracts_all_fields(tmp_path):
    _write(tmp_path, "CN-001.md", """\
## CN-001 — Board shows a task done that isn't

**Severity:** HIGH
**Category:** board-integrity
**Raised by:** opencode (window 3)
**Related task:** D-259

D-259 shows status done but the dependency graph file it was supposed to
produce doesn't exist in the repo.
""")
    concerns = concern_parser.parse_concerns(tmp_path)
    assert len(concerns) == 1
    c = concerns[0]
    assert c.id == "CN-001"
    assert c.title == "Board shows a task done that isn't"
    assert c.severity == "HIGH"
    assert c.category == "board-integrity"
    assert c.raised_by == "opencode (window 3)"
    assert c.related_task == "D-259"
    assert "dependency graph file" in c.body


def test_parse_concerns_without_related_task_is_none(tmp_path):
    _write(tmp_path, "CN-001.md", """\
## CN-001 — General process friction

**Severity:** LOW
**Category:** process
**Raised by:** Kilo

Just some narrative text.
""")
    c = concern_parser.parse_concerns(tmp_path)[0]
    assert c.related_task is None


def test_parse_concerns_invalid_severity_falls_back_to_medium(tmp_path):
    _write(tmp_path, "CN-001.md", """\
## CN-001 — Malformed severity field

**Severity:** SUPER-URGENT
**Category:** other
**Raised by:** Kilo

body text
""")
    c = concern_parser.parse_concerns(tmp_path)[0]
    assert c.severity == "MEDIUM"


def test_parse_concerns_invalid_category_falls_back_to_other(tmp_path):
    _write(tmp_path, "CN-001.md", """\
## CN-001 — Malformed category field

**Severity:** LOW
**Category:** made-up-category
**Raised by:** Kilo

body text
""")
    c = concern_parser.parse_concerns(tmp_path)[0]
    assert c.category == "other"


def test_parse_concerns_missing_directory_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert concern_parser.parse_concerns(missing) == []


def test_parse_concerns_multiple_files_sorted_and_merged(tmp_path):
    _write(tmp_path, "CN-002.md", "## CN-002 — Second\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** a\n\nbody\n")
    _write(tmp_path, "CN-001.md", "## CN-001 — First\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** a\n\nbody\n")
    ids = [c.id for c in concern_parser.parse_concerns(tmp_path)]
    assert ids == ["CN-001", "CN-002"]


def test_parse_concerns_duplicate_id_across_files_raises(tmp_path):
    _write(tmp_path, "CN-001.md", "## CN-001 — First\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** a\n\nbody\n")
    _write(tmp_path, "CN-001b.md", "## CN-001 — Duplicate\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** b\n\nbody\n")
    with pytest.raises(ValueError, match="Duplicate concern ID"):
        concern_parser.parse_concerns(tmp_path)


def test_get_concern_section_text_returns_raw_section(tmp_path):
    _write(tmp_path, "CN-001.md", "## CN-001 — Title here\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** a\n\nSome body text.\n")
    text = concern_parser.get_concern_section_text("CN-001", tmp_path)
    assert text.startswith("## CN-001 — Title here")
    assert "Some body text." in text


def test_get_concern_section_text_missing_id_raises(tmp_path):
    _write(tmp_path, "CN-001.md", "## CN-001 — Title\n\n**Severity:** LOW\n**Category:** other\n**Raised by:** a\n\nbody\n")
    with pytest.raises(ValueError, match="No such concern"):
        concern_parser.get_concern_section_text("CN-999", tmp_path)
