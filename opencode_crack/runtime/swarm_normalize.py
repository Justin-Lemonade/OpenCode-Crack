"""
Normalize OpenCode Swarm `swarm run --json` results and JSONL events into a
stable internal representation (roadmap D-142, broken out of C-074).

The swarm CLI's exact key spelling is not guaranteed (snake_case vs
camelCase, `swarmId` vs `swarm_id`, ...), so every consumer-facing record
goes through one pure normalizer here. The normalized shapes:

    SwarmRunRecord:  swarm_id | status | agents | error
    AgentRecord:     name | status | model | tokens | cost | error
    SwarmEventRecord: event_id | event_type | agent | session_id | task_id
                     | status | model | tokens | cost | error

Rules:
  * every field that is present in the raw record is preserved;
  * every field that is absent is None (never a crash);
  * malformed events are either ignored (parse_events_text skips garbage
    lines) or returned as an explicit record with event_type="unknown"
    (normalize_event on an unusable dict);
  * nothing here touches disk, the network, or a subprocess — purely
    deterministic and offline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

_ID_KEYS = ("swarmId", "swarm_id", "id")
_STATUS_KEYS = ("status", "state", "phase")
_MODEL_KEYS = ("model", "modelId", "model_id")
_TOKENS_KEYS = ("tokens", "tokenCount", "token_count")
_COST_KEYS = ("cost", "costUsd", "costUSD", "cost_usd")
_ERROR_KEYS = ("error", "errorText", "error_text", "message")
_EVENT_TYPE_KEYS = ("type", "event", "eventType", "event_type")
_AGENT_KEYS = ("agent", "agentName", "agent_id", "agentId")
_SESSION_KEYS = ("sessionId", "session_id", "session")
_TASK_KEYS = ("taskId", "task_id", "task")


@dataclass(frozen=True)
class AgentRecord:
    name: str | None = None
    status: str | None = None
    model: str | None = None
    tokens: int | None = None
    cost: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class SwarmRunRecord:
    swarm_id: str | None = None
    status: str = "unknown"
    agents: tuple[AgentRecord, ...] = field(default_factory=tuple)
    error: str | None = None


@dataclass(frozen=True)
class SwarmEventRecord:
    event_id: str | None = None
    event_type: str = "unknown"
    agent: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    status: str | None = None
    model: str | None = None
    tokens: int | None = None
    cost: float | None = None
    error: str | None = None


def _first(d: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        value = d.get(key)
        if value is not None:
            return value
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_agent(raw: Any) -> AgentRecord:
    """Normalize one `agents[]` entry (or anything dict-like) into an AgentRecord."""
    if not isinstance(raw, dict):
        return AgentRecord()
    return AgentRecord(
        name=str(_first(raw, ("name", "agentName", "agent", "agent_id")) or "") or None,
        status=str(_first(raw, _STATUS_KEYS) or "") or None,
        model=str(_first(raw, _MODEL_KEYS) or "") or None,
        tokens=_to_int(_first(raw, _TOKENS_KEYS)),
        cost=_to_float(_first(raw, _COST_KEYS)),
        error=str(_first(raw, _ERROR_KEYS) or "") or None,
    )


def normalize_swarm_result(raw: Any) -> SwarmRunRecord:
    """Normalize a `swarm run --json` result dict into a SwarmRunRecord."""
    if not isinstance(raw, dict):
        return SwarmRunRecord(status="unknown", error="malformed result: not an object")
    agents_raw = raw.get("agents")
    agents = tuple(
        normalize_agent(a) for a in agents_raw if isinstance(a, dict)
    ) if isinstance(agents_raw, list) else tuple()
    return SwarmRunRecord(
        swarm_id=str(_first(raw, _ID_KEYS) or "") or None,
        status=str(_first(raw, _STATUS_KEYS) or "") or "unknown",
        agents=agents,
        error=str(_first(raw, _ERROR_KEYS) or "") or None,
    )


def normalize_event(raw: Any) -> SwarmEventRecord:
    """Normalize one JSONL event into a SwarmEventRecord.

    A dict with no usable event type still yields an explicit record with
    event_type="unknown" — the consumer never crashes on partial input. A
    non-dict yields an all-None "unknown" record.
    """
    if not isinstance(raw, dict):
        return SwarmEventRecord(event_type="unknown", error="malformed event: not an object")
    event_type = str(_first(raw, _EVENT_TYPE_KEYS) or "") or "unknown"
    return SwarmEventRecord(
        event_id=str(_first(raw, ("id", "eventId", "event_id")) or "") or None,
        event_type=event_type,
        agent=str(_first(raw, _AGENT_KEYS) or "") or None,
        session_id=str(_first(raw, _SESSION_KEYS) or "") or None,
        task_id=str(_first(raw, _TASK_KEYS) or "") or None,
        status=str(_first(raw, _STATUS_KEYS) or "") or None,
        model=str(_first(raw, _MODEL_KEYS) or "") or None,
        tokens=_to_int(_first(raw, _TOKENS_KEYS)),
        cost=_to_float(_first(raw, _COST_KEYS)),
        error=str(_first(raw, _ERROR_KEYS) or "") or None,
    )


def parse_event_line(line: str) -> SwarmEventRecord | None:
    """Parse one JSONL line.

    Returns None for blank lines, unparseable JSON, and non-object JSON so
    callers that want strictness can ignore garbage outright.
    """
    if not line.strip():
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return normalize_event(value)


def parse_events_text(text: str) -> list[SwarmEventRecord]:
    """Parse a JSONL event stream, ignoring blank/malformed lines.

    Strict: garbage lines are skipped entirely (the acceptance permits
    "ignored or explicit unknown"). For the explicit-unknown view on a
    single record, call normalize_event directly.
    """
    records: list[SwarmEventRecord] = []
    for line in text.splitlines():
        record = parse_event_line(line)
        if record is not None:
            records.append(record)
    return records
