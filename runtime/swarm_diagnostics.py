"""
Read-only Swarm + runtime diagnostics (roadmap D-165).

A single deterministic snapshot of OpenCode Swarm health and AI-Brain
runtime state, built entirely from existing helpers:

  * ``SwarmRuntime.check()``   — swarm executable availability + version;
  * ``AgentRuntime.is_server_healthy()`` — configured OpenCode endpoint
    health (never exposes credentials);
  * ``control_db``             — registered agents, active sessions, recent
    swarm lifecycle events.

The collector never starts/stops services, never mutates runtime state, and
never raises a traceback when a runtime service is missing — missing
services become explicit diagnostic fields instead. The checker callables
are injectable so tests exercise the exact snapshot shape without a live
swarm binary or OpenCode server.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src.config import CONTROL_DB_PATH, OPENCODE_BASE_URL
from src.runtime import control_db


def collect_swarm_status(
    db_path: Path = CONTROL_DB_PATH,
    swarm_checker: Optional[Callable[[], Any]] = None,
    opencode_checker: Optional[Callable[[], bool]] = None,
    event_limit: int = 10,
) -> dict[str, Any]:
    """Read-only diagnostic snapshot of swarm + runtime state.

    ``swarm_checker`` returns a ``SwarmHealth`` (default:
    ``SwarmRuntime().check``); ``opencode_checker`` returns bool (default:
    ``AgentRuntime().is_server_healthy``). Both default to the real
    read-only helpers and are injectable for deterministic tests. Missing
    services are reported as structured fields, never as exceptions.
    """
    if swarm_checker is None:
        from src.runtime.swarm_runtime import SwarmRuntime
        swarm_checker = SwarmRuntime().check
    if opencode_checker is None:
        from src.runtime.agent_runtime import AgentRuntime
        opencode_checker = AgentRuntime().is_server_healthy

    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "swarm_executable": _swarm_block(swarm_checker),
        "opencode_endpoint": _opencode_block(opencode_checker),
        "agents": _agents_block(db_path),
        "active_sessions": control_db.list_active_sessions(db_path=db_path),
        "recent_swarm_events": _recent_swarm_events(db_path, event_limit),
        "stuck_settlements": _stuck_settlements_block(),
    }
    return report


# Settlement states the opencode-swarm plugin treats as terminal/safe.
# Anything else past the staleness window is flagged, not touched.
_SETTLEMENT_TERMINAL_STATES = frozenset({
    "COMPLETED", "REJECTED", "RECOVERED", "ABANDONED",
})
_SETTLEMENT_STALE_AFTER_SECONDS = 15 * 60  # 15 min: generous vs. normal Stage B


def _stuck_settlements_block(
    swarm_dir: Path = Path(".swarm"),
    stale_after_seconds: int = _SETTLEMENT_STALE_AFTER_SECONDS,
) -> list[dict[str, Any]]:
    """Read-only scan for coder settlements stuck in a non-terminal state
    (e.g. DISPATCHED) past a staleness window.

    This works around a known upstream opencode-swarm plugin defect
    (see docs/known_issues/opencode_swarm_settlement_bug.md) where a stream
    death between coder completion and settlement write leaves a durable
    ``DISPATCHED`` record that blocks every recovery path the plugin itself
    offers (``check_gate_status``, ``update_task_status``, re-delegation).

    This function ONLY reads ``.swarm/coder-settlements/*.json`` — it never
    writes to plugin-owned state. Flag-only, by design: recommending a fix
    (e.g. "reset with '/swarm resettle <task_id>' or ask the architect to
    re-scope") is a decision for the operator/architect, not something this
    diagnostic silently does on the plugin's behalf. Never raises; any read
    error becomes an explicit error field instead of a crash, matching the
    rest of this module.
    """
    settlements_dir = swarm_dir / "coder-settlements"
    out: list[dict[str, Any]] = []
    if not settlements_dir.is_dir():
        return out

    now = datetime.now(timezone.utc)
    try:
        entries = sorted(settlements_dir.glob("*.json"))
    except OSError as exc:  # pragma: no cover - defensive
        return [{"error": f"could not list {settlements_dir}: {exc}"}]

    for path in entries:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            out.append({"task_id": path.stem, "error": f"unreadable: {exc}"})
            continue

        state = data.get("state")
        if state in _SETTLEMENT_TERMINAL_STATES:
            continue

        updated_raw = data.get("updated_at") or data.get("dispatched_at")
        age_seconds: float | None = None
        if updated_raw:
            try:
                updated_at = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                age_seconds = (now - updated_at).total_seconds()
            except ValueError:
                age_seconds = None

        if age_seconds is None or age_seconds < stale_after_seconds:
            continue  # too fresh to call stuck, or timestamp unparseable

        out.append({
            "task_id": data.get("task_id", path.stem),
            "state": state,
            "age_seconds": round(age_seconds),
            "path": str(path),
        })
    return out


def _swarm_block(checker: Callable[[], Any]) -> dict[str, Any]:
    try:
        health = checker()
        return {
            "binary": getattr(health, "binary", "swarm"),
            "available": bool(getattr(health, "available", False)),
            "version": getattr(health, "version", None),
            "error": getattr(health, "error", None),
        }
    except Exception as exc:  # pragma: no cover - defensive; never traceback
        return {"binary": "swarm", "available": False,
                "version": None, "error": str(exc)}


def _opencode_block(checker: Callable[[], bool]) -> dict[str, Any]:
    try:
        healthy = bool(checker())
    except Exception as exc:
        return {"base_url": _endpoint_label(OPENCODE_BASE_URL),
                "healthy": False, "error": str(exc)}
    return {"base_url": _endpoint_label(OPENCODE_BASE_URL),
            "healthy": healthy,
            "error": None if healthy else "OpenCode endpoint unreachable"}


def _endpoint_label(base_url: str) -> str:
    """Endpoint shown for diagnostics. Strips any userinfo (user:pass@) so
    credentials embedded in a URL are never printed."""
    label = base_url
    if "://" in label:
        scheme, _, rest = label.partition("://")
        if "@" in rest:
            rest = rest.rsplit("@", 1)[1]
        label = f"{scheme}://{rest}"
    return label


def _agents_block(db_path: Path) -> list[dict[str, Any]]:
    agents = control_db.list_agents(db_path=db_path)
    return [
        {"agent_id": a["agent_id"], "role": a["role"], "status": a["status"]}
        for a in agents if a["status"] != "idle"
    ]


def _recent_swarm_events(db_path: Path, limit: int) -> list[dict[str, Any]]:
    events = control_db.get_recent_events(limit=limit * 4, db_path=db_path)
    swarm_events = [e for e in events if str(e["event_type"]).startswith("swarm:")]
    out = []
    for e in swarm_events[:limit]:
        payload = {}
        try:
            payload = json.loads(e["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {"raw": str(e["payload"])}
        out.append({
            "id": e["id"],
            "event_type": e["event_type"],
            "agent_id": e["agent_id"],
            "session_id": e["session_id"],
            "task_id": e["task_id"],
            "occurred_at": e["occurred_at"],
            "payload": payload,
        })
    return out