"""
Session-context cache for delegated agents (D-150).

Generates a lightweight briefing artifact containing invariant
repository/orchestration context so an agent processing multiple
tasks in one runtime does not repeatedly reload it.
"""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opencode_crack.config import _PROJECT_ROOT
from opencode_crack.orchestrator.task_board import list_tasks, BOARD_PATH

FIND_WORK_PATH = Path(".agent_prompts/find_work.md")
BRIEFING_PATH = _PROJECT_ROOT / ".cache" / "session_briefing.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _get_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def _check_internet() -> bool:
    try:
        socket.setdefaulttimeout(2)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


def _check_push_access() -> bool:
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _queue_snapshot() -> dict[str, Any]:
    tasks = list_tasks()
    open_tasks = [t for t in tasks if t.state.status == "open"]
    priority_order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM-HIGH": 2,
        "MEDIUM": 3,
        "LOW-MEDIUM": 4,
        "LOW": 5,
    }
    open_tasks.sort(key=lambda t: priority_order.get(t.priority, 9))
    return {
        "total": len(tasks),
        "open_count": len(open_tasks),
        "top_open": [
            {"id": t.id, "priority": t.priority, "title": t.title}
            for t in open_tasks[:10]
        ],
    }


def build_briefing() -> dict[str, Any]:
    """Build a fresh session briefing from current repo state."""
    return {
        "protocol_version": "find_work.v4",
        "generated_at": _now(),
        "git_commit": _get_git_commit(),
        "repo_root": str(_PROJECT_ROOT),
        "queue_snapshot": _queue_snapshot(),
        "environment": {
            "os": platform.system(),
            "internet_access": _check_internet(),
            "push_access": _check_push_access(),
        },
        "content_hashes": {
            "protocol": _sha256(_read_text(FIND_WORK_PATH)),
            "task_board": _sha256(_read_text(BOARD_PATH)),
        },
    }


def write_briefing() -> Path:
    """Write a fresh briefing to the ephemeral cache path."""
    BRIEFING_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRIEFING_PATH.write_text(
        json.dumps(build_briefing(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return BRIEFING_PATH


def load_briefing() -> dict[str, Any] | None:
    """Load cached briefing, or None if not present."""
    if not BRIEFING_PATH.exists():
        return None
    return json.loads(BRIEFING_PATH.read_text(encoding="utf-8"))


def refresh_briefing(write: bool = False) -> dict[str, Any]:
    """Load cached briefing and refresh changed state, or build fresh.

    When the underlying protocol or task-board content has changed, the
    full briefing is rebuilt. Otherwise only the timestamp and queue
    snapshot are refreshed in-place.
    """
    cached = load_briefing()
    if cached is None:
        return build_briefing()

    current_protocol_hash = _sha256(_read_text(FIND_WORK_PATH))
    current_board_hash = _sha256(_read_text(BOARD_PATH))

    if (
        cached.get("content_hashes", {}).get("protocol") != current_protocol_hash
        or cached.get("content_hashes", {}).get("task_board") != current_board_hash
    ):
        return build_briefing()

    cached["generated_at"] = _now()
    cached["queue_snapshot"] = _queue_snapshot()
    if write:
        BRIEFING_PATH.write_text(
            json.dumps(cached, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return cached
