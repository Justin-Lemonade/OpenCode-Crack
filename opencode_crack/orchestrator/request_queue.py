"""GitHub-editable task-board request queue.

Requests are declarative YAML files. A trusted execution environment (the
GitHub Actions workflow in `.github/workflows/orchestrator-requests.yml`, or
a local operator) calls :func:`process_inbox` to apply them through the same
`task_board` functions used by the CLI.

The queue is deliberately not a second state store: `orchestration/tasks.yaml`
remains generated board state. This module is only an adapter that turns a
reviewable file change into an existing audited transition.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from opencode_crack.orchestrator import task_board

INBOX = Path("orchestration/requests/inbox")
PROCESSING = Path("orchestration/requests/processing")
PROCESSED = Path("orchestration/requests/processed")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TASK_RE = re.compile(r"^[HDC]-\d{3}$")
_OPERATIONS = {"claim", "start", "submit", "approve", "reject", "complete", "release", "block"}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _commit_paths(paths: list[Path], message: str) -> None:
    result = _git("add", *(str(p) for p in paths))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git add failed")
    result = _git("commit", "-m", message)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
    result = _git("push")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git push failed")


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError("request must be a YAML mapping")
    return value


def validate(request: dict[str, Any]) -> None:
    required = ("id", "operation", "task_id", "actor")
    missing = [key for key in required if not str(request.get(key, "")).strip()]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    request_id = str(request["id"])
    if not _ID_RE.fullmatch(request_id):
        raise ValueError("id may contain only letters, numbers, '.', '_' and '-'")
    operation = str(request["operation"]).lower()
    if operation not in _OPERATIONS:
        raise ValueError(f"unsupported operation: {operation!r}")
    task_id = str(request["task_id"]).upper()
    if not _TASK_RE.fullmatch(task_id):
        raise ValueError(f"invalid task_id: {task_id!r}")
    if operation in {"reject", "block"} and not str(request.get("notes", "")).strip():
        raise ValueError(f"{operation} requires notes")
    if operation in {"claim", "start", "submit"} and not str(request.get("agent", "")).strip():
        raise ValueError(f"{operation} requires agent")
    if operation == "release" and not str(request.get("agent", "")).strip() and not request.get("override"):
        raise ValueError("release requires agent unless override=true")
    if operation == "submit" and request.get("report_path"):
        report_path = Path(str(request["report_path"]))
        if report_path.is_absolute() or ".." in report_path.parts:
            raise ValueError("report_path must stay inside the repository")


def _notes(request: dict[str, Any]) -> str:
    notes = str(request.get("notes", "")).strip()
    actor = str(request["actor"]).strip()
    session = str(request.get("actor_session", "")).strip()
    attribution = f"[request actor={actor}"
    if session:
        attribution += f" session={session}"
    attribution += "]"
    return f"{attribution} {notes}".strip()


def apply(request: dict[str, Any]):
    """Apply one validated request through the existing task-board API."""
    validate(request)
    operation = str(request["operation"]).lower()
    task_id = str(request["task_id"]).upper()
    notes = _notes(request)
    agent = str(request.get("agent", "")).strip() or None
    report_path = str(request.get("report_path", "")).strip() or None
    override = bool(request.get("override", False))

    if operation == "claim":
        return task_board.claim_task(task_id, agent)  # type: ignore[arg-type]
    if operation == "start":
        return task_board.start_task(task_id, agent=agent)
    if operation == "submit":
        return task_board.submit_for_review(task_id, notes, report_path=report_path, agent=agent)
    if operation == "approve":
        return task_board.approve_task(task_id, notes=notes)
    if operation == "reject":
        return task_board.reject_task(task_id, notes=notes)
    if operation == "complete":
        return task_board.complete_task(task_id, notes=notes)
    if operation == "release":
        return task_board.release_task(task_id, notes=notes, agent=agent, override=override)
    if operation == "block":
        return task_board.block_task(task_id, notes=notes, agent=agent)
    raise AssertionError(operation)


def _reserve(path: Path) -> Path:
    """Move a request out of the inbox before applying it.

    The reservation commit is pushed with the workflow token. Because
    GITHUB_TOKEN-originated pushes do not recursively trigger workflows,
    this is safe from duplicate processing. A failed request remains in
    `processing/` for explicit inspection/requeue rather than being retried
    blindly.
    """
    PROCESSING.mkdir(parents=True, exist_ok=True)
    target = PROCESSING / path.name
    path.rename(target)
    _commit_paths([path, target], f"orchestrator: reserve request {path.stem}")
    return target


def _archive(path: Path, request: dict[str, Any]) -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    target = PROCESSED / path.name
    path.rename(target)
    result_path = PROCESSED / f"{path.stem}.result.yaml"
    task = task_board.get_task(str(request["task_id"]).upper())
    result = {
        "request_id": request["id"],
        "operation": request["operation"],
        "task_id": request["task_id"],
        "actor": request["actor"],
        "result": "applied",
        "final_status": task.state.status if task else None,
    }
    result_path.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
    _commit_paths([path, target, result_path], f"orchestrator: archive request {path.stem}")


def process_inbox() -> int:
    """Process all inbox requests sequentially. Returns the number applied."""
    INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX.glob("*.yaml"))
    applied = 0
    for path in files:
        request = _load(path)
        validate(request)
        reserved = _reserve(path)
        try:
            apply(request)
        except Exception:
            # Keep the reserved request visible. The workflow will fail and
            # the operator can inspect/requeue it after fixing the cause.
            raise
        _archive(reserved, request)
        applied += 1
    return applied


if __name__ == "__main__":
    count = process_inbox()
    print(f"Applied {count} orchestrator request(s).")
