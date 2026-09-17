"""Tests for src/orchestrator/report_validator.py (roadmap D-085 + D-086).

D-085 scope: missing required sections (Environment + five-point format).
D-086 scope: git-state wording detection — the validator must tell apart
"pushed in commit", "committed locally, not pushed", and "working tree
only, no commit" phrasings, and must never infer git state from prose.
"""
import pytest

from opencode_crack.orchestrator import report_validator

FULL_REPORT = """\
# D-999 Report — Everything works

## 0. Environment
OS: win32. Internet: yes. Push: yes.

## 1. Summary
Did the thing.

## 2. Files changed
- src/foo.py

## 3. Tests run
pytest -q -> all pass

## 4. Failures
None.

## 5. Questions/blockers
None.
"""


def test_complete_report_has_no_missing_sections():
    assert report_validator.missing_sections(FULL_REPORT) == []


def test_missing_sections_are_listed_in_protocol_order():
    text = "## 0. Environment\n## 1. Summary\n"
    assert report_validator.missing_sections(text) == [
        "Files changed",
        "Tests run",
        "Failures",
        "Questions/blockers",
    ]


def test_empty_text_reports_everything_missing():
    assert report_validator.missing_sections("") == report_validator.REQUIRED_SECTIONS


def test_heading_level_and_numbering_variants_are_accepted():
    text = (
        "### 0. **Environment**\n"
        "## Summary\n"
        "#### 2. Files changed\n"
        "# 3. Tests run\n"
        "## 4. Failures\n"
        "## 5. Questions/blockers\n"
    )
    assert report_validator.missing_sections(text) == []


def test_questions_and_blockers_spelling_variants_are_accepted():
    for heading in ("## 5. Questions & blockers", "## 5. Questions and blockers", "## 5. Questions/blockers"):
        text = FULL_REPORT.replace("## 5. Questions/blockers", heading)
        assert report_validator.missing_sections(text) == []


def test_matching_is_case_insensitive():
    text = FULL_REPORT.replace("## 1. Summary", "## 1. SUMMARY")
    assert report_validator.missing_sections(text) == []


def test_non_heading_text_never_counts_as_a_section():
    text = "just prose mentioning Summary, Files changed, Tests run, Failures\n"
    assert report_validator.missing_sections(text) == report_validator.REQUIRED_SECTIONS


def test_validate_report_reads_file_path(tmp_path):
    report = tmp_path / "D-999_report.md"
    report.write_text(FULL_REPORT, encoding="utf-8")
    assert report_validator.validate_report(report) == []


def test_validate_report_reads_missing_sections_from_file(tmp_path):
    report = tmp_path / "D-998_report.md"
    report.write_text("## 1. Summary\n## 2. Files changed\n", encoding="utf-8")
    assert report_validator.validate_report(report) == [
        "Environment",
        "Tests run",
        "Failures",
        "Questions/blockers",
    ]


# --- D-120: report task ID must match its filename ------------------------------

def test_report_task_id_extracted_from_header_heading():
    assert report_validator.report_task_id(FULL_REPORT) == "D-999"


def test_report_task_id_matches_dash_form_header():
    text = "# D-051 — Add session correlation ID to activity.log entries\n## 1. Summary\n"
    assert report_validator.report_task_id(text) == "D-051"


def test_report_task_id_ignores_task_ids_in_body_prose():
    # A task ID mentioned in prose (not a heading) must not count.
    text = "worked on D-120 and finished it.\n"
    assert report_validator.report_task_id(text) is None


def test_report_task_id_none_when_no_task_id_anywhere():
    assert report_validator.report_task_id("just a report with no id\n") is None


def test_task_id_matches_filename_true_when_ids_agree():
    assert report_validator.task_id_matches_filename(FULL_REPORT, "D-999") is True


def test_task_id_matches_filename_false_when_ids_differ():
    assert report_validator.task_id_matches_filename(FULL_REPORT, "D-998") is False


def test_validate_report_id_true_when_header_matches_filename(tmp_path):
    report = tmp_path / "D-120_report.md"
    report.write_text(FULL_REPORT.replace("D-999", "D-120"), encoding="utf-8")
    assert report_validator.validate_report_id(report) is True


def test_validate_report_id_false_when_header_wrong_task(tmp_path):
    report = tmp_path / "D-120_report.md"
    report.write_text(FULL_REPORT, encoding="utf-8")  # header says D-999
    assert report_validator.validate_report_id(report) is False


def test_validate_report_id_false_when_filename_has_no_task_id(tmp_path):
    report = tmp_path / "unexpected_name.md"
    report.write_text(FULL_REPORT, encoding="utf-8")
    assert report_validator.validate_report_id(report) is False


def test_validate_report_id_false_when_empty(tmp_path):
    report = tmp_path / "D-120_report.md"
    report.write_text("", encoding="utf-8")
    assert report_validator.validate_report_id(report) is False


# --- D-086: git-state wording detection --------------------------------------

def test_detects_pushed_in_commit_wording():
    text = "Git state: pushed in commit 9a202a5."
    assert report_validator.git_state_wording(text) == ["pushed in commit"]


def test_detects_pushed_in_commit_with_backticked_hash():
    text = "pushed in commit `9a202a5` (just checked)"
    assert report_validator.git_state_wording(text) == ["pushed in commit"]


def test_detects_committed_locally_not_pushed_wording():
    text = "committed locally, not pushed"
    assert report_validator.git_state_wording(text) == ["committed locally, not pushed"]


def test_detects_committed_locally_with_hash_and_commit_msg_between():
    text = (
        "committed locally as `13d68a3` (\"cli: add logs command\"), "
        "**not pushed**"
    )
    assert report_validator.git_state_wording(text) == ["committed locally, not pushed"]


def test_bare_committed_locally_is_not_accepted_as_state():
    # No "not pushed" -> ambiguous (could be pushed): must NOT count.
    assert report_validator.git_state_wording("committed locally.") == []


def test_committed_locally_and_not_pushed_far_apart_are_not_joined():
    # Same tokens but 300+ chars apart (different sections) must NOT count
    # as a state statement — bounded proximity prevents that inference.
    text = (
        "committed locally. " + "x" * 300 + " not pushed."
    )
    assert report_validator.git_state_wording(text) == []


def test_detects_working_tree_only_no_commit_wording():
    text = "working tree only, no commit"
    assert report_validator.git_state_wording(text) == ["working tree only, no commit"]


def test_detects_working_tree_wording_with_variants():
    assert report_validator.git_state_wording("working tree only, no commit.") == [
        "working tree only, no commit"
    ]
    assert report_validator.git_state_wording("Working tree only, no commit") == [
        "working tree only, no commit"
    ]


def test_wording_matching_is_case_insensitive():
    text = "Pushed in commit ABCD1234"
    assert report_validator.git_state_wording(text) == ["pushed in commit"]


def test_all_three_phrasings_can_be_present_at_once():
    text = (
        "pushed in commit 1111111; committed locally, not pushed; "
        "working tree only, no commit"
    )
    assert report_validator.git_state_wording(text) == [
        "pushed in commit",
        "committed locally, not pushed",
        "working tree only, no commit",
    ]


def test_ambiguous_prose_is_not_inferred_as_git_state():
    # These are NOT the canonical phrasings — the validator must not
    # treat "I pushed it" or a bare "committed" as a state statement.
    for text in (
        "I pushed my changes already.",
        "committed the work.",
        "the change is in a commit.",
        "I'll commit later.",
        "git state: unknown",
        "",
    ):
        assert report_validator.git_state_wording(text) == [], repr(text)


def test_git_state_wording_never_runs_git(monkeypatch):
    # Pure text analysis: if git_state_wording ever tried to verify state
    # by shelling out, subprocess.run would be hit and this fails loudly.
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError("git_state_wording must not run subprocesses")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    assert report_validator.git_state_wording("working tree only, no commit") == [
        "working tree only, no commit"
    ]
    assert report_validator.git_state_wording("vague prose here") == []


# --- D-121: duplicate required section headings --------------------------------

def test_complete_report_has_no_duplicate_required_headings():
    assert report_validator.duplicate_required_sections(FULL_REPORT) == []


def test_empty_text_has_no_duplicate_headings():
    assert report_validator.duplicate_required_sections("") == []


def test_duplicated_required_heading_is_reported():
    text = FULL_REPORT.replace(
        "## 2. Files changed", "## 2. Files changed\n\n## 2. Files changed", 1
    )
    assert report_validator.duplicate_required_sections(text) == ["Files changed"]


def test_duplicate_makes_report_look_complete_while_still_malformed():
    """The failure mode D-121 guards against: with a duplicated heading,
    missing_sections sees every required section and returns [] — the
    report would *appear* complete. duplicate_required_sections must
    independently flag it."""
    text = FULL_REPORT.replace("## 1. Summary", "## 1. Summary\n\n## 1. Summary", 1)
    assert report_validator.missing_sections(text) == []
    assert report_validator.duplicate_required_sections(text) == ["Summary"]


def test_multiple_duplicates_are_listed_in_protocol_order():
    text = (
        "## 0. Environment\n## 0. Environment\n"
        "## 1. Summary\n"
        "## 2. Files changed\n## 2. Files changed\n"
        "## 3. Tests run\n## 3. Tests run\n"
        "## 4. Failures\n"
        "## 5. Questions/blockers\n"
    )
    assert report_validator.duplicate_required_sections(text) == [
        "Environment",
        "Files changed",
        "Tests run",
    ]


def test_duplicate_heading_level_and_numbering_variants_are_counted():
    text = "## 1. Summary\n### 0. Summary\n"
    assert report_validator.duplicate_required_sections(text) == ["Summary"]


def test_duplicate_detection_is_case_insensitive():
    text = "## 1. Summary\n## 1. SUMMARY\n"
    assert report_validator.duplicate_required_sections(text) == ["Summary"]


def test_duplicate_detection_covers_questions_section_aliases():
    text = "## 5. Questions/blockers\n## 5. Questions & blockers\n"
    assert report_validator.duplicate_required_sections(text) == ["Questions/blockers"]


def test_duplicate_prose_never_counts_as_duplicate_heading():
    text = FULL_REPORT.replace("None.", "Summary, Summary, Summary.", 2)
    assert report_validator.duplicate_required_sections(text) == []