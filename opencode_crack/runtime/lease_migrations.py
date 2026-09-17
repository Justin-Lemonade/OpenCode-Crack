"""
Lease schema migrations for watchdog/recovery (D-278/D-280).

Adds retry tracking columns to the leases table.
"""
from opencode_crack.storage.migrations import Migration


def add_retry_columns(conn):
    """Add retry tracking columns to leases table."""
    conn.execute("""
        ALTER TABLE leases ADD COLUMN retry_count INTEGER DEFAULT 0
    """)
    conn.execute("""
        ALTER TABLE leases ADD COLUMN max_retries INTEGER DEFAULT 3
    """)
    conn.execute("""
        ALTER TABLE leases ADD COLUMN last_retry_at TEXT
    """)


def add_backoff_config(conn):
    """Add backoff configuration columns to leases table."""
    conn.execute("""
        ALTER TABLE leases ADD COLUMN backoff_base_minutes INTEGER DEFAULT 5
    """)
    conn.execute("""
        ALTER TABLE leases ADD COLUMN backoff_max_minutes INTEGER DEFAULT 60
    """)


def add_escalation_columns(conn):
    """Add escalation tracking columns."""
    conn.execute("""
        ALTER TABLE leases ADD COLUMN escalated INTEGER DEFAULT 0
    """)
    conn.execute("""
        ALTER TABLE leases ADD COLUMN escalation_reason TEXT
    """)


MIGRATIONS = [
    Migration(2, "add retry tracking columns to leases", add_retry_columns),
    Migration(3, "add backoff configuration columns to leases", add_backoff_config),
    Migration(4, "add escalation tracking columns to leases", add_escalation_columns),
]