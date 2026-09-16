"""
Normalize OpenCode session events (roadmap D-132, broken out of C-072).

The runtime reads OpenCode session objects directly today (GET /session/{id})
and will consume OpenCode's JSON event stream in Phase D. This module maps
either a bare session object or an event envelope onto one stable shape:

    {"session_id": str|None, "status": "active"|"archived"|"unknown",
     "model": Any|None, "tokens": Any|None, "title": str|None}

The exact stream shape is not yet verified against the ibraheem-111 fork
(docs/opencode_phase_a_findings.md, "Integration risks"), so the parser is
tolerant of a few realistic envelopes and degrades to status="unknown"
rather than raising when nothing usable can be extracted.
"""
from __future__ import annotations
from typing import Any, Optional

_ENVELOPE_KEYS = ("session", "data", "payload")
_ID_KEYS = ("id", "session_id", "sessionID", "sessionId")
_ARCHIVED_STATUS_VALUES = ("archived", "stopped", "closed")


def parse_session_event(raw: dict) -> dict[str, Any]:
    """Normalize an OpenCode session object or session event into a 5-key dict."""
    if not isinstance(raw, dict):
        return _unknown()

    envelope_type = raw.get("type") or raw.get("event") or raw.get("action")
    session = raw
    for key in _ENVELOPE_KEYS:
        if isinstance(raw.get(key), dict):
            session = raw[key]
            break

    session_id = _first_value(session, _ID_KEYS)
    if not session_id:
        return _unknown()

    return {
        "session_id": session_id,
        "status": _infer_status(session, envelope_type),
        "model": _pick(session, raw, "model"),
        "tokens": _pick(session, raw, "tokens"),
        "title": _pick(session, raw, "title"),
    }


def _unknown() -> dict[str, Any]:
    return {"session_id": None, "status": "unknown", "model": None, "tokens": None, "title": None}


def _first_value(d: dict, keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        val = d.get(key)
        if val:
            return val
    return None


def _pick(session: dict, raw: dict, key: str) -> Any:
    val = session.get(key)
    if val is None:
        val = raw.get(key)
    return val


def _infer_status(session: dict, envelope_type: Optional[str]) -> str:
    explicit = session.get("status")
    if isinstance(explicit, str):
        lowered = explicit.lower()
        if lowered in _ARCHIVED_STATUS_VALUES:
            return "archived"
        if lowered in ("active", "running", "started"):
            return "active"

    time = session.get("time")
    if isinstance(time, dict) and time.get("archived"):
        return "archived"

    if envelope_type:
        lowered = envelope_type.lower()
        if "archiv" in lowered or "closed" in lowered or "stopped" in lowered:
            return "archived"
        if "created" in lowered or "updated" in lowered or "started" in lowered:
            return "active"

    return "active"