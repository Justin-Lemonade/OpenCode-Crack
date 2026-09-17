"""
Protocol redundancy audit (roadmap D-159).

Pins the static audit behavior: it flags duplicate/near-duplicate
instruction blocks above a configurable size across protocol documents and
the delegation contract template; reports source file + approximate
duplicate text; never deletes or requires deletion; and is verified against
small fixtures with known duplicates/non-duplicates (the comparison core is
pure and takes explicit blocks).
"""
import pytest

from opencode_crack.orchestrator.protocol_audit import (
    Block,
    DEFAULT_MIN_CHARS,
    Duplicate,
    _excerpt,
    audit_protocols,
    find_duplicates,
    load_protocol_documents,
    normalize_block,
    render_report,
    split_blocks,
)


def _para(text: str, padding: int = 0) -> str:
    return " ".join([text] * (padding + 1))


# --- block splitting / normalization ------------------------------------------------

class TestBlockSplitting:
    def test_short_blocks_filtered(self):
        assert split_blocks("tiny", min_chars=50) == []

    def test_long_block_kept(self):
        text = "This is a long instruction block that clearly exceeds the minimum size."
        assert split_blocks(text, min_chars=20) == [normalize_block(text)]

    def test_whitespace_and_casing_normalized(self):
        a = "Implement ONE task.  Report to\n the manager."
        b = "implement one task. report to the manager."
        assert normalize_block(a) == normalize_block(b)

    def test_bullet_markers_stripped(self):
        a = "- implement the fix\n- run the tests"
        b = "implement the fix run the tests"
        assert normalize_block(a) == normalize_block(b)


# --- fixture-based duplicate detection ----------------------------------------------

DUPE_A = _para("The agent must claim the task before any other work begins and must never work on an unclaimed task.", 6)
DUPE_B = _para("The agent must claim the task before any other work begins and must never work on an unclaimed task.", 6)
NEAR_A = _para("Every task ends by submitting a report and moving the task to review, never completing it directly.", 6)
NEAR_B = _para("Every task ends by submitting a report and moving the task to review, never completing it DIRECTLY.", 6)
DISTINCT = _para("A completely different instruction block that shares nothing with the others above.", 6)


class TestFindDuplicates:
    def test_exact_duplicate_across_sources_flagged(self):
        hits = find_duplicates([Block("a", DUPE_A), Block("b", DUPE_B)], min_chars=20)
        assert len(hits) == 1
        assert hits[0].similarity == 1.0
        assert {hits[0].source, hits[0].other} == {"a", "b"}

    def test_near_duplicate_flagged(self):
        hits = find_duplicates([Block("a", NEAR_A), Block("b", NEAR_B)], min_chars=20)
        assert any(h.similarity >= 0.9 and 0.0 < h.similarity < 1.0 for h in hits)

    def test_distinct_blocks_not_flagged(self):
        hits = find_duplicates([Block("a", DUPE_A), Block("b", DISTINCT)], min_chars=20)
        assert hits == []

    def test_same_source_blocks_not_flagged(self):
        hits = find_duplicates([Block("a", DUPE_A), Block("a", DUPE_B)], min_chars=20)
        assert hits == []

    def test_configurable_min_size(self):
        # Below the size floor, the same text is not flagged.
        short_a = "claim the task first"
        short_b = "claim the task first"
        assert find_duplicates([Block("a", short_a), Block("b", short_b)], min_chars=100) == []
        assert len(find_duplicates([Block("a", short_a), Block("b", short_b)], min_chars=10)) == 1

    def test_threshold_raises_bar(self):
        hits = find_duplicates([Block("a", NEAR_A), Block("b", NEAR_B)],
                               min_chars=20, similarity_threshold=1.0)
        assert hits == []  # only case change -> below 1.0

    def test_empty_input(self):
        assert find_duplicates([]) == []


# --- read-only behavior --------------------------------------------------------------

class TestReadOnly:
    def test_report_is_read_only(self):
        hits = [Duplicate("a", "b", DUPE_A, 1.0)]
        report = render_report(hits)
        assert "Read-only report" in report
        assert "delete" not in report.lower() or "never" in report.lower()

    def test_empty_report(self):
        assert "no duplicate" in render_report([])

    def test_report_names_sources(self):
        hits = [Duplicate("AGENTS.md", "find_work.md", DUPE_A, 1.0)]
        report = render_report(hits)
        assert "AGENTS.md" in report and "find_work.md" in report
        assert _excerpt(DUPE_A) in report


# --- real protocol documents ---------------------------------------------------------

class TestRealDocuments:
    # AI-Brain's version of this test also asserted specific real
    # documents (AGENTS.md, delegator.build_contract's docstring) are
    # present. Those aren't ported into this framework repo yet — see
    # C-076's manifest — so that assertion doesn't apply here; the
    # loader mechanics themselves are already covered by
    # test_audit_runs_without_error below, which works against
    # whatever documents actually exist.

    def test_audit_runs_without_error(self):
        hits = audit_protocols()
        assert isinstance(hits, list)
        for h in hits:
            assert isinstance(h, Duplicate)
            assert h.similarity >= 0.9
            assert h.source != h.other