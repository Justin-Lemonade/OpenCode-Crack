"""Tests for src/orchestrator/knowledge_parser.py (D-313, mirrors test_concern_parser.py)."""
import pytest

from opencode_crack.orchestrator import knowledge_parser


def _write(directory, filename, text):
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_knowledge_extracts_all_fields(tmp_path):
    _write(tmp_path, "K-042.md", """\
## K-042 — sentence_transformers fails under Python 3.14 in this environment

**Status:** agent-verified
**Category:** environment
**Tags:** python, dependencies, sentence_transformers, embeddings
**Submitted by:** opencode (window 3) (task D-260)
**Related task:** D-260
**Supersedes:** K-031

sentence_transformers fails to import under Python 3.14 because of the
removed imp module shim.
""")
    entries = knowledge_parser.parse_knowledge(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "K-042"
    assert e.title == "sentence_transformers fails under Python 3.14 in this environment"
    assert e.status == "agent-verified"
    assert e.category == "environment"
    assert e.tags == ["python", "dependencies", "sentence_transformers", "embeddings"]
    assert e.submitted_by == "opencode (window 3) (task D-260)"
    assert e.related_task == "D-260"
    assert e.supersedes == "K-031"
    assert "removed imp module shim" in e.body


def test_parse_knowledge_multiple_entries_in_one_file(tmp_path):
    _write(tmp_path, "K-batch.md", """\
## K-001 — First entry

**Status:** draft
**Category:** technique
**Tags:** a
**Submitted by:** tester

first body

## K-002 — Second entry

**Status:** agent-verified
**Category:** gotcha
**Tags:** b, c
**Submitted by:** tester

second body
""")
    entries = knowledge_parser.parse_knowledge(tmp_path)
    assert [e.id for e in entries] == ["K-001", "K-002"]
    assert "first body" in entries[0].body
    assert "second body" in entries[1].body


def test_parse_knowledge_without_optional_fields_are_none(tmp_path):
    _write(tmp_path, "K-001.md", """\
## K-001 — General technique note

**Status:** draft
**Category:** technique
**Tags:** notes
**Submitted by:** Kilo

Just some narrative text.
""")
    e = knowledge_parser.parse_knowledge(tmp_path)[0]
    assert e.related_task is None
    assert e.supersedes is None


def test_parse_knowledge_invalid_category_falls_back_to_other(tmp_path):
    _write(tmp_path, "K-001.md", """\
## K-001 — Malformed category field

**Status:** draft
**Category:** made-up-category
**Tags:** x
**Submitted by:** Kilo

body text
""")
    e = knowledge_parser.parse_knowledge(tmp_path)[0]
    assert e.category == "other"


def test_parse_knowledge_empty_tags_list_handled(tmp_path):
    _write(tmp_path, "K-001.md", """\
## K-001 — No tags entry

**Status:** draft
**Category:** process
**Submitted by:** Kilo

body text without any tags line
""")
    e = knowledge_parser.parse_knowledge(tmp_path)[0]
    assert e.tags == []


def test_parse_knowledge_tags_lowercased_and_stripped(tmp_path):
    _write(tmp_path, "K-001.md", """\
## K-001 — Tag normalization

**Status:** draft
**Category:** tooling
**Tags:**  Python , DOCKER,  embeddings
**Submitted by:** Kilo

body
""")
    e = knowledge_parser.parse_knowledge(tmp_path)[0]
    assert e.tags == ["python", "docker", "embeddings"]


def test_parse_knowledge_missing_directory_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert knowledge_parser.parse_knowledge(missing) == []


def test_parse_knowledge_multiple_files_sorted_and_merged(tmp_path):
    _write(tmp_path, "K-002.md", "## K-002 — Second\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** a\n\nbody\n")
    _write(tmp_path, "K-001.md", "## K-001 — First\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** a\n\nbody\n")
    ids = [e.id for e in knowledge_parser.parse_knowledge(tmp_path)]
    assert ids == ["K-001", "K-002"]


def test_parse_knowledge_duplicate_id_across_files_raises(tmp_path):
    _write(tmp_path, "K-001.md", "## K-001 — First\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** a\n\nbody\n")
    _write(tmp_path, "K-001b.md", "## K-001 — Duplicate\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** b\n\nbody\n")
    with pytest.raises(ValueError, match="Duplicate knowledge ID"):
        knowledge_parser.parse_knowledge(tmp_path)


def test_get_knowledge_section_text_returns_raw_section(tmp_path):
    _write(tmp_path, "K-001.md", "## K-001 — Title here\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** a\n\nSome body text.\n")
    text = knowledge_parser.get_knowledge_section_text("K-001", tmp_path)
    assert text.startswith("## K-001 — Title here")
    assert "Some body text." in text


def test_get_knowledge_section_text_missing_id_raises(tmp_path):
    _write(tmp_path, "K-001.md", "## K-001 — Title\n\n**Status:** draft\n**Category:** other\n**Tags:** a\n**Submitted by:** a\n\nbody\n")
    with pytest.raises(ValueError, match="No such knowledge entry"):
        knowledge_parser.get_knowledge_section_text("K-999", tmp_path)
