"""
Tests for src/orchestrator/*.

Fast + no network: uses a small in-memory-ish fixture roadmap file instead
of the real `iteration improvement ideas.md`, and monkeypatches git calls
in task_board so these tests never touch the actual repo's git state.
"""
import subprocess

import pytest

from opencode_crack.orchestrator import task_board, task_parser, delegator

FIXTURE_ROADMAP = """\
# Fixture Roadmap

## H-001 — Decide something important

**Priority: CRITICAL.**

Some human decision text.

---

## D-001 — Run the tests and report failures

**Priority: HIGH.**

Narrow, testable, reversible.

---

## D-002 — Rotate provider credentials

**Priority: HIGH.**

This title combines a risky action (rotate) with a sensitive target
(credentials) on purpose, to test the delegator's safety net.

---

## D-003 — Static security audit

**Priority: HIGH.**

Read-only recon for hard-coded secrets, unsafe subprocess usage, etc.
Should still be delegate-safe despite the word "security" in the title.

---

## C-001 — Redesign the core memory schema

**Priority: CRITICAL — architectural.**

Needs primary Claude.
"""


@pytest.fixture
def fixture_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FIXTURE_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    return roadmap_path


@pytest.fixture
def fixture_board(tmp_path, fixture_roadmap, monkeypatch):
    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    # Isolate STATUS.md too — every task_board write also regenerates it,
    # and without this every test run was silently overwriting the real
    # repo's STATUS.md with fixture data (caught during live verification).
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")
    # Every test in this module works on a fresh, isolated board.
    return board_path


@pytest.fixture
def no_op_git(monkeypatch):
    """Replace all git subprocess calls with successful no-ops, so
    task_board's claim/release/etc. logic can be tested without a real
    git repo or network access."""
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(task_board, "_run_git", fake_run_git)


# --- task_parser -------------------------------------------------------

def test_parse_roadmap_extracts_all_ids(fixture_roadmap):
    tasks = task_parser.parse_roadmap(fixture_roadmap)
    ids = [t.id for t in tasks]
    assert ids == ["H-001", "D-001", "D-002", "D-003", "C-001"]


def test_parse_roadmap_assigns_correct_tier(fixture_roadmap):
    tasks = {t.id: t for t in task_parser.parse_roadmap(fixture_roadmap)}
    assert tasks["H-001"].tier == "human"
    assert tasks["D-001"].tier == "delegate"
    assert tasks["C-001"].tier == "primary_claude"


def test_parse_roadmap_extracts_priority(fixture_roadmap):
    tasks = {t.id: t for t in task_parser.parse_roadmap(fixture_roadmap)}
    assert tasks["H-001"].priority == "CRITICAL"
    assert tasks["D-001"].priority == "HIGH"


def test_get_section_text_returns_matching_section(fixture_roadmap):
    section = task_parser.get_section_text("D-001")
    assert section.startswith("## D-001")
    assert "Run the tests and report failures" in section
    assert "HIGH" in section


def test_get_section_text_unknown_task_raises(fixture_roadmap):
    with pytest.raises(ValueError):
        task_parser.get_section_text("D-999")


def test_parse_roadmap_rejects_duplicate_task_ids_in_one_document(tmp_path):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(
        "## D-001 — First task\n\n**Priority: HIGH.**\n\n---\n\n"
        "## D-001 — Duplicate task\n\n**Priority: MEDIUM.**\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="D-001"):
        task_parser.parse_roadmap(roadmap_path)


def test_parse_roadmap_rejects_duplicate_task_ids_across_documents(tmp_path, monkeypatch):
    """A duplicate can also sneak in between the main roadmap and a
    delegated_tasks/ extension file — the check must span all documents."""
    main_path = tmp_path / "iteration improvement ideas.md"
    main_path.write_text("## D-001 — In main roadmap\n\n**Priority: HIGH.**\n", encoding="utf-8")
    ext_dir = tmp_path / "delegated_tasks"
    ext_dir.mkdir()
    (ext_dir / "extension.md").write_text(
        "## D-001 — In extension\n\n**Priority: MEDIUM.**\n", encoding="utf-8"
    )
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", main_path)
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", ext_dir)

    with pytest.raises(ValueError, match="D-001"):
        task_parser.parse_roadmap()


# --- task_board ----------------------------------------------------------

def test_sync_creates_all_open_tasks(fixture_board, fixture_roadmap, monkeypatch, no_op_git):
    """sync() must create an open board row for every task the roadmap
    parses — no more, no less (D-264).

    The fixture redirects ROADMAP_PATH but historically left
    DELEGATED_TASKS_DIR at the real delegated_tasks/ directory, so real
    extension tasks leaked in and the old hardcoded ``== 5`` broke as the
    extension grew. Isolating the extension dir makes the fixture truly
    hermetic, and the assertion pins the exact fixture ID set instead of
    a magic count."""
    ext_dir = fixture_roadmap.parent / "delegated_tasks"
    ext_dir.mkdir()
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", ext_dir)

    tasks = task_board.sync()
    assert {t.id for t in tasks} == {"H-001", "D-001", "D-002", "D-003", "C-001"}
    assert all(t.state.status == "open" for t in tasks)


def test_sync_preserves_existing_state(fixture_board, no_op_git):
    task_board.sync()
    task_board.claim_task("D-001", "agent-a")
    tasks = {t.id: t for t in task_board.sync()}
    assert tasks["D-001"].state.status == "claimed"
    assert tasks["D-001"].state.assignee == "agent-a"


def test_claim_then_list_by_status(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    open_tasks = task_board.list_tasks(status="open")
    claimed_tasks = task_board.list_tasks(status="claimed")
    assert "D-001" not in [t.id for t in open_tasks]
    assert "D-001" in [t.id for t in claimed_tasks]


def test_cannot_claim_already_claimed_task(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    with pytest.raises(RuntimeError, match="already"):
        task_board.claim_task("D-001", "agent-b")


def test_claim_remote_outage_is_not_reported_as_taken(fixture_board, monkeypatch):
    task_board.sync()

    def unreachable_git(*args):
        if args[:2] == ("pull", "--rebase"):
            return subprocess.CompletedProcess(
                args, returncode=1, stdout="", stderr="Could not resolve host: github.com"
            )
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(task_board, "_run_git", unreachable_git)
    with pytest.raises(task_board.GitSyncError, match="remote is unreachable"):
        task_board.claim_task("D-001", "agent-a")

    assert task_board.get_task("D-001").state.status == "open"
    assert not task_board.ACTIVITY_LOG.exists()


def test_start_task_requires_an_assignee(fixture_board, no_op_git):
    """The start_task() guard: an unclaimed task must refuse to go
    in_progress, otherwise the board shows in-progress work with no
    assignee (nobody knows who's doing it)."""
    with pytest.raises(RuntimeError, match="no assignee"):
        task_board.start_task("D-001", "agent-a")


def test_start_task_succeeds_after_claim(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task = task_board.start_task("D-001", "agent-a")
    assert task.state.status == "in_progress"
    assert task.state.assignee == "agent-a"


def test_complete_task_marks_done(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task = task_board.complete_task("D-001", notes="all good")
    assert task.state.status == "done"
    assert task.state.notes == "all good"


def test_release_task_reopens_and_clears_assignee(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task = task_board.release_task("D-001", notes="ran out of time", agent="agent-a")
    assert task.state.status == "open"
    assert task.state.assignee is None


def test_release_task_from_in_progress_returns_to_open(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.start_task("D-001", "agent-a")
    task = task_board.release_task("D-001", notes="interrupted mid-work", agent="agent-a")
    assert task.state.status == "open"
    assert task.state.assignee is None


def test_release_task_clears_all_active_ownership_fields(fixture_board, no_op_git):
    """Releasing must clear every ownership field (assignee + claimed_at +
    D-363 owner/owner_session), not just the status flag, so the board
    cannot keep showing who owned it."""
    task_board.claim_task("D-001", "agent-a")
    claimed = task_board.get_task("D-001")
    assert claimed.state.claimed_at is not None
    assert claimed.state.owner == "agent-a"
    task = task_board.release_task("D-001", notes="handed back", agent="agent-a")
    assert task.state.assignee is None
    assert task.state.claimed_at is None
    assert task.state.owner is None
    assert task.state.owner_session is None


def test_release_task_retains_notes(fixture_board, no_op_git):
    """A release with notes must keep those notes so the reason is not lost
    (the same behaviour a rejection relies on for its feedback)."""
    task_board.claim_task("D-001", "agent-a")
    task = task_board.release_task("D-001", notes="gave up the claim; test runner died", agent="agent-a")
    assert task.state.notes == "gave up the claim; test runner died"


def test_release_task_never_becomes_done_or_review(fixture_board, no_op_git):
    """Release is the opposite of submit: it must land on 'open' only, never
    drift into 'done' or 'review' regardless of the notes text."""
    task_board.claim_task("D-001", "agent-a")
    for notes in ("done", "submitted for review", "all finished"):
        task = task_board.release_task("D-001", notes=notes, agent="agent-a")
        assert task.state.status == "open"
        assert task.state.status not in ("done", "review")
        assert task.state.assignee is None


def test_stale_detection_flags_old_active_task(fixture_board, no_op_git):
    from datetime import datetime, timezone
    task_board.claim_task("D-001", "agent-a")
    task = task_board.get_task("D-001")
    # Not stale immediately after claiming.
    assert task_board.is_stale(task) is False

    # Backdate updated_at past the staleness threshold and check again.
    old_time = "2020-01-01T00:00:00Z"
    task.state.updated_at = old_time
    assert task_board.is_stale(task, now=datetime.now(timezone.utc)) is True


def test_stale_detection_ignores_non_active_statuses(fixture_board, no_op_git):
    task = task_board.get_task("D-001")
    task.state.status = "open"
    task.state.updated_at = "2020-01-01T00:00:00Z"
    assert task_board.is_stale(task) is False


# --- review workflow -------------------------------------------------------

def test_submit_for_review_requires_active_status(fixture_board, no_op_git):
    with pytest.raises(RuntimeError, match="expected claimed/in_progress"):
        task_board.submit_for_review("D-001", notes="done", agent="agent-a")


def test_submit_for_review_moves_to_review_not_done(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task = task_board.submit_for_review("D-001", notes="finished the tests", report_path="reports/D-001_report.md", agent="agent-a")
    assert task.state.status == "review"
    assert task.state.report_path == "reports/D-001_report.md"
    assert task.state.assignee == "agent-a"  # still locked to whoever submitted it


def test_cannot_claim_a_task_that_is_in_review(fixture_board, no_op_git):
    """This is the core overlap-prevention fix: a task an agent finished
    but hasn't been verified yet must stay locked, not look 'available'."""
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="done", agent="agent-a")
    with pytest.raises(RuntimeError, match="already"):
        task_board.claim_task("D-001", "agent-b")


def test_approve_task_requires_review_status(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    with pytest.raises(RuntimeError, match="expected 'review'"):
        task_board.approve_task("D-001")


def test_approve_task_marks_done(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    task = task_board.approve_task("D-001", notes="verified, tests pass")
    assert task.state.status == "done"
    assert task.state.notes == "verified, tests pass"


def test_reject_task_requires_notes(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    with pytest.raises(ValueError, match="requires notes"):
        task_board.reject_task("D-001", notes="")


def test_reject_task_reopens_with_feedback(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    task = task_board.reject_task("D-001", notes="tests don't actually cover the edge case")
    assert task.state.status == "open"
    assert task.state.assignee is None
    assert "edge case" in task.state.notes


def test_reject_then_reclaim_by_different_agent(fixture_board, no_op_git):
    """After rejection the task is genuinely open again — anyone can pick
    it up, and the rejection reason travels with it via notes."""
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    task_board.reject_task("D-001", notes="missing edge case coverage")
    task = task_board.claim_task("D-001", "agent-b")
    assert task.state.assignee == "agent-b"
    assert "missing edge case" in task.state.notes


def test_activity_log_records_transitions(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    task_board.approve_task("D-001")
    log_content = task_board.ACTIVITY_LOG.read_text()
    assert "open -> claimed" in log_content
    assert "claimed -> review" in log_content
    assert "review -> done" in log_content


def test_activity_log_entries_share_session_id_within_import(fixture_board, no_op_git):
    task_board.claim_task("D-001", "agent-a")
    task_board.submit_for_review("D-001", notes="finished", agent="agent-a")
    lines = task_board.ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        assert f"[{task_board.SESSION_ID}]" in line
    assert len(task_board.SESSION_ID) == 8


def test_activity_log_session_id_new_after_reload(fixture_board, no_op_git, monkeypatch):
    import importlib

    isolated_reports = task_board.REPORTS_DIR
    isolated_log = task_board.ACTIVITY_LOG
    original_id = task_board.SESSION_ID
    reloaded = importlib.reload(task_board)
    try:
        assert reloaded.SESSION_ID != original_id
        assert len(reloaded.SESSION_ID) == 8
        # reload re-executes the module body, resetting the fixture's
        # isolated paths — re-point them so the append stays in tmp
        monkeypatch.setattr(reloaded, "REPORTS_DIR", isolated_reports)
        monkeypatch.setattr(reloaded, "ACTIVITY_LOG", isolated_log)
        reloaded._append_activity("post-reload event")
        lines = isolated_log.read_text(encoding="utf-8").splitlines()
        assert lines[-1].endswith("post-reload event")
        assert f"[{reloaded.SESSION_ID}]" in lines[-1]
    finally:
        importlib.reload(task_board)


def test_claim_rejected_on_push_failure(fixture_board, monkeypatch):
    """If the push is rejected (simulating a concurrent claim by another
    agent), claim_task must raise rather than silently 'winning' the race."""
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(task_board, "_run_git", fake_run_git)
    monkeypatch.setattr(task_board, "_git_publish", lambda msg: False)

    with pytest.raises(RuntimeError, match="Push rejected"):
        task_board.claim_task("D-001", "agent-a")


# --- delegator -----------------------------------------------------------

def test_delegate_decision_for_human_task(fixture_board, no_op_git):
    task = task_board.get_task("H-001")
    decision = delegator.decide(task)
    assert decision.should_delegate is False


def test_delegate_decision_for_primary_claude_task(fixture_board, no_op_git):
    task = task_board.get_task("C-001")
    decision = delegator.decide(task)
    assert decision.should_delegate is False


def test_delegate_decision_for_clean_delegate_task(fixture_board, no_op_git):
    task = task_board.get_task("D-001")
    decision = delegator.decide(task)
    assert decision.should_delegate is True


def test_delegate_decision_blocks_on_dangerous_action_plus_target(fixture_board, no_op_git):
    """D-002's title combines a risky action ('rotate') with a sensitive
    target ('credentials') — the safety net should override the D- tier."""
    task = task_board.get_task("D-002")
    decision = delegator.decide(task)
    assert decision.should_delegate is False
    assert "risky action" in decision.reason.lower()


def test_delegate_decision_allows_readonly_security_recon(fixture_board, no_op_git):
    """A task can legitimately be *about* security (e.g. scanning for
    hard-coded secrets) without being dangerous to delegate — only the
    combination of a risky ACTION with a sensitive target should block."""
    task = task_board.get_task("D-003")  # title: "Static security audit" in fixture
    decision = delegator.decide(task)
    assert decision.should_delegate is True


def test_build_contract_includes_task_id_and_title(fixture_board, no_op_git):
    task = task_board.get_task("D-001")
    contract = delegator.build_contract(task)
    assert "D-001" in contract
    assert "Run the tests and report failures" in contract
    # Full section text should be embedded, not just referenced.
    assert "Narrow, testable, reversible" in contract


# --- status_report ---------------------------------------------------------

def test_sync_writes_status_md(fixture_board, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator import status_report
    status_path = tmp_path / "STATUS.md"
    monkeypatch.setattr(status_report, "STATUS_PATH", status_path)

    task_board.sync()
    assert status_path.exists()
    content = status_path.read_text()
    assert "Summary" in content
    assert "Active right now" in content


def test_status_md_reflects_active_and_open_work(fixture_board, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator import status_report
    status_path = tmp_path / "STATUS.md"
    monkeypatch.setattr(status_report, "STATUS_PATH", status_path)

    task_board.claim_task("D-001", "agent-a")
    content = status_path.read_text()
    assert "D-001" in content
    assert "agent-a" in content
    # H-001 was never claimed, so it should show up as not-yet-started.
    assert "H-001" in content


def test_save_states_rejects_fractional_second_timestamp(fixture_board, no_op_git):
    # D-263 regression: a fractional-second timestamp (the format
    # test_serialization_contract.py and is_stale()'s strptime both reject)
    # must never reach disk, regardless of which caller produced it.
    import pytest

    states = task_board._load_states()
    states["D-001"] = {
        "status": "open", "assignee": None, "notes": "",
        "claimed_at": None, "updated_at": "2026-08-21T17:21:06.289342Z",
    }
    with pytest.raises(ValueError, match="does not match the required"):
        task_board._save_states(states)


def test_save_states_rejects_non_utc_offset_timestamp(fixture_board, no_op_git):
    import pytest

    states = task_board._load_states()
    states["D-001"] = {
        "status": "open", "assignee": None, "notes": "",
        "claimed_at": None, "updated_at": "2026-08-21T17:21:06+00:00",
    }
    with pytest.raises(ValueError, match="does not match the required"):
        task_board._save_states(states)


def test_save_states_accepts_well_formed_timestamp(fixture_board, no_op_git):
    states = task_board._load_states()
    states["D-001"] = {
        "status": "open", "assignee": None, "notes": "",
        "claimed_at": None, "updated_at": task_board._now(),
    }
    task_board._save_states(states)  # must not raise
    assert task_board._load_states()["D-001"]["updated_at"] == states["D-001"]["updated_at"]


def test_status_md_flags_stale_active_task(fixture_board, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator import status_report
    status_path = tmp_path / "STATUS.md"
    monkeypatch.setattr(status_report, "STATUS_PATH", status_path)

    task_board.claim_task("D-001", "agent-a")
    states = task_board._load_states()
    states["D-001"]["updated_at"] = "2020-01-01T00:00:00Z"
    task_board._save_states(states)

    task_board.sync()
    content = status_path.read_text()
    assert "STALE" in content


# --- D-122: delegated task Origin line validation ------------------------------

FIXTURE_DELEGATED_OK = """\
# Delegated Task Extension — D-200 through D-201

## D-200 — Something new

**Priority: MEDIUM.**

**Origin: some review, 2026-08-18.** Do the thing.

**Out of scope:** everything else.

## D-201 — Another new task

**Priority: MEDIUM.**

**Origin: another review, 2026-08-18.** Do the other thing.

**Out of scope:** everything else.
"""

FIXTURE_DELEGATED_MISSING_ORIGIN = """\
# Delegated Task Extension — D-300 through D-301

## D-300 — Has an origin

**Priority: MEDIUM.**

**Origin: a review, 2026-08-18.** Fine.

**Out of scope:** nothing.

## D-301 — Missing an origin

**Priority: MEDIUM.**

No Origin line here at all.

**Out of scope:** nothing.
"""


def test_delegated_origin_check_ok_when_all_have_origin(tmp_path, monkeypatch):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_OK, encoding="utf-8")
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path)
    assert task_parser.delegated_tasks_missing_origin() == []


def test_delegated_origin_check_reports_missing_origin_task(tmp_path, monkeypatch):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_MISSING_ORIGIN, encoding="utf-8")
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path)
    result = task_parser.delegated_tasks_missing_origin()
    assert len(result) == 1
    assert "D-301" in result[0]
    assert "ext.md" in result[0]


def test_delegated_origin_check_ignores_h_and_c_tasks(tmp_path):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(
        "## H-001 — Human task\n\nNo origin needed.\n\n"
        "## C-001 — Claude task\n\nNo origin needed.\n",
        encoding="utf-8",
    )
    assert task_parser.delegated_tasks_missing_origin(ext_file) == []


def test_delegated_origin_check_uses_explicit_path_when_given(tmp_path):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    ext_file = other_dir / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_MISSING_ORIGIN, encoding="utf-8")
    result = task_parser.delegated_tasks_missing_origin(ext_file)
    assert len(result) == 1
    assert "D-301" in result[0]


def test_delegated_origin_check_no_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "does-not-exist")
    assert task_parser.delegated_tasks_missing_origin() == []


# --- D-123: delegated task out-of-scope section validation ----------------------

FIXTURE_DELEGATED_NO_SCOPE = """\
# Delegated Task Extension — D-400 through D-401

## D-400 — Has a scope boundary

**Priority: MEDIUM.**

**Origin: a review, 2026-08-18.** Fine.

**Out of scope:** everything else.

## D-401 — Missing a scope boundary

**Priority: MEDIUM.**

**Origin: another review, 2026-08-18.** Vague, no boundary here.
"""


def test_delegated_scope_check_ok_when_all_have_out_of_scope(tmp_path, monkeypatch):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_OK, encoding="utf-8")
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path)
    assert task_parser.delegated_tasks_missing_out_of_scope() == []


def test_delegated_scope_check_reports_missing_boundary_task(tmp_path, monkeypatch):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_NO_SCOPE, encoding="utf-8")
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path)
    result = task_parser.delegated_tasks_missing_out_of_scope()
    assert len(result) == 1
    assert "D-401" in result[0]
    assert "ext.md" in result[0]


def test_delegated_scope_check_ignores_h_and_c_tasks(tmp_path):
    ext_file = tmp_path / "ext.md"
    ext_file.write_text(
        "## H-001 — Human task\n\nNo scope needed.\n\n"
        "## C-001 — Claude task\n\nNo scope needed.\n",
        encoding="utf-8",
    )
    assert task_parser.delegated_tasks_missing_out_of_scope(ext_file) == []


def test_delegated_scope_check_uses_explicit_path_when_given(tmp_path):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    ext_file = other_dir / "ext.md"
    ext_file.write_text(FIXTURE_DELEGATED_NO_SCOPE, encoding="utf-8")
    result = task_parser.delegated_tasks_missing_out_of_scope(ext_file)
    assert len(result) == 1
    assert "D-401" in result[0]


def test_delegated_scope_check_no_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "does-not-exist")
    assert task_parser.delegated_tasks_missing_out_of_scope() == []


# --- D-158: get_next_task ordering -------------------------------------------

FEEDBACK_ROADMAP = """\
# Fixture Roadmap

## D-001 — Run tests

**Priority: HIGH.**

Run them.

**Out of scope:** everything else.

## D-002 — Fresh work

**Priority: MEDIUM.**

Do fresh work.

**Out of scope:** everything else.

## D-003 — Depends on D-001

**Priority: HIGH.**

Needs D-001 first.

**Out of scope:** everything else.

## D-004 — Also depends on D-001

**Priority: MEDIUM.**

Also needs D-001.

**Out of scope:** everything else.
"""


@pytest.fixture
def next_task_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FEEDBACK_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "nonexistent")
    return roadmap_path


def test_get_next_task_prefers_feedback_over_no_feedback(next_task_roadmap, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator.task_board import Task, TaskState, get_next_task

    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")

    task_board.sync()
    task_board._save_states({
        "D-001": {"status": "open", "notes": "needs more coverage"},
        "D-002": {"status": "open", "notes": ""},
        "D-003": {"status": "open", "notes": ""},
        "D-004": {"status": "open", "notes": ""},
    })

    next_task = get_next_task()
    assert next_task is not None
    assert next_task.id == "D-001"


def test_get_next_task_unblocking_value_ranks_above_priority(next_task_roadmap, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator.task_board import Task, TaskState, get_next_task

    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")

    task_board.sync()
    task_board._save_states({
        "D-001": {"status": "open", "notes": ""},
        "D-002": {"status": "open", "notes": ""},
        "D-003": {"status": "open", "notes": ""},
        "D-004": {"status": "open", "notes": ""},
    })

    next_task = get_next_task()
    assert next_task is not None
    assert next_task.id == "D-001"


def test_get_next_task_priority_tiebreaker(next_task_roadmap, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator.task_board import Task, TaskState, get_next_task

    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")

    task_board.sync()
    task_board._save_states({
        "D-001": {"status": "open", "notes": ""},
        "D-002": {"status": "open", "notes": ""},
    })

    next_task = get_next_task()
    assert next_task is not None
    assert next_task.id == "D-001"


def test_get_next_task_returns_none_when_no_open_delegate(next_task_roadmap, no_op_git, tmp_path, monkeypatch):
    from opencode_crack.orchestrator.task_board import get_next_task

    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    from opencode_crack.orchestrator import status_report
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")

    task_board.sync()
    task_board._save_states({
        "D-001": {"status": "claimed", "notes": ""},
        "D-002": {"status": "in_progress", "notes": ""},
        "D-003": {"status": "review", "notes": ""},
        "D-004": {"status": "done", "notes": ""},
        "H-001": {"status": "open", "notes": ""},
    })

    assert get_next_task() is None
