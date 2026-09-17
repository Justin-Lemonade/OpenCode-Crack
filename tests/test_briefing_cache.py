"""
Tests for src/orchestrator/briefing_cache.py (D-150).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opencode_crack.orchestrator import briefing_cache, task_board, task_parser
from opencode_crack.orchestrator import status_report

FIXTURE_ROADMAP = """\
# Fixture Roadmap

## D-001 — Run the tests

**Priority: HIGH.**

Do the thing.

---
"""


@pytest.fixture
def fixture_roadmap(tmp_path, monkeypatch):
    roadmap_path = tmp_path / "iteration improvement ideas.md"
    roadmap_path.write_text(FIXTURE_ROADMAP, encoding="utf-8")
    monkeypatch.setattr(task_parser, "ROADMAP_PATH", roadmap_path)
    monkeypatch.setattr(task_parser, "DELEGATED_TASKS_DIR", tmp_path / "nonexistent")
    return roadmap_path


@pytest.fixture
def fixture_board(tmp_path, fixture_roadmap, monkeypatch):
    board_path = tmp_path / "orchestration" / "tasks.yaml"
    monkeypatch.setattr(task_board, "BOARD_PATH", board_path)
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(task_board, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(task_board, "ACTIVITY_LOG", reports_dir / "activity.log")
    monkeypatch.setattr(status_report, "STATUS_PATH", tmp_path / "STATUS.md")
    return board_path


@pytest.fixture
def no_op_git(monkeypatch):
    def fake_run_git(*args):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(task_board, "_run_git", fake_run_git)


@pytest.fixture
def briefing_cache_path(tmp_path, monkeypatch):
    cache_path = tmp_path / ".cache" / "session_briefing.json"
    monkeypatch.setattr(briefing_cache, "BRIEFING_PATH", cache_path)
    return cache_path


@pytest.fixture
def briefing_fixture_files(tmp_path, monkeypatch):
    proto_path = tmp_path / "find_work.md"
    proto_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(briefing_cache, "FIND_WORK_PATH", proto_path)
    return proto_path


def test_build_briefing_contains_required_fields(
    fixture_board, no_op_git, briefing_cache_path, briefing_fixture_files, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    briefing = briefing_cache.build_briefing()

    assert briefing["protocol_version"] == "find_work.v4"
    assert briefing["git_commit"] == "abc123"
    assert briefing["repo_root"] == str(briefing_cache._PROJECT_ROOT)
    assert "queue_snapshot" in briefing
    assert "environment" in briefing
    assert "content_hashes" in briefing

    env = briefing["environment"]
    assert env["os"] in ("Windows", "Linux", "Darwin")
    assert env["internet_access"] is True
    assert env["push_access"] is True

    assert briefing["content_hashes"]["protocol"] == briefing_cache._sha256("")
    assert len(briefing["content_hashes"]["task_board"]) == 12


def test_write_and_load_briefing(
    fixture_board, no_op_git, briefing_cache_path, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    path = briefing_cache.write_briefing()
    assert path.exists()
    assert path.parent.name == ".cache"

    loaded = briefing_cache.load_briefing()
    assert loaded is not None
    assert loaded["git_commit"] == "abc123"
    assert loaded["protocol_version"] == "find_work.v4"


def test_refresh_rebuilds_when_protocol_changes(
    fixture_board, no_op_git, briefing_cache_path, tmp_path, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    proto_path = tmp_path / "find_work.md"
    proto_path.write_text("v1 protocol", encoding="utf-8")
    monkeypatch.setattr(briefing_cache, "FIND_WORK_PATH", proto_path)

    briefing_cache.write_briefing()
    original = briefing_cache.load_briefing()
    original_protocol_hash = original["content_hashes"]["protocol"]

    proto_path.write_text("v2 protocol changed", encoding="utf-8")
    refreshed = briefing_cache.refresh_briefing()

    assert refreshed["content_hashes"]["protocol"] != original_protocol_hash
    assert refreshed["content_hashes"]["protocol"] == briefing_cache._sha256(
        "v2 protocol changed"
    )
    assert refreshed["git_commit"] == "abc123"


def test_refresh_rebuilds_when_task_board_changes(
    fixture_board, no_op_git, briefing_cache_path, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    briefing_cache.write_briefing()
    original = briefing_cache.load_briefing()
    original_board_hash = original["content_hashes"]["task_board"]

    board_path = briefing_cache.BOARD_PATH
    original_content = board_path.read_text(encoding="utf-8") if board_path.exists() else ""
    board_path.write_text("changed board content", encoding="utf-8")
    try:
        refreshed = briefing_cache.refresh_briefing()
        assert refreshed["content_hashes"]["task_board"] != original_board_hash
        assert refreshed["content_hashes"]["task_board"] == briefing_cache._sha256(
            "changed board content"
        )
    finally:
        board_path.write_text(original_content, encoding="utf-8")


def test_refresh_preserves_cache_when_nothing_changed(
    fixture_board, no_op_git, briefing_cache_path, briefing_fixture_files, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    times = ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"]
    monkeypatch.setattr(briefing_cache, "_now", lambda: times.pop(0))

    briefing_cache.write_briefing()
    original = briefing_cache.load_briefing()

    refreshed = briefing_cache.refresh_briefing()

    assert refreshed["protocol_version"] == original["protocol_version"]
    assert refreshed["git_commit"] == original["git_commit"]
    assert refreshed["content_hashes"]["protocol"] == original["content_hashes"]["protocol"]
    assert refreshed["content_hashes"]["task_board"] == original["content_hashes"]["task_board"]
    assert refreshed["generated_at"] != original["generated_at"]
    assert refreshed["generated_at"] == "2026-01-01T00:00:01Z"


def test_refresh_writes_back_when_requested(
    fixture_board, no_op_git, briefing_cache_path, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    briefing_cache.write_briefing()
    original_mtime = briefing_cache_path.stat().st_mtime

    briefing_cache.refresh_briefing(write=True)
    new_mtime = briefing_cache_path.stat().st_mtime

    assert new_mtime > original_mtime
    loaded = briefing_cache.load_briefing()
    assert loaded is not None
    assert loaded["git_commit"] == "abc123"


def test_refresh_rebuilds_when_both_hashes_change(
    fixture_board, no_op_git, briefing_cache_path, tmp_path, monkeypatch
):
    monkeypatch.setattr(briefing_cache, "_get_git_commit", lambda: "abc123")
    monkeypatch.setattr(briefing_cache, "_check_internet", lambda: True)
    monkeypatch.setattr(briefing_cache, "_check_push_access", lambda: True)

    proto_path = tmp_path / "find_work.md"
    proto_path.write_text("v1", encoding="utf-8")
    monkeypatch.setattr(briefing_cache, "FIND_WORK_PATH", proto_path)

    briefing_cache.write_briefing()
    original = briefing_cache.load_briefing()

    proto_path.write_text("v2 protocol", encoding="utf-8")
    board_path = briefing_cache.BOARD_PATH
    original_content = board_path.read_text(encoding="utf-8") if board_path.exists() else ""
    board_path.write_text("v2 board", encoding="utf-8")
    try:
        refreshed = briefing_cache.refresh_briefing()
        assert refreshed["content_hashes"]["protocol"] != original["content_hashes"]["protocol"]
        assert refreshed["content_hashes"]["task_board"] != original["content_hashes"]["task_board"]
        assert refreshed["git_commit"] == "abc123"
    finally:
        board_path.write_text(original_content, encoding="utf-8")
