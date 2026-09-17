"""
Agent context telemetry (roadmap D-160).

Records lightweight, opt-in estimates of the prompt/context an agent consumed
per delegated task, so the token-efficiency audit can compare the old
heavyweight workflow against the new compact one:

    protocol   estimated tokens for the find-work protocol text
    contract   estimated tokens for the task contract/brief
    briefing   estimated tokens for the repository session briefing
    handoff    estimated tokens for the report/handoff text
    output     estimated tokens of model output (work produced)

Design rules (from the roadmap acceptance):

  * opt-in — nothing is recorded unless OPENCODE_CRACK_CONTEXT_TELEMETRY is set to a
    truthy value;
  * no secrets, no memory contents, no full prompts/responses — only integer
    token estimates per category are ever stored, so redaction is structural:
    the payload schema simply has no room for content;
  * bounded events — payload keys are fixed and all values are capped ints;
  * aggregate counts — the report groups by workflow label (legacy vs compact)
    and yields averages per category, total estimated context, and a
    context-to-work ratio (context tokens / output tokens);
  * stored through the existing control_db `events` table (event_type
    "context_telemetry") — no schema change, no migration.

None of this contacts a provider billing/token API.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from opencode_crack.runtime import control_db

ENV_FLAG = "OPENCODE_CRACK_CONTEXT_TELEMETRY"
EVENT_TYPE = "context_telemetry"
CONTEXT_CATEGORIES = ("protocol", "contract", "briefing", "handoff")
ALL_CATEGORIES = (*CONTEXT_CATEGORIES, "output")

# Upper bound for any single category's estimated-token value. Keeps each
# payload small and bounded regardless of how large a real context got.
_MAX_CATEGORY_TOKENS = 1_000_000


def telemetry_enabled() -> bool:
    """Opt-in gate: on only when the env flag is set to a truthy value."""
    return os.getenv(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def estimate_tokens(chars: int) -> int:
    """Rough token estimate from a character count (~4 chars/token).

    Pure and offline; no provider API is consulted. Negative inputs clamp
    to 0."""
    return max(0, int(chars / 4))


def _bounded(value: int) -> int:
    return max(0, min(int(value), _MAX_CATEGORY_TOKENS))


def record_context_usage(
    task_id: str,
    *,
    protocol_tokens: int = 0,
    contract_tokens: int = 0,
    briefing_tokens: int = 0,
    handoff_tokens: int = 0,
    output_tokens: int = 0,
    workflow: str = "unknown",
    db_path: Path = control_db.CONTROL_DB_PATH,
) -> bool:
    """Record one task's estimated context usage.

    Returns False when telemetry is disabled (opt-in), True when an event
    was written. Never raises for an unknown task — the events table carries
    a free-form task_id. Only capped integers are stored; content is never
    accepted or persisted.
    """
    if not telemetry_enabled():
        return False
    context = {
        "protocol": _bounded(protocol_tokens),
        "contract": _bounded(contract_tokens),
        "briefing": _bounded(briefing_tokens),
        "handoff": _bounded(handoff_tokens),
    }
    payload = {
        "workflow": str(workflow)[:32],
        "context": context,
        "output": _bounded(output_tokens),
    }
    payload["total_context"] = sum(context.values())
    control_db._append_event(
        event_type=EVENT_TYPE,
        task_id=task_id,
        payload=payload,
        db_path=db_path,
    )
    return True


def summarize_context_usage(db_path: Path = control_db.CONTROL_DB_PATH) -> dict[str, Any]:
    """Aggregate recorded telemetry for the token-efficiency comparison.

    Returns a dict with per-category averages, average total estimated
    context, average output, an overall context-to-work ratio, and the same
    aggregates grouped by workflow label (legacy vs compact). Empty when
    nothing has been recorded.
    """
    events = [
        e for e in control_db.get_recent_events(limit=100000, db_path=db_path)
        if e["event_type"] == EVENT_TYPE
    ]
    if not events:
        return {"count": 0, "workflows": {}}

    def _avg(values):
        return round(sum(values) / len(values), 1) if values else 0.0

    by_workflow: dict[str, list[dict]] = {}
    for event in events:
        try:
            payload = event["payload"] if isinstance(event["payload"], dict) else _json_loads(event["payload"])
        except (ValueError, TypeError):
            payload = {}
        workflow = str(payload.get("workflow", "unknown"))
        by_workflow.setdefault(workflow, []).append(payload)

    def _summarize(rows: list[dict]) -> dict[str, Any]:
        totals = {c: _avg([_row_int(r, f"context.{c}") for r in rows]) for c in CONTEXT_CATEGORIES}
        avg_total = _avg([_row_int(r, "total_context") for r in rows])
        avg_output = _avg([_row_int(r, "output") for r in rows])
        ratio = round(avg_total / avg_output, 2) if avg_output else None
        return {
            "count": len(rows),
            "avg_context": totals,
            "avg_total_context": avg_total,
            "avg_output": avg_output,
            "context_to_work_ratio": ratio,
        }

    return {
        "count": len(events),
        "overall": _summarize(list(by_workflow.values()) and [p for rows in by_workflow.values() for p in rows]),
        "workflows": {w: _summarize(rows) for w, rows in by_workflow.items()},
    }


def _json_loads(text: str) -> dict:
    import json
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def _row_int(payload: dict, dotted_key: str) -> int:
    value: Any = payload
    for part in dotted_key.split("."):
        if isinstance(value, dict):
            value = value.get(part, 0)
        else:
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
