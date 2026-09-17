"""
Tests for src/orchestrator/dispatcher.py (roadmap D-041).

Covers dispatch_manual(), dispatch_ollama() failure/success paths,
dispatch() backend routing (manual, ollama, swarm), and build_contract() key-field presence
(including the graceful fallback when a task's roadmap section can't be
read). No real network, git, or Ollama calls: CONTRACTS_DIR is pointed at
tmp_path and urllib is monkeypatched.
"""
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from opencode_crack.orchestrator import dispatcher
from opencode_crack.orchestrator.task_board import Task


@pytest.fixture(autouse=True)
def _hermetic_durable_knowledge(tmp_path, monkeypatch):
    """Keep durable-knowledge reads/writes off the real repo tree.

    Contract building retrieves from the real knowledge/ dir by default and
    dispatch records retrieval into orchestration/durable_knowledge.yaml —
    both must stay hermetic under tests."""
    from opencode_crack.orchestrator import durable_knowledge as dk
    monkeypatch.setattr(dk, "DURABLE_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(dk, "SIDECAR_PATH", tmp_path / "orchestration" / "durable_knowledge.yaml")


def make_task(task_id="D-999", title="Fake delegatable task", priority="MEDIUM"):
    return Task(id=task_id, tier="delegate", title=title, priority=priority)


def _section_for(task, body="The full task section text."):
    """Mimic a real roadmap section: heading carries title, **Priority:**
    line carries priority, body is the full embedded text."""
    return (
        f"## {task.id} — {task.title}\n\n"
        f"**Priority: {task.priority}.**\n\n"
        f"{body}"
    )


def _patch_contract_section(monkeypatch, task, body="The full task section text."):
    monkeypatch.setattr(
        "opencode_crack.orchestrator.task_parser.get_section_text",
        lambda _task_id: _section_for(task, body),
    )


# --- dispatch_manual --------------------------------------------------------


def test_dispatch_manual_writes_contract_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "CONTRACTS_DIR", tmp_path)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    out_path = dispatcher.dispatch_manual(task)

    assert isinstance(out_path, Path)
    assert out_path == tmp_path / "D-999.md"
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "Fake delegatable task" in text
    assert "D-999" in text
    assert "MEDIUM" in text
    assert "The full task section text." in text


def test_dispatch_manual_does_not_touch_network(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "CONTRACTS_DIR", tmp_path)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    def _boom(*a, **kw):
        raise AssertionError("dispatch_manual must not make network calls")

    monkeypatch.setattr(dispatcher.urllib.request, "urlopen", _boom)
    dispatcher.dispatch_manual(make_task())


# --- dispatch() routing -----------------------------------------------------


def test_dispatch_manual_backend_returns_path_message(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "CONTRACTS_DIR", tmp_path)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    result = dispatcher.dispatch(make_task(), backend="manual")

    assert result.startswith("Contract written to")
    assert "D-999.md" in result


def test_dispatch_unknown_backend_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "CONTRACTS_DIR", tmp_path)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    with pytest.raises(ValueError, match="Unknown backend"):
        dispatcher.dispatch(make_task(), backend="bogus")


def test_dispatch_swarm_backend_requires_manager_worker_tester(monkeypatch):
    task = make_task()
    with pytest.raises(ValueError, match="swarm backend requires: --manager-id, --worker-id, --tester-id"):
        dispatcher.dispatch(task, backend="swarm")


def test_dispatch_swarm_backend_with_all_args(monkeypatch):
    task = make_task()
    mock_dispatch_swarm = MagicMock(return_value=MagicMock(error=None))
    monkeypatch.setattr(dispatcher, "dispatch_swarm", mock_dispatch_swarm)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    dispatcher.dispatch(task, backend="swarm", manager_id="mgr", worker_id="wrk", tester_id="tst")

    mock_dispatch_swarm.assert_called_once()
    call_kwargs = mock_dispatch_swarm.call_args.kwargs
    assert call_kwargs["manager_id"] == "mgr"
    assert call_kwargs["worker_id"] == "wrk"
    assert call_kwargs["tester_id"] == "tst"


def test_dispatch_opencode_backend_alias(monkeypatch):
    task = make_task()
    mock_dispatch_swarm = MagicMock(return_value=MagicMock(error=None))
    monkeypatch.setattr(dispatcher, "dispatch_swarm", mock_dispatch_swarm)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    dispatcher.dispatch(task, backend="opencode", manager_id="mgr", worker_id="wrk", tester_id="tst")

    mock_dispatch_swarm.assert_called_once()


# --- dispatch_ollama --------------------------------------------------------


def test_dispatch_ollama_unreachable_raises_clear_runtime_error(monkeypatch):
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    def _refuse(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(dispatcher.urllib.request, "urlopen", _refuse)

    with pytest.raises(RuntimeError, match="Could not reach Ollama"):
        dispatcher.dispatch_ollama(task)


def test_dispatch_ollama_returns_response_text(monkeypatch):
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"response": "delegation done"}).encode("utf-8")

    def _ok(*a, **kw):
        return _FakeResp()

    monkeypatch.setattr(dispatcher.urllib.request, "urlopen", _ok)

    result = dispatcher.dispatch_ollama(task)
    assert result == "delegation done"


def test_dispatch_ollama_backend_routes_to_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "CONTRACTS_DIR", tmp_path)
    task = make_task()
    _patch_contract_section(monkeypatch, task)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"response": "ok"}).encode("utf-8")

    monkeypatch.setattr(dispatcher.urllib.request, "urlopen", lambda *a, **kw: _FakeResp())

    result = dispatcher.dispatch(make_task(), backend="ollama")
    assert result == "ok"


# --- build_contract key fields ----------------------------------------------


def test_contract_includes_task_id_title_priority_and_section(monkeypatch):
    task = make_task(task_id="D-123", title="Some delegatable thing", priority="HIGH")
    _patch_contract_section(monkeypatch, task)
    contract = dispatcher.build_contract(task)
    assert "D-123" in contract
    assert "Some delegatable thing" in contract
    assert "HIGH" in contract
    assert "The full task section text." in contract


def test_contract_falls_back_when_section_unavailable(monkeypatch):
    def _raise(_task_id):
        raise ValueError("no such task")

    monkeypatch.setattr("opencode_crack.orchestrator.task_parser.get_section_text", _raise)
    contract = dispatcher.build_contract(make_task())
    assert "D-999" in contract
    assert "Section text unavailable" in contract