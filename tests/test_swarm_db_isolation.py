"""Swarm DB isolation/configuration regression tests (roadmap D-145).

Proves AI-Brain can configure a dedicated OpenCode Swarm runtime DB, that the
default DB path is the explicit Swarm config default, and that the runtime
adapter does not silently redirect Swarm state into the AI-Brain control DB.

No Bun/OpenCode is executed: ``subprocess.run`` is mocked and tests assert
on the constructed command line instead.
"""
import subprocess

from opencode_crack.config import SWARM_DB_PATH
from opencode_crack.runtime.swarm_runtime import SwarmRuntime


def _completed(stdout="{}", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _install_fakes(monkeypatch, captured):
    monkeypatch.setattr("opencode_crack.runtime.swarm_runtime.shutil.which", lambda _: "/fake/bin/swarm")

    def fake_run(args, **kwargs):
        captured["cmd"] = list(args)
        captured["kwargs"] = kwargs
        return _completed()

    monkeypatch.setattr("opencode_crack.runtime.swarm_runtime.subprocess.run", fake_run)


def test_external_swarm_db_path_propagated_to_command(monkeypatch, tmp_path):
    captured = {}
    _install_fakes(monkeypatch, captured)
    db_path = tmp_path / ".swarm" / "swarm.db"

    SwarmRuntime(binary="swarm", db_path=db_path).run(tmp_path / "config.json")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db") + 1] == str(db_path)
    assert cmd[cmd.index("--events") + 1] == str(tmp_path / ".swarm" / "events.jsonl")


def test_external_swarm_db_path_is_not_rebased_to_control_db(monkeypatch, tmp_path):
    captured = {}
    _install_fakes(monkeypatch, captured)
    db_path = tmp_path / ".swarm" / "swarm.db"

    SwarmRuntime(binary="swarm", db_path=db_path).run(tmp_path / "config.json")

    cmd = captured["cmd"]
    assert str(db_path) in cmd
    assert str(SWARM_DB_PATH) not in cmd


def test_configured_db_does_not_create_unexpected_project_level_runtime_files(monkeypatch, tmp_path):
    captured = {}
    _install_fakes(monkeypatch, captured)
    db_path = tmp_path / ".swarm" / "swarm.db"

    SwarmRuntime(binary="swarm", db_path=db_path).run(tmp_path / "config.json")

    # The adapter constructs paths for Swarm; it must not create a second DB
    # or event stream beside the project config.
    assert not (tmp_path / "swarm.db").exists()
    assert not (tmp_path / "swarm-events.jsonl").exists()
    assert captured["cmd"][captured["cmd"].index("--db") + 1] == str(db_path)


def test_default_db_path_is_configured_swarm_db_path():
    assert SwarmRuntime(binary="swarm").db_path == SWARM_DB_PATH


def test_default_command_uses_configured_swarm_db(monkeypatch, tmp_path):
    captured = {}
    _install_fakes(monkeypatch, captured)

    SwarmRuntime(binary="swarm").run(tmp_path / "config.json", event_path=tmp_path / "events.jsonl")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db") + 1] == str(SWARM_DB_PATH)
    assert cmd[cmd.index("--events") + 1] == str(tmp_path / "events.jsonl")


def test_default_events_sits_beside_default_swarm_db(monkeypatch):
    captured = {}
    _install_fakes(monkeypatch, captured)

    SwarmRuntime(binary="swarm").run("config.json")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--events") + 1] == str(SWARM_DB_PATH.parent / "events.jsonl")


def test_gitignore_ignores_swarm_runtime_state():
    from pathlib import Path

    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert ".swarm/" in ignore
    assert "data/control.db" in ignore
    assert "*.db" in ignore
