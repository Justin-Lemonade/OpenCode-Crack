"""Shared CLI output layer tests (roadmap D-186).

These pin the representative output contracts every command shares:
stable headings, cross-platform path rendering, real pluralization, the
canonical status-line shape, and the single error/exit semantics.
"""
import logging

import pytest

from opencode_crack.cli_format import (
    count_str,
    display_path,
    error_and_exit,
    heading,
    status_line,
)


# --- headings ----------------------------------------------------------------

def test_heading_has_stable_marks():
    assert heading("Folder intake") == "== Folder intake =="


# --- paths -------------------------------------------------------------------

def test_display_path_uses_forward_slashes_on_every_os(tmp_path):
    nested = tmp_path / "sub" / "note.txt"
    rendered = display_path(nested)
    assert "\\" not in rendered
    assert rendered.endswith("sub/note.txt")


def test_display_path_accepts_strings():
    assert display_path("data/intake") == "data/intake"


# --- counts ------------------------------------------------------------------

@pytest.mark.parametrize(
    "n,singular,plural,expected",
    [
        (1, "file", None, "1 file"),
        (3, "file", None, "3 files"),
        (0, "chunk", None, "0 chunks"),
        (2, "more unchanged file", "more unchanged files", "2 more unchanged files"),
        (1, "more unchanged file", "more unchanged files", "1 more unchanged file"),
    ],
)
def test_count_str_pluralizes(n, singular, plural, expected):
    assert count_str(n, singular, plural) == expected


# --- status lines ------------------------------------------------------------

def test_status_line_ok_with_detail():
    assert status_line("python_version", True, "3.14.6 (need >= 3.10)") == (
        "python_version: OK - 3.14.6 (need >= 3.10)"
    )


def test_status_line_fail_with_detail():
    assert status_line("db_health", False, "unreachable") == "db_health: FAIL - unreachable"


def test_status_line_without_detail_omits_dash():
    assert status_line("llm_backend_available", True) == "llm_backend_available: OK"


# --- error / exit semantics ----------------------------------------------------

def test_error_and_exit_logs_and_exits_1(caplog):
    with pytest.raises(SystemExit) as excinfo:
        error_and_exit("No such folder: docs")
    assert excinfo.value.code == 1
    assert any("No such folder: docs" in r.message for r in caplog.records)


def test_error_and_exit_honors_explicit_code(caplog):
    with pytest.raises(SystemExit) as excinfo:
        error_and_exit("fatal", code=2)
    assert excinfo.value.code == 2
    assert caplog.records[-1].levelno == logging.ERROR
