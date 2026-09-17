"""
Minimal example agent profiles for the OpenCode Swarm first vertical slice.

These three profiles -- a manager, a single coding worker, and a
reviewer/tester -- are the smallest set needed to demonstrate a
manager -> worker -> tester loop under C-074. They are deliberately
minimal: real, long-lived agent identities live in ``control.db`` and are
provisioned out of band, not committed here.

Each value is a fully-validating :class:`~src.runtime.agent_profile.AgentProfile`
instance. Constructing one runs ``AgentProfile.__post_init__``, so merely
importing this module proves all three are valid (empty ``agent_id``/``model``
or an unknown ``role`` would raise ``ValueError`` at import time). The
profiles also round-trip through ``AgentProfile.from_row`` and
``control_db.register_agent``, so they double as stable documentation
examples and as test fixtures for D-143 (swarm.json rendering) and
D-147 (manager-loop fixtures).

No production credentials and no machine-specific paths are embedded: the
``model`` values are ``provider:model`` placeholders using the same
convention as ``config/opencode_agents_example.json`` (D-138), and
``tool_permissions`` are tool names declared in ``src/tools/registry.py``
(not provider API keys, shell paths, or URLs).

This is data only -- out of scope: the manager loop itself or any
autonomous behavior (see D-074 / C-074).
"""
from __future__ import annotations

from opencode_crack.runtime.agent_profile import AgentProfile

# Permission tool-names mirror the declarative catalog in
# src/tools/registry.py and the example set shipped by D-138. These are
# tool names (not credential material), so they contain no secrets.
_READ_ONLY = ["search_memory", "query_database", "read_file", "read_document"]
_CODING_WRITE = [
    "search_memory", "query_database", "remember", "ingest_document",
    "read_file", "read_document", "write_file", "run_python",
]


# --- Manager: coordinating, read-only ----------------------------------------
MANAGER_PROFILE = AgentProfile(
    agent_id="manager-01",
    role="manager",
    model="anthropic:claude-sonnet-4-6",
    tool_permissions=list(_READ_ONLY),
    manager_id=None,
    personality=(
        "Coordinator. Assigns work to workers, merges their reports, and "
        "keeps the loop moving. Reads broadly, writes nothing."
    ),
    notes=(
        "Example manager for the first vertical slice (D-146). Supervising "
        "role: no write/edit/execute tool permissions in this example."
    ),
)


# --- Coding worker: implementation-focused, supervised by the manager --------
CODING_WORKER_PROFILE = AgentProfile(
    agent_id="worker-01",
    role="worker",
    model="openrouter:anthropic/claude-haiku-4-5",
    tool_permissions=list(_CODING_WRITE),
    manager_id="manager-01",
    personality=(
        "Implementation-focused. Finishes one task before starting the next."
    ),
    notes=(
        "Example coding worker for the first vertical slice (D-146). "
        "Reports to manager-01."
    ),
)


# --- Tester: verification-focused, read/query only ---------------------------
TESTER_PROFILE = AgentProfile(
    agent_id="tester-01",
    role="tester",
    model="anthropic:claude-haiku-4-5",
    tool_permissions=list(_READ_ONLY),
    manager_id="manager-01",
    personality=(
        "Verification-focused. Reports evidence, not vibes."
    ),
    notes=(
        "Example reviewer/tester for the first vertical slice (D-146). "
        "Read + query only; deliberately no write/execute permissions."
    ),
)


#: The first vertical slice, in reporting order: manager -> worker -> tester.
EXAMPLE_PROFILES = [MANAGER_PROFILE, CODING_WORKER_PROFILE, TESTER_PROFILE]
