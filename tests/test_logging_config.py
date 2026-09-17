"""
Tests for src/logging_config.py (D-029).

D-007 found data/ai-brain.log was a 0-byte no-op. These tests pin down
the D-029 contract: setup_logging() actually writes formatted lines to
the log file, including the source line number and a per-run correlation
ID that is stable for the whole run.
"""
import logging

from opencode_crack import logging_config


def _reset_root_logger():
    """Detach any handlers the root logger accumulated, so each test starts
    from the same clean slate."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()


def test_setup_logging_writes_lines_to_file(tmp_path, monkeypatch):
    log_file = tmp_path / "ai-brain.log"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_file)
    _reset_root_logger()

    logging_config.setup_logging()

    logging.getLogger("tests.logging_config").info("hello log world")

    text = log_file.read_text(encoding="utf-8")
    assert "hello log world" in text
    assert "tests.logging_config" in text


def test_log_format_includes_lineno(tmp_path, monkeypatch):
    import re

    log_file = tmp_path / "ai-brain.log"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_file)
    _reset_root_logger()

    logging_config.setup_logging()
    logging.getLogger("tests.lineno").info("line-number check")

    text = log_file.read_text(encoding="utf-8")
    # Format is "%(name)s:%(lineno)d [run=...]" — check the shape rather
    # than the exact number so the test doesn't break when lines shift.
    assert re.search(r"tests\.lineno:\d+ \[run=", text)


def test_run_id_stable_across_one_run(tmp_path, monkeypatch):
    log_file = tmp_path / "ai-brain.log"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_file)
    _reset_root_logger()

    logging_config.setup_logging()
    logger = logging.getLogger("tests.runid")
    logger.info("first")
    logger.info("second")

    import re

    text = log_file.read_text(encoding="utf-8")
    run_ids = set(re.findall(r"\[run=([0-9a-f]{8})\]", text))
    assert len(run_ids) == 1  # one stable ID for the whole run


def test_warning_and_error_levels_are_written(tmp_path, monkeypatch):
    log_file = tmp_path / "ai-brain.log"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_file)
    _reset_root_logger()

    logging_config.setup_logging()
    logger = logging.getLogger("tests.levels")
    logger.warning("a warning")
    logger.error("an error")

    text = log_file.read_text(encoding="utf-8")
    assert "[WARNING]" in text
    assert "[ERROR]" in text