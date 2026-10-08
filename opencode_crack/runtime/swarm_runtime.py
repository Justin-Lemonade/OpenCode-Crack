"""OpenCode Swarm subprocess adapter.

This package owns organizational state; OpenCode Swarm owns worker execution.
The two databases remain separate: control.db is this package's control plane;
.swarm/swarm.db is Swarm's runtime state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from opencode_crack.config import SWARM_DB_PATH
from opencode_crack.runtime import control_db


@dataclass(frozen=True)
class SwarmResult:
    swarm_id: str | None
    status: str
    agents: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class SwarmHealth:
    binary: str
    available: bool
    version: str | None = None
    error: str | None = None


_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(text: str) -> str | None:
    match = _VERSION_RE.search(text or "")
    return match.group(0) if match else None


# CN-013: bound on the post-tree-kill reap wait.
# The tree-kill itself closes inherited pipes; this is only a backstop
# so a wedged grandchild can never turn a timeout into an unbounded hang.
_TREE_KILL_REAP_SECONDS = 15


# Terminate the process tree rooted at pid (best effort, tree scope only).
# Windows taskkill with /T enumerates children of the given PID; POSIX
# killpg targets the session created for the child at spawn.
# A missing or invalid pid kills nothing.
def _kill_process_tree(pid):
    if pid is None or pid <= 0:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ['taskkill', '/PID', str(pid), '/T', '/F'],
                capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


# Swarm ID keys that unambiguously identify a swarm in an event record.
# NOTE: generic "id" is deliberately excluded — in event streams it is the
# event ID, not the swarm ID, and using it would reintroduce cross-swarm
# misattribution.
_EVENT_SWARM_ID_KEYS = ("swarmId", "swarm_id", "swarmID")


def _swarm_ids_from_events(event_path) -> list[str]:
    """Return distinct swarm IDs observed in this invocation's event file.

    Only swarm-specific keys are honored. Missing/unreadable files yield [].
    Order of first appearance is preserved for determinism.
    """
    try:
        path = Path(event_path)
    except (TypeError, ValueError):
        return []
    if not path.is_file():
        return []
    seen: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        for key in _EVENT_SWARM_ID_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate and candidate not in seen:
                seen.append(candidate)
                break
    return seen


def _config_swarm_name(config_path) -> str | None:
    """Best-effort swarm name from this invocation's config file.

    Returns None when the config is missing, unreadable, or nameless.
    Never raises — recovery must fail closed to a plain timeout, not crash.
    """
    if config_path is None:
        return None
    try:
        path = Path(config_path)
    except (TypeError, ValueError):
        return None
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    return name if isinstance(name, str) and name else None


class SwarmRuntime:
    """Run OpenCode Swarm through its documented machine-facing CLI."""

    def __init__(
        self,
        binary: str = "swarm",
        db_path: Path = SWARM_DB_PATH,
        cwd: Path | None = None,
        control_db_path: Path | None = None,
    ) -> None:
        self.binary = binary
        self.db_path = Path(db_path)
        self.cwd = Path(cwd) if cwd else Path.cwd()
        # Deliberately separate from db_path: db_path is Swarm's own
        # runtime database (passed to `swarm run --db`), while this is
        # the control plane database that ingest_events()
        # writes into. Defaults to the real CONTROL_DB_PATH; tests that
        # need an isolated control DB pass control_db_path explicitly
        # rather than repurposing db_path, which stays reserved for
        # Swarm's own state per this module's documented db isolation
        # contract (see docstring on run()).
        self._control_db_path_override = Path(control_db_path) if control_db_path else None

    def available(self) -> bool:
        return shutil.which(self.binary) is not None or Path(self.binary).is_file()

    def version(self) -> str | None:
        if not self.available():
            return None
        proc = subprocess.run(
            [self.binary, "--version"], cwd=self.cwd, capture_output=True,
            text=True, timeout=10, check=False,
        )
        if proc.returncode:
            return None
        return _parse_version(proc.stdout or proc.stderr or "")

    def check(self) -> SwarmHealth:
        if not self.available():
            return SwarmHealth(
                binary=self.binary, available=False,
                error=f"OpenCode Swarm executable not found: {self.binary}",
            )
        version = self.version()
        if version is None:
            return SwarmHealth(
                binary=self.binary, available=True,
                error="swarm --version returned no recognizable version",
            )
        return SwarmHealth(binary=self.binary, available=True, version=version)

    def run(
        self,
        config_path: Path,
        *,
        max_concurrent: int | None = None,
        budget_usd: float | None = None,
        timeout_seconds: int = 3600,
        event_path: Path | None = None,
        resume_id: str | None = None,
        server_url: str | None = None,
        cwd: Path | None = None,
    ) -> SwarmResult:
        """Run a swarm and normalize its JSON result/event stream.

        ``--db`` always points at Swarm's own database; it must never be the
        control plane's own DB. The latter is updated only by ``ingest_events``.

        ``cwd``, if given, overrides ``self.cwd`` for this call only (D-290:
        callers that resolved and validated an isolated lane worktree via
        ``worktree_guard`` pass it here so the swarm subprocess -- and
        anything it spawns -- actually launches with that worktree as its
        effective working directory, rather than whatever directory this
        Python process happened to start in).

        Timeout semantics (CN-013): the wait is bounded.
        On expiry the whole process tree rooted at the spawned child is
        terminated, and work already recorded in the runtime database is
        recovered into the result instead of a bare timeout. Recovery is
        correlated to THIS invocation (resume ID, then this run's event
        file, then unambiguous single-candidate/config-name match) and
        never falls back to "latest completed swarm", so a concurrent
        swarm's result cannot be reported as our own.
        """
        effective_cwd = cwd if cwd is not None else self.cwd
        if not self.available():
            return SwarmResult(
                None, "unavailable",
                error=f"OpenCode Swarm executable not found: {self.binary}",
            )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        event_path = event_path or (self.db_path.parent / "events.jsonl")
        command = [
            self.binary, "run", str(config_path),
            "--json", "--events", str(event_path), "--db", str(self.db_path),
        ]
        if max_concurrent is not None:
            command.extend(["--max-concurrent", str(max_concurrent)])
        if budget_usd is not None:
            command.extend(["--budget-usd", str(budget_usd)])
        if resume_id:
            command.extend(["--resume", resume_id])
        if server_url:
            command.extend(["--server", server_url])
        try:
            proc = subprocess.Popen(
                command, cwd=effective_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session= (os.name != "nt"),
            )
        except OSError as exc:
            return SwarmResult(None, "error", error=str(exc))
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return self._handle_timeout(
                proc, event_path, timeout_seconds,
                resume_id=resume_id, config_path=config_path,
            )
        raw = _parse_json(stdout)
        events = list(_read_jsonl(event_path))
        swarm_id = raw.get("swarmId") or raw.get("swarm_id") or raw.get("id")
        status = raw.get("status") or ("completed" if proc.returncode == 0 else "failed")
        error = None if proc.returncode == 0 else (
            stderr.strip() or raw.get("error") or "swarm exited non-zero"
        )
        agents = raw.get("agents", [])
        return SwarmResult(
            swarm_id, status,
            agents if isinstance(agents, list) else [],
            events, raw, error,
        )

    def ingest_events(self, result: SwarmResult) -> int:
        """Mirror runtime events into this control plane idempotently."""
        count = 0
        for event in result.events:
            agent = event.get("agent") or event.get("agentName") or event.get("agent_id")
            event_type = event.get("type") or event.get("event") or "unknown"
            source_event_id = event.get("id") or event.get("eventId") or event.get("event_id")
            session_id = event.get("sessionId") or event.get("session_id")
            task_id = event.get("taskId") or event.get("task_id")
            payload = dict(event)
            if result.swarm_id:
                payload.setdefault("swarm_id", result.swarm_id)
            if source_event_id:
                payload["source_event_id"] = source_event_id
            full_type = f"swarm:{event_type}"
            if source_event_id and control_db.event_exists(full_type, source_event_id, db_path=self._control_db_path()):
                continue
            control_db._append_event(
                event_type=full_type,
                agent_id=agent,
                session_id=session_id,
                task_id=task_id,
                payload=payload,
                db_path=self._control_db_path(),
            )
            count += 1
            if event_type == "agent-settled" and session_id:
                control_db.mark_session_terminal(session_id, db_path=self._control_db_path())
        return count

    def _control_db_path(self) -> Path:
        if self._control_db_path_override is not None:
            return self._control_db_path_override
        from opencode_crack.config import CONTROL_DB_PATH
        return CONTROL_DB_PATH


def _parse_json(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"error": text.strip()}
    return value if isinstance(value, dict) else {"result": value}


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
