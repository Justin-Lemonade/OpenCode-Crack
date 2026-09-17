"""Tests for the machine-readable compact agent report schema (roadmap D-153).

`report_schema.py` defines a bounded JSON companion to the Markdown report:
required fields (task ID, outcome, files changed, tests, failures, blockers,
confidence, source, git state), enum vocabularies, and hard size limits that
reject oversized free-text instead of silently accepting it. These tests pin
the required fields, the enum values, the size bounds, the write-time
rejection of invalid reports, the git-state vocabulary shared with
report_validator (D-086), and the CLI wiring.
"""
import json
import tempfile
from pathlib import Path

import pytest

from opencode_crack.orchestrator import report_schema, report_validator
from opencode_crack.orchestrator.report_schema import (
    build_report,
    report_json_path,
    validate_report,
    validate_report_file,
    write_report_json,
)


def tmp_reports_dir() -> Path:
    return Path(tempfile.mkdtemp())

VALID = dict(
    task_id="D-153",
    outcome="submitted",
    files_changed=["src/orchestrator/report_schema.py"],
    tests=["tests/test_report_schema.py"],
    failures=[],
    blockers=[],
    confidence="high",
    source="tests",
    git_state="pushed in commit",
)


def test_valid_report_has_no_problems():
    assert validate_report(VALID) == []


# --- required fields ----------------------------------------------------------

def test_all_required_fields_are_declared():
    for field in report_schema.REQUIRED_FIELDS:
        assert field in VALID, f"required field not in sample report: {field}"


def test_missing_required_field_is_reported():
    report = dict(VALID)
    del report["blockers"]
    problems = validate_report(report)
    assert "missing required field: blockers" in problems


def test_every_required_field_is_checked_individually():
    report = {field: "" for field in report_schema.REQUIRED_FIELDS}
    report["task_id"] = ""
    problems = validate_report(report)
    assert problems  # empty strings fail enum/list checks, not just missing


def test_non_dict_input_is_rejected():
    assert validate_report(["not", "a", "dict"]) == ["report must be a JSON object"]


# --- enum vocabularies ---------------------------------------------------------

def test_outcome_enum_is_bounded():
    assert set(report_schema.OUTCOMES) == {"submitted", "blocked", "released"}


def test_confidence_enum_is_bounded():
    assert set(report_schema.CONFIDENCE_LEVELS) == {"high", "medium", "low"}


def test_source_enum_is_bounded():
    assert set(report_schema.SOURCES) == {"tests", "inspection", "manual"}


def test_git_state_vocabulary_matches_report_validator():
    """The JSON git-state values must be exactly the canonical phrasings the
    Markdown validator (D-086) recognizes, so the two forms never disagree."""
    assert set(report_schema.GIT_STATES) == set(report_validator.GIT_STATE_PATTERNS)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("outcome", "complete"),
        ("confidence", "very-high"),
        ("source", "guessed"),
        ("git_state", "committed"),
    ],
)
def test_unknown_enum_value_is_rejected(field, bad_value):
    report = dict(VALID)
    report[field] = bad_value
    problems = validate_report(report)
    assert any(problem.startswith(f"{field} must be one of") for problem in problems)


def test_task_id_shape_is_checked():
    report = dict(VALID)
    report["task_id"] = "D-153-extra"
    assert any(p.startswith("task_id must look like") for p in validate_report(report))


# --- size bounds ---------------------------------------------------------------

def test_list_over_item_cap_is_rejected():
    report = dict(VALID)
    report["tests"] = ["t"] * (report_schema.MAX_LIST_ITEMS + 1)
    problems = validate_report(report)
    assert any("exceeds" in p and "items" in p for p in problems)


def test_oversized_list_item_is_rejected():
    report = dict(VALID)
    report["blockers"] = ["x" * (report_schema.MAX_LIST_ITEM_LENGTH + 1)]
    problems = validate_report(report)
    assert any("exceeds" in p and "chars" in p for p in problems)


def test_oversized_free_text_field_is_rejected():
    """The core D-153 guarantee: unexpectedly huge free-text is rejected by
    validation AND by write time."""
    report = dict(VALID)
    report["blockers"] = ["huge" * (report_schema.MAX_LIST_ITEM_LENGTH // 2)]
    problems = validate_report(report)
    assert problems
    with pytest.raises(ValueError, match="refusing to write invalid report"):
        write_report_json("D-153", report, tmp_reports_dir())


def test_non_string_list_item_is_rejected():
    report = dict(VALID)
    report["files_changed"] = [123]
    problems = validate_report(report)
    assert any("must be strings" in p for p in problems)


def test_non_list_field_shape_is_rejected():
    report = dict(VALID)
    report["tests"] = "tests/test_report_schema.py"
    problems = validate_report(report)
    assert any("must be a list" in p for p in problems)


# --- write / read round trip ---------------------------------------------------

def tmp_reports_dir(tmp_path=None):
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp())


def test_write_report_json_writes_deterministic_file(tmp_path):
    path = write_report_json("D-153", VALID, tmp_path)
    assert path == report_json_path("D-153", tmp_path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == VALID


def test_write_report_json_is_byte_stable(tmp_path):
    first = write_report_json("D-153", VALID, tmp_path).read_text(encoding="utf-8")
    second = write_report_json("D-153", VALID, tmp_path).read_text(encoding="utf-8")
    assert first == second


def test_validate_report_file_round_trip(tmp_path):
    write_report_json("D-153", VALID, tmp_path)
    assert validate_report_file(report_json_path("D-153", tmp_path)) == []


def test_validate_report_file_rejects_invalid_file(tmp_path):
    bad = tmp_path / "D-999_report.json"
    bad.write_text(json.dumps({"task_id": "D-999"}), encoding="utf-8")
    problems = validate_report_file(bad)
    assert any("missing required field" in p for p in problems)


def test_validate_report_file_missing_file():
    assert validate_report_file("reports/DOES-NOT-EXIST_report.json")


def test_validate_report_file_unreadable_json(tmp_path):
    bad = tmp_path / "D-999_report.json"
    bad.write_text("{not json", encoding="utf-8")
    assert validate_report_file(bad)


# --- CLI wiring ---------------------------------------------------------------

@pytest.fixture
def fixture_roadmap(tmp_path, monkeypatch):
    from opencode_crack.orchestrator import task_parser

    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(
        "# Fixture Roadmap\n\n## D-153 — Machine-readable report schema\n\n"
        "**Priority: HIGH.**\n\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "no_delegated")
    return roadmap_path


@pytest.fixture
def fixture_board(tmp_path, fixture_roadmap, monkeypatch):
    from opencode_crack.orchestrator import task_board
    from opencode_crack.orchestrator import status_report

    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")
    return board_path


def test_cli_report_json_writes_and_checks(tmp_path, fixture_board, monkeypatch, capsys):
    from opencode_crack.orchestrator import report_schema
    from opencode_crack.orchestrator.cli import run_orchestrate

    monkeypatch.setattr(report_schema, "report_json_path",
                        lambda tid, reports_dir=tmp_path: tmp_path / f"{tid}_report.json")
    run_orchestrate(["report-json", "D-153", "--files", "src/a.py",
                     "--source", "tests", "--git-state", "pushed in commit"])
    out = capsys.readouterr().out
    assert f"Wrote {tmp_path / 'D-153_report.json'}" in out
    run_orchestrate(["report-json", "D-153", "--check"])
    assert "schema-valid" in capsys.readouterr().out


def test_cli_report_json_unknown_task(fixture_board, capsys):
    from opencode_crack.orchestrator.cli import run_orchestrate

    with pytest.raises(SystemExit) as exc:
        run_orchestrate(["report-json", "Z-999"])
    assert exc.value.code == 1
    assert "No such task" in capsys.readouterr().out
