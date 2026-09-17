"""
Deterministic fixture builders for OpenCode Swarm command/result serialization
(roadmap D-142, broken out of C-074).

Fixtures are generated at test time — no committed binary blobs — and are
byte-for-byte reproducible:

    build_successful_swarm()       raw `swarm run --json` result, all agents ok
    build_partial_failed_swarm()   same result with one failed agent + error
    build_events_text()            JSONL stream with agent-turn-done / agent-settled
    build_events_text_malformed()  JSONL stream including garbage lines

The raw shapes mirror what the OpenCode Swarm CLI is documented to emit
(`swarmId`, `status`, `agents[]`, `--events` JSONL) so the normalizers in
src/runtime/swarm_normalize.py get realistic input.
"""
from __future__ import annotations

import json
from typing import Any

SUCCESSFUL_AGENTS = [
    {
        "name": "manager",
        "status": "completed",
        "model": "openai/gpt-4o-mini",
        "tokens": 2100,
        "cost": 0.04,
    },
    {
        "name": "worker",
        "status": "completed",
        "model": "openai/gpt-4o-mini",
        "tokens": 18400,
        "cost": 0.31,
    },
    {
        "name": "tester",
        "status": "completed",
        "model": "openai/gpt-4o-mini",
        "tokens": 5600,
        "cost": 0.09,
    },
]

PARTIAL_AGENTS = [
    {
        "name": "manager",
        "status": "completed",
        "model": "openai/gpt-4o-mini",
        "tokens": 2100,
        "cost": 0.04,
    },
    {
        "name": "worker",
        "status": "failed",
        "model": "openai/gpt-4o-mini",
        "tokens": 9400,
        "cost": 0.17,
        "error": "worker exited after reaching max rounds (5) without a stable result",
    },
    {
        "name": "tester",
        "status": "never_started",
        "model": "openai/gpt-4o-mini",
    },
]

EVENT_TURN_DONE = {
    "id": "evt-0001",
    "type": "agent-turn-done",
    "agent": "worker",
    "sessionId": "ses-worker-1",
    "taskId": "D-142",
    "model": "openai/gpt-4o-mini",
    "tokens": 7200,
    "cost": 0.12,
}

EVENT_SETTLED = {
    "id": "evt-0002",
    "type": "agent-settled",
    "agent": "worker",
    "sessionId": "ses-worker-1",
    "taskId": "D-142",
    "status": "settled",
}


def build_successful_swarm(swarm_id: str = "sw_fixture_success") -> dict[str, Any]:
    """A full `swarm run --json` result where every agent completed."""
    return {
        "swarmId": swarm_id,
        "status": "completed",
        "agents": [dict(a) for a in SUCCESSFUL_AGENTS],
    }


def build_partial_failed_swarm(swarm_id: str = "sw_fixture_partial") -> dict[str, Any]:
    """A result where one agent failed and another never started."""
    return {
        "swarmId": swarm_id,
        "status": "failed",
        "agents": [dict(a) for a in PARTIAL_AGENTS],
    }


def build_events_text() -> str:
    """JSONL event stream with one agent-turn-done and one agent-settled."""
    return "\n".join(json.dumps(e) for e in (EVENT_TURN_DONE, EVENT_SETTLED)) + "\n"


def build_events_text_malformed() -> str:
    """JSONL stream interleaving valid events with garbage lines.

    The first garbage line is not valid JSON at all; the second is valid JSON
    but not an object; the third is an object missing every usable field.
    """
    return "\n".join(
        [
            json.dumps(EVENT_TURN_DONE),
            "this is not json {{{",
            json.dumps(EVENT_SETTLED),
            '["an", "array", "not", "an", "object"]',
            '{"type": "agent-turn-done"}',
            "",
        ]
    ) + "\n"
