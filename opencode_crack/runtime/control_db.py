"""
Control-plane runtime state (SQLite).

Owns: agent registry, OpenCode session mapping, runtime task leases,
inter-agent messages, lifecycle events.

control.db is NOT committed to git — see .gitignore.
Do not commit it. It is runtime state, not a project artifact.
"""
from __future__ import annotations
import json, logging, sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

from opencode_crack.config import CONTROL_DB_PATH
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.storage.migrations import Migration, migrate

log = logging.getLogger(__name__)
EXPECTED_SCHEMA_VERSION = 1
DEFAULT_LEASE_MINUTES = 15

# C-070: real forward steps go here as Migration(version, description,
# apply) in ascending order. v1 is a no-op baseline — init_db() already
# creates the current shape above via SCHEMA — no real schema change to
# author yet.
MIGRATIONS: list[Migration] = [
    Migration(EXPECTED_SCHEMA_VERSION, "baseline (no-op)", lambda conn: None),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, migrated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY, role TEXT NOT NULL, model TEXT NOT NULL,
    tool_permissions TEXT DEFAULT '[]', manager_id TEXT, personality TEXT DEFAULT '',
    notes TEXT DEFAULT '', status TEXT DEFAULT 'idle',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oc_sessions (
    session_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, task_id TEXT,
    status TEXT DEFAULT 'active', model TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id));
CREATE TABLE IF NOT EXISTS leases (
    task_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT,
    claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL, heartbeat_at TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_retry_at TEXT,
    backoff_base_minutes INTEGER DEFAULT 5,
    backoff_max_minutes INTEGER DEFAULT 60,
    escalated INTEGER DEFAULT 0,
    escalation_reason TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id));
CREATE TABLE IF NOT EXISTS task_retry_state (
    task_id TEXT PRIMARY KEY,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    last_retry_at TEXT,
    backoff_base_minutes INTEGER DEFAULT 5,
    backoff_max_minutes INTEGER DEFAULT 60,
    escalated INTEGER DEFAULT 0,
    escalation_reason TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL, session_id TEXT, content TEXT NOT NULL,
    sent_at TEXT NOT NULL, read_at TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, session_id TEXT,
    task_id TEXT, event_type TEXT NOT NULL, payload TEXT DEFAULT '{}',
    occurred_at TEXT NOT NULL);
"""

@contextmanager
def _connect(db_path: Path = CONTROL_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db(db_path: Path = CONTROL_DB_PATH) -> None:
    """Create control.db's tables if they don't exist yet. Safe to call
    every time the app starts.

    Hands version tracking off to the shared migration runner (C-070/
    D-230) instead of the previous ad-hoc COUNT-then-compare-then-warn
    logic: still seeds the baseline version row itself on first run
    (the runner refuses to guess a version for a DB with other tables
    but an empty schema_version — by design, see migrations.py), so on
    an already-current DB the migrate() call below is a true no-op, and
    any future real migration added to MIGRATIONS applies safely
    instead of the old warn-and-keep-running behavior.
    """
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row[0] == 0:
            conn.execute(
                "INSERT INTO schema_version VALUES (?, ?)",
                (EXPECTED_SCHEMA_VERSION, _now()),
            )
    migrate(db_path, MIGRATIONS)

def get_schema_version(db_path: Path = CONTROL_DB_PATH) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("schema_version table is empty — run init_db() first")
        return row["version"]

def register_agent(profile: AgentProfile, db_path: Path = CONTROL_DB_PATH) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute("""
            INSERT INTO agents (agent_id,role,model,tool_permissions,manager_id,personality,notes,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,'idle',?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                role=excluded.role, model=excluded.model,
                tool_permissions=excluded.tool_permissions, manager_id=excluded.manager_id,
                personality=excluded.personality, notes=excluded.notes, updated_at=excluded.updated_at
        """, (profile.agent_id, profile.role, profile.model, profile.tool_permissions_json(),
              profile.manager_id, profile.personality, profile.notes, now, now))

def get_agent(agent_id: str, db_path: Path = CONTROL_DB_PATH) -> Optional[AgentProfile]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    return AgentProfile.from_row(dict(row)) if row else None

def list_agents(role=None, status=None, db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    clauses, params = [], []
    if role: clauses.append("role=?"); params.append(role)
    if status: clauses.append("status=?"); params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM agents {where} ORDER BY created_at", params).fetchall()
    return [dict(r) for r in rows]

def set_agent_status(agent_id: str, status: str, db_path: Path = CONTROL_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE agents SET status=?, updated_at=? WHERE agent_id=?", (status, _now(), agent_id))

def deregister_agent(agent_id: str, db_path: Path = CONTROL_DB_PATH) -> bool:
    """Remove an agent from the registry (C-073's `brain agents remove`).

    Releases any leases the agent currently holds first — leases.agent_id
    has a real FOREIGN KEY constraint (unlike sessions/events/messages,
    which reference agent_id as plain TEXT with no constraint), so
    deletion would otherwise fail with an IntegrityError while the agent
    holds an active lease. Historical sessions/events/messages are left
    alone since they're audit record, not live state — see
    `brain events` for why they should stay visible after removal.
    Returns False if no such agent existed (caller can report "not
    found" vs "removed")."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM leases WHERE agent_id=?", (agent_id,))
        cur = conn.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
        return cur.rowcount > 0

def record_session_start(session_id, agent_id, task_id=None, model=None, db_path: Path = CONTROL_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO oc_sessions (session_id,agent_id,task_id,status,model,started_at) VALUES (?,?,?,'active',?,?)",
                     (session_id, agent_id, task_id, model, _now()))
    _append_event("session_started", agent_id=agent_id, session_id=session_id, task_id=task_id, db_path=db_path)

def link_session_to_task(session_id, task_id, agent_id, db_path: Path = CONTROL_DB_PATH) -> bool:
    """Attribute an existing session row to a task + agent (roadmap D-162).

    Idempotent: only updates when the session exists and does not already
    carry a different task_id (an ingested session's own attribution wins —
    it is never clobbered). Returns True when the session row exists (so a
    binding can proceed), False when the session is unknown entirely.
    """
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM oc_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE oc_sessions SET task_id=?, agent_id=? WHERE session_id=? AND (task_id IS NULL OR task_id=?)",
            (task_id, agent_id, session_id, task_id))
        return True

def record_session_end(session_id, status="finished", db_path: Path = CONTROL_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE oc_sessions SET status=?, finished_at=? WHERE session_id=?", (status, _now(), session_id))
    _append_event("session_ended", session_id=session_id, payload={"final_status": status}, db_path=db_path)

def get_active_session(agent_id, db_path: Path = CONTROL_DB_PATH) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM oc_sessions WHERE agent_id=? AND status='active' ORDER BY started_at DESC LIMIT 1", (agent_id,)).fetchone()
    return dict(row) if row else None

def list_active_sessions(db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    """All currently-active OpenCode sessions, newest first (roadmap D-165)."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM oc_sessions WHERE status='active' ORDER BY started_at DESC").fetchall()
    return [dict(r) for r in rows]

def acquire_lease(task_id, agent_id, session_id=None, duration_minutes=DEFAULT_LEASE_MINUTES, db_path: Path = CONTROL_DB_PATH) -> bool:
    # Block acquisition if task has been escalated (terminal failure)
    with _connect(db_path) as conn:
        rs = conn.execute("SELECT escalated FROM task_retry_state WHERE task_id=?", (task_id,)).fetchone()
        if rs and rs["escalated"]:
            return False
    now, expires = _now(), _now_plus(minutes=duration_minutes)
    try:
        with _connect(db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO leases (task_id,agent_id,session_id,claimed_at,expires_at,heartbeat_at) VALUES (?,?,?,?,?,?)",
                         (task_id, agent_id, session_id, now, expires, now))
            row = conn.execute("SELECT agent_id FROM leases WHERE task_id=?", (task_id,)).fetchone()
        if row and row["agent_id"] == agent_id:
            # Hydrate new lease row from durable retry state if it exists (persists across restart/requeue)
            with _connect(db_path) as conn:
                rs = conn.execute("SELECT * FROM task_retry_state WHERE task_id=?", (task_id,)).fetchone()
                if rs and rs["retry_count"] > 0:
                    conn.execute(
                        "UPDATE leases SET retry_count=?, max_retries=?, last_retry_at=?, backoff_base_minutes=?, backoff_max_minutes=?, escalated=?, escalation_reason=? WHERE task_id=?",
                        (rs["retry_count"], rs["max_retries"], rs["last_retry_at"], rs["backoff_base_minutes"], rs["backoff_max_minutes"], rs["escalated"], rs["escalation_reason"], task_id),
                    )
            _append_event("lease_claimed", agent_id=agent_id, task_id=task_id, db_path=db_path)
            return True
        return False
    except sqlite3.IntegrityError:
        return False

def heartbeat_lease(task_id, db_path: Path = CONTROL_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE leases SET heartbeat_at=?, expires_at=? WHERE task_id=?", (_now(), _now_plus(minutes=DEFAULT_LEASE_MINUTES), task_id))

def heartbeat_agent_leases(agent_id: str, db_path: Path = CONTROL_DB_PATH) -> int:
    """Heartbeat every lease currently held by agent_id (C-073's
    "heartbeat loop, background or invokable"). This is the invokable
    form: one call refreshes all of an agent's active leases in one
    pass, so a session/cron/supervisor can call it on a fixed interval
    without knowing which task_ids that agent currently holds.

    A true background daemon isn't implemented here — this repo has no
    process-supervision infrastructure yet (see C-004/C-005, which is
    about local model runtimes, not process management) — but this
    function is the piece any such daemon or an external cron entry
    would call in a loop. Returns the number of leases heartbeated."""
    with _connect(db_path) as conn:
        now = _now()
        expires = _now_plus(minutes=DEFAULT_LEASE_MINUTES)
        cur = conn.execute(
            "UPDATE leases SET heartbeat_at=?, expires_at=? WHERE agent_id=?",
            (now, expires, agent_id),
        )
        return cur.rowcount
from opencode_crack.orchestrator.task_board import release_task, block_task


def release_lease(task_id, db_path: Path = CONTROL_DB_PATH) -> None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT agent_id FROM leases WHERE task_id=?", (task_id,)).fetchone()
        conn.execute("DELETE FROM leases WHERE task_id=?", (task_id,))
    _append_event("lease_released", agent_id=row["agent_id"] if row else None, task_id=task_id, db_path=db_path)


def get_stale_leases(db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM leases WHERE expires_at<?", (_now(),)).fetchall()
    return [dict(r) for r in rows]


def reclaim_stale_leases(
    max_retries: int = 3,
    backoff_base_minutes: int = 5,
    backoff_max_minutes: int = 60,
    db_path: Path = CONTROL_DB_PATH,
    _release_task=None,
    _block_task=None,
) -> list[dict]:
    """
    Reclaim stale leases and re-queue or escalate tasks.

    For each stale lease:
    - Increment retry_count
    - If retry_count <= max_retries: persist retry state, release lease, re-queue task (status -> open)
      with exponential backoff (base * 2^retry + jitter, capped at backoff_max_minutes)
    - If retry_count > max_retries: mark task blocked, emit escalation event

    Retry count persists in task_retry_state across lease deletion/restart.
    Returns list of reclaimed tasks with their recovery actions.
    """
    # Dependency injection for testing
    release_fn = _release_task or release_task
    block_fn = _block_task or block_task

    stale = get_stale_leases(db_path)
    results = []

    for lease in stale:
        task_id = lease["task_id"]
        # Prefer durable retry state if it holds a higher count (survives prior reclaim)
        durable_retry = 0
        with _connect(db_path) as conn:
            rs = conn.execute("SELECT retry_count FROM task_retry_state WHERE task_id=?", (task_id,)).fetchone()
            if rs:
                durable_retry = rs["retry_count"]
        effective_retry = max(lease.get("retry_count", 0) or 0, durable_retry)
        retry_count = effective_retry + 1
        max_r = lease.get("max_retries", max_retries)
        if lease.get("max_retries") is None:
            with _connect(db_path) as conn:
                rs2 = conn.execute("SELECT max_retries FROM task_retry_state WHERE task_id=?", (task_id,)).fetchone()
                if rs2 and rs2["max_retries"] is not None:
                    max_r = rs2["max_retries"]
        base = lease.get("backoff_base_minutes", backoff_base_minutes) or backoff_base_minutes
        cap = lease.get("backoff_max_minutes", backoff_max_minutes) or backoff_max_minutes

        if retry_count <= max_r:
            # Calculate backoff with jitter
            import random
            backoff_min = min(base * (2 ** (retry_count - 1)) + random.randint(0, base), cap)

            # Persist retry state durably BEFORE deleting lease (survives restart)
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO task_retry_state (task_id, retry_count, max_retries, last_retry_at, backoff_base_minutes, backoff_max_minutes, escalated)
                    VALUES (?,?,?,?,?,?,0)
                    ON CONFLICT(task_id) DO UPDATE SET retry_count=excluded.retry_count, max_retries=excluded.max_retries, last_retry_at=excluded.last_retry_at, backoff_base_minutes=excluded.backoff_base_minutes, backoff_max_minutes=excluded.backoff_max_minutes, escalated=0, escalation_reason=NULL
                    """,
                    (task_id, retry_count, max_r, _now(), base, cap),
                )

            # Now safe to release the stale lease (single-owner invariant: delete first, then re-queue)
            release_lease(task_id, db_path)

            # Re-queue task by releasing it (task_board.release_task handles status -> open)
            # Guard: task may not exist in task board (e.g., test tasks)
            try:
                release_fn(task_id, notes=f"Watchdog reclaimed stale lease (retry {retry_count}/{max_r}, backoff {backoff_min}min)")
            except ValueError as e:
                if "No such task" in str(e):
                    log.info("Task %s not in task board; skipping task-board re-queue", task_id)
                else:
                    raise

            _append_event(
                "lease_reclaimed",
                agent_id=lease.get("agent_id"),
                task_id=task_id,
                payload={
                    "retry_count": retry_count,
                    "max_retries": max_r,
                    "backoff_minutes": backoff_min,
                    "previous_expires_at": lease.get("expires_at"),
                },
                db_path=db_path,
            )

            results.append({
                "task_id": task_id,
                "action": "requeued",
                "retry_count": retry_count,
                "backoff_minutes": backoff_min,
            })
        else:
            # Max retries exceeded - escalate durably
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO task_retry_state (task_id, retry_count, max_retries, last_retry_at, escalated, escalation_reason)
                    VALUES (?,?,?,?,1,?)
                    ON CONFLICT(task_id) DO UPDATE SET retry_count=excluded.retry_count, max_retries=excluded.max_retries, last_retry_at=excluded.last_retry_at, escalated=1, escalation_reason=excluded.escalation_reason
                    """,
                    (task_id, retry_count, max_r, _now(), f"Max retries ({max_r}) exceeded"),
                )
                # Also update leases row for visibility if still present
                conn.execute(
                    """
                    UPDATE leases SET
                        retry_count = ?,
                        escalated = 1,
                        escalation_reason = ?
                    WHERE task_id = ?
                    """,
                    (retry_count, f"Max retries ({max_r}) exceeded", task_id),
                )

            # Mark task as blocked with escalation
            try:
                block_fn(task_id, notes=f"Watchdog escalation: max retries ({max_r}) exceeded after {retry_count} attempts. Last expires_at: {lease.get('expires_at')}")
            except ValueError as e:
                if "No such task" in str(e):
                    log.info("Task %s not in task board; skipping task-board escalation", task_id)
                else:
                    raise

            _append_event(
                "task_escalated",
                agent_id=lease.get("agent_id"),
                task_id=task_id,
                payload={
                    "retry_count": retry_count,
                    "max_retries": max_r,
                    "reason": "max_retries_exceeded",
                    "last_expires_at": lease.get("expires_at"),
                },
                db_path=db_path,
            )

            results.append({
                "task_id": task_id,
                "action": "escalated",
                "retry_count": retry_count,
            })

    return results


def get_task_retry_state(task_id: str, db_path: Path = CONTROL_DB_PATH) -> Optional[dict]:
    """Durable retry state for a task (survives lease deletion/restart)."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM task_retry_state WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def clear_task_retry_state(task_id: str, db_path: Path = CONTROL_DB_PATH) -> None:
    """Clear retry state after successful completion (human/verified path)."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM task_retry_state WHERE task_id=?", (task_id,))


def get_stale_retry_state(db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    """All escalated or retrying task states (for monitoring)."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM task_retry_state WHERE escalated=1 OR retry_count>0").fetchall()
    return [dict(r) for r in rows]

def send_message(from_agent_id, to_agent_id, content, session_id=None, db_path: Path = CONTROL_DB_PATH) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute("INSERT INTO messages (from_agent_id,to_agent_id,session_id,content,sent_at) VALUES (?,?,?,?,?)",
                           (from_agent_id, to_agent_id, session_id, content, _now()))
        return cur.lastrowid

def get_unread_messages(agent_id, db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM messages WHERE to_agent_id=? AND read_at IS NULL ORDER BY sent_at ASC", (agent_id,)).fetchall()
    return [dict(r) for r in rows]

def mark_messages_read(agent_id, db_path: Path = CONTROL_DB_PATH) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute("UPDATE messages SET read_at=? WHERE to_agent_id=? AND read_at IS NULL", (_now(), agent_id))
        return cur.rowcount

def get_recent_events(limit=50, agent_id=None, db_path: Path = CONTROL_DB_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        if agent_id:
            rows = conn.execute("SELECT * FROM events WHERE agent_id=? ORDER BY id DESC LIMIT ?", (agent_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_control_plane_summary(db_path: Path = CONTROL_DB_PATH) -> dict:
    """Compact snapshot of the agent control plane for brain.get_status()
    (C-073). Read-only: reuses the same queries as `brain agents` /
    `brain agents stale` / `brain events` so the summary always agrees
    with what those commands report — no separate counting logic to
    drift out of sync.

    Returns zeroed-out counts (not an error) when control.db doesn't
    exist yet, since an unconfigured control plane is a valid state for
    doctor to report on, not a failure."""
    if not db_path.exists():
        return {
            "db_initialized": False,
            "agent_count": 0,
            "active_sessions": 0,
            "stale_leases": 0,
            "recent_event_count": 0,
        }
    agents = list_agents(db_path=db_path)
    active_sessions = list_active_sessions(db_path=db_path)
    stale = get_stale_leases(db_path=db_path)
    recent_events = get_recent_events(limit=10, db_path=db_path)
    return {
        "db_initialized": True,
        "agent_count": len(agents),
        "active_sessions": len(active_sessions),
        "stale_leases": len(stale),
        "recent_event_count": len(recent_events),
    }

def event_exists(event_type: str, source_event_id: str, db_path: Path = CONTROL_DB_PATH) -> bool:
    """True when an event of this type was already ingested for the given
    source event id (idempotency key for swarm event ingestion).

    Events without a source id are never considered duplicates, so an
    empty/None source_event_id returns False. Unknown rows are a no-op."""
    if not source_event_id:
        return False
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE event_type=? AND json_extract(payload, '$.source_event_id')=? LIMIT 1",
            (event_type, source_event_id),
        ).fetchone()
    return row is not None

def mark_session_terminal(session_id: str, status: str = "settled", db_path: Path = CONTROL_DB_PATH) -> bool:
    """Record a session's terminal state (roadmap D-144: agent-settled).

    Idempotent: re-marking an already-terminal session is a harmless no-op
    that still returns True while the row exists. Unknown sessions return
    False (never raise) so ingestion of unknown references does not crash.
    """
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE oc_sessions SET status=?, finished_at=? WHERE session_id=?",
            (status, _now(), session_id),
        )
        return cur.rowcount > 0

def _append_event(event_type, agent_id=None, session_id=None, task_id=None, payload=None, db_path: Path = CONTROL_DB_PATH) -> None:
    try:
        with _connect(db_path) as conn:
            conn.execute("INSERT INTO events (agent_id,session_id,task_id,event_type,payload,occurred_at) VALUES (?,?,?,?,?,?)",
                         (agent_id, session_id, task_id, event_type, json.dumps(payload or {}), _now()))
    except Exception as exc:
        log.warning("Failed to append event %r: %s", event_type, exc)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_plus(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
