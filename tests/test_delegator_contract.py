"""Tests for src/orchestrator/delegator.py build_contract determinism
(roadmap D-087) and complete-section embedding (roadmap D-088).

The delegation contract is a formatted prompt that embeds the task's full
roadmap section. It must be byte-for-byte stable across repeated calls when
the roadmap and task state are unchanged — if anyone later injects a
timestamp, random id, dict-ordering dependency, or similar into
build_contract, these tests fail loudly. All calls are hermetically routed
to a small fixture roadmap so nothing depends on the real board.
"""
import pytest

from opencode_crack.orchestrator import task_parser
from opencode_crack.orchestrator import knowledge_board as kb
from opencode_crack.orchestrator.delegator import build_contract
from opencode_crack.orchestrator.task_board import Task

FIXTURE_ROADMAP = """\
# Fixture Roadmap

## D-001 — Run the tests and report failures

**Priority: HIGH.**

Narrow, testable, reversible.

---

## D-002 — Static security audit

**Priority: MEDIUM.**

Read-only recon.
"""


@pytest.fixture
def fixed_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FIXTURE_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


@pytest.fixture(autouse=True)
def isolated_knowledge_board(tmp_path, monkeypatch):
    """search_relevant() (C-75) always calls record_retrieval() on its
    matches -- by design, per the task spec, not a bug. That means any
    test calling build_contract()/build_compact_contract*() against the
    real repo's knowledge board risks silently mutating real
    orchestration/knowledge.yaml (and, if a test files an entry,
    KNOWLEDGE_BOARD.md's render) if a fixture task's title happens to
    share a tag. Autouse-isolate every test in this file to an empty
    tmp knowledge tree so that can never happen, regardless of what
    future fixture task titles get added here.

    Must patch knowledge_report.BOARD_MD_PATH too, not just
    knowledge_board's own paths -- missing that once already let an
    isolated test's board render leak into the real KNOWLEDGE_BOARD.md
    even though the underlying data files stayed correctly isolated.
    """
    from opencode_crack.orchestrator import knowledge_parser as kp
    from opencode_crack.orchestrator import knowledge_report as kr
    import subprocess as _subprocess

    knowledge_dir = tmp_path / "isolated_knowledge"
    monkeypatch.setattr(kp, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(kb, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(kb, "BOARD_PATH", tmp_path / "isolated_knowledge.yaml")
    monkeypatch.setattr(kb, "ACTIVITY_LOG", knowledge_dir / "activity.log")
    monkeypatch.setattr(kr, "BOARD_MD_PATH", tmp_path / "ISOLATED_KNOWLEDGE_BOARD.md")

    def fake_run_git(*args):
        return _subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(kb, "_run_git", fake_run_git)


def _task(task_id="D-001", title="Run the tests and report failures",
          tier="delegate", priority="HIGH") -> Task:
    return Task(id=task_id, tier=tier, title=title, priority=priority)


def test_contract_is_byte_for_byte_stable_across_repeated_calls(fixed_roadmap):
    task = _task()
    first = build_contract(task)
    for _ in range(5):
        assert build_contract(task) == first


def test_contract_stable_across_independent_task_instances(fixed_roadmap):
    a = build_contract(_task())
    b = build_contract(_task())
    c = build_contract(_task(task_id="D-002", title="Static security audit", priority="MEDIUM"))
    assert a == b
    assert a != c  # sanity: the check isn't trivially passing on a constant


def test_contract_stable_across_fresh_roadmap_reads(fixed_roadmap):
    """Re-reading the roadmap file between calls must not change output —
    the roadmap is unchanged, so the contract must be too."""
    first = build_contract(_task())
    task_parser.parse_roadmap(task_parser.ROADMAP_PATH)  # fresh parse of same file
    assert build_contract(_task()) == first


def test_contract_deterministic_with_explicit_scope_overrides(fixed_roadmap):
    task = _task()
    first = build_contract(
        task,
        allowed_paths="src/orchestrator/",
        forbidden_paths="everything else",
    )
    assert build_contract(
        task,
        allowed_paths="src/orchestrator/",
        forbidden_paths="everything else",
    ) == first


def test_contract_fallback_text_is_stable_when_section_unavailable(fixed_roadmap, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("section not found")

    monkeypatch.setattr(task_parser, "get_section_text", boom)
    first = build_contract(_task())
    assert build_contract(_task()) == first
    assert "Section text unavailable" in first


def test_contract_does_not_include_volatile_task_state(fixed_roadmap):
    """Task state (assignee, notes, timestamps) must not leak into the
    contract — state changes between calls must leave the output identical."""
    task_a = _task()
    task_b = _task()
    task_b.state.assignee = "some-agent"
    task_b.state.notes = "updated later"
    task_b.state.updated_at = "2026-08-18T00:00:00Z"
    assert build_contract(task_a) == build_contract(task_b)


# --- D-088: contract embeds the complete task section -------------------------

FIXTURE_RICH_ROADMAP = """\
# Fixture Roadmap

## D-088 — Embed the complete section

**Priority: MEDIUM.**

**Origin: build_contract and get_section_text design review, 2026-08-17.**

This task body has a distinctive multi-line paragraph that must appear
verbatim inside the generated contract, proving the contract embeds the
complete section rather than only the title or a cross-reference.

**Out of scope:** task selection, claiming, and delegation-policy changes.

Final distinctive tail sentence that should travel with the section.
"""


@pytest.fixture
def rich_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FIXTURE_RICH_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


def test_contract_embeds_complete_task_section_verbatim(rich_roadmap):
    from opencode_crack.orchestrator.task_parser import get_section_text

    task = _task(task_id="D-088", title="Embed the complete section", priority="MEDIUM")
    contract = build_contract(task)
    section = get_section_text("D-088")
    # The full section appears as one contiguous block, not title-only.
    assert section in contract
    # Every line of the section travels with it.
    for line in section.splitlines():
        assert line in contract


def test_contract_embeds_section_in_task_region_not_elsewhere(rich_roadmap):
    from opencode_crack.orchestrator.task_parser import get_section_text

    task = _task(task_id="D-088", title="Embed the complete section", priority="MEDIUM")
    contract = build_contract(task)
    section = get_section_text("D-088")
    task_start = contract.index("TASK\n----")
    scope_start = contract.index("SCOPE\n-----")
    section_pos = contract.index(section)
    assert task_start < section_pos < scope_start


def test_contract_is_not_a_title_only_summary_or_cross_reference(rich_roadmap):
    """Body text that exists only in the full section — not in the title and
    not as a roadmap pointer — must appear, and the fallback 'see the roadmap
    directly' cross-reference must NOT appear in the embedded task region."""
    task = _task(task_id="D-088", title="Embed the complete section", priority="MEDIUM")
    contract = build_contract(task)
    assert "distinctive multi-line paragraph" in contract
    assert "**Out of scope:**" in contract
    assert "Final distinctive tail sentence that should travel" in contract
    task_region = contract.split("TASK\n----", 1)[1].split("SCOPE\n-----", 1)[0]
    assert "see iteration improvement ideas.md directly" not in task_region


# --- C-75: Knowledge Board (K-*.md) automatic contract injection -----------

from opencode_crack.orchestrator.delegator import build_compact_contract, build_compact_contract_json
import json as _json


def _file_and_verify(**kw):
    base = dict(title="Something learned", body="body text", category="technique",
                submitted_by="agent-a", tags=["python"], publish=False)
    base.update(kw)
    e = kb.file_knowledge(**base)
    kb.mark_agent_verified(e.id, "agent-a", "self-checked")
    return kb.mark_reviewer_verified(e.id, "reviewer", "checked")


def test_board_knowledge_section_appears_when_relevant(fixed_roadmap):
    _file_and_verify(title="Static analysis pitfall", tags=["security", "audit"])
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_contract(task)
    assert "RELEVANT PRIOR KNOWLEDGE" in contract
    assert "Static analysis pitfall" in contract


def test_board_knowledge_section_omitted_when_no_overlap(fixed_roadmap):
    """D-001's title ('Run the tests and report failures') shares no
    keywords with this entry's tags -- the section must not appear at
    all, not appear empty."""
    _file_and_verify(title="Unrelated fact", tags=["banking", "compliance"])
    task = _task()  # D-001
    contract = build_contract(task)
    assert "RELEVANT PRIOR KNOWLEDGE" not in contract


def test_board_knowledge_section_omitted_with_empty_knowledge_base(fixed_roadmap):
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_contract(task)
    assert "RELEVANT PRIOR KNOWLEDGE" not in contract


def test_board_knowledge_line_format_matches_spec(fixed_roadmap):
    """One line per entry: ID, status in parens, title only -- no body."""
    e = _file_and_verify(title="sentence_transformers fails under Python 3.14",
                          tags=["security", "audit"], body="full body should not leak")
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_contract(task)
    expected_line = f"- {e.id} (reviewer-verified) — sentence_transformers fails under Python 3.14"
    assert expected_line in contract
    assert "full body should not leak" not in contract


def test_board_knowledge_appears_immediately_before_task(fixed_roadmap):
    _file_and_verify(title="Static analysis pitfall", tags=["security", "audit"])
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_contract(task)
    section_pos = contract.index("RELEVANT PRIOR KNOWLEDGE")
    task_pos = contract.index("TASK\n----")
    assert section_pos < task_pos
    # Nothing but whitespace/the durable-knowledge block between the two.
    between = contract[section_pos:task_pos]
    assert between.count("RELEVANT PRIOR KNOWLEDGE") == 1


def test_compact_contract_includes_board_knowledge(fixed_roadmap):
    _file_and_verify(title="Static analysis pitfall", tags=["security", "audit"])
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_compact_contract(task)
    assert "RELEVANT PRIOR KNOWLEDGE" in contract
    assert "Static analysis pitfall" in contract


def test_compact_contract_json_includes_board_knowledge(fixed_roadmap):
    e = _file_and_verify(title="Static analysis pitfall", tags=["security", "audit"])
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    parsed = _json.loads(build_compact_contract_json(task))
    assert "relevant_board_knowledge" in parsed
    assert parsed["relevant_board_knowledge"][0]["id"] == e.id
    assert parsed["relevant_board_knowledge"][0]["status"] == "reviewer-verified"


def test_unverified_board_knowledge_not_injected(fixed_roadmap):
    """Filed but never verified -- must not appear in an automatically
    injected contract (search_relevant's include_unverified defaults to
    False for exactly this path)."""
    kb.file_knowledge(title="Fresh unverified pitfall", body="text",
                       category="technique", submitted_by="agent-a",
                       tags=["security", "audit"], publish=False)
    task = _task(task_id="D-002", title="Static security audit", priority="MEDIUM")
    contract = build_contract(task)
    assert "RELEVANT PRIOR KNOWLEDGE" not in contract
    assert "Fresh unverified pitfall" not in contract