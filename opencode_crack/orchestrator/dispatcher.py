"""Execute delegation contracts against local agent backends.

The ``opencode``/``swarm`` backend is local-only: it resolves registered
manager/worker/tester profiles and sends the claimed task through the real
ManagerLoop -> OpenCode Swarm path.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from opencode_crack.orchestrator.delegator import build_contract
from opencode_crack.orchestrator.task_board import Task

log = logging.getLogger(__name__)
OLLAMA_URL = "http://localhost:11434/api/generate"
CONTRACTS_DIR = Path("orchestration/contracts")


def _record_handoff(task: Task) -> None:
    """USE step: a contract for ``task`` was handed to a worker backend —
    record retrieval for the injected entries (sidecar only, no git)."""
    try:
        from opencode_crack.orchestrator.delegator import _get_relevant_knowledge_for_task
        from opencode_crack.orchestrator import durable_knowledge as dk
        for scored in _get_relevant_knowledge_for_task(task):
            dk.record_entry_retrieval(scored.entry.id)
    except Exception:
        pass


def dispatch_manual(task: Task) -> Path:
    """Write the contract to a file the user can paste into an assistant."""
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONTRACTS_DIR / f"{task.id}.md"
    out_path.write_text(build_contract(task), encoding="utf-8")
    _record_handoff(task)
    log.info("Contract written to %s", out_path)
    return out_path


def dispatch_ollama(task: Task, model: str = "llama3.1") -> str:
    """POST the contract to a local Ollama server."""
    payload = json.dumps({"model": model, "prompt": build_contract(task), "stream": False}).encode("utf-8")
    _record_handoff(task)
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("response", "")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_URL} ({exc}).") from exc


def dispatch_swarm(
    task: Task,
    *,
    manager_id: str,
    worker_id: str,
    tester_id: str,
    timeout_seconds: int = 3600,
    max_concurrent: int = 3,
    budget_usd: float | None = None,
):
    """Claim an open task if necessary, then run it through the real swarm loop."""
    from opencode_crack.orchestrator import task_board
    from opencode_crack.orchestrator.manager_loop import ManagerLoop
    from opencode_crack.runtime import control_db

    profiles = [control_db.get_agent(agent_id) for agent_id in (manager_id, worker_id, tester_id)]
    if any(profile is None for profile in profiles):
        missing = [agent_id for agent_id, profile in zip((manager_id, worker_id, tester_id), profiles) if profile is None]
        raise RuntimeError(f"Unregistered runtime agent(s): {', '.join(missing)}")

    current = task_board.get_task(task.id)
    if current is None:
        raise RuntimeError(f"Task disappeared from board: {task.id}")
    if current.state.status in {"open", "blocked"}:
        current = task_board.claim_task(task.id, worker_id)
    if current.state.assignee != worker_id:
        raise RuntimeError(f"Task {task.id} is owned by {current.state.assignee or 'another agent'}")
    if current.state.status == "claimed":
        task_board.start_task(task.id, worker_id)

    result = ManagerLoop().run_once(
        task.id, profiles[0], profiles[1], profiles[2],
        timeout_seconds=timeout_seconds,
        max_concurrent=max_concurrent,
        budget_usd=budget_usd,
    )
    if result.error:
        raise RuntimeError(result.error)
    return result


def dispatch(task: Task, backend: str = "manual", model: str = "llama3.1", **kwargs):
    if backend == "manual":
        path = dispatch_manual(task)
        return f"Contract written to {path} — paste it into your chosen model."
    if backend == "ollama":
        return dispatch_ollama(task, model=model)
    if backend in {"opencode", "swarm"}:
        required = ("manager_id", "worker_id", "tester_id")
        missing = [name for name in required if not kwargs.get(name)]
        if missing:
            raise ValueError(f"{backend} backend requires: {', '.join('--' + n.replace('_', '-') for n in missing)}")
        return dispatch_swarm(
            task, manager_id=kwargs["manager_id"], worker_id=kwargs["worker_id"], tester_id=kwargs["tester_id"],
            timeout_seconds=kwargs.get("timeout_seconds", 3600),
            max_concurrent=kwargs.get("max_concurrent", 3), budget_usd=kwargs.get("budget_usd"),
        )
    raise ValueError(f"Unknown backend: {backend!r} (use 'manual', 'ollama', 'opencode', or 'swarm')")
