"""Shared timestamp contract for orchestration state files.

`tasks.yaml` and `concerns.yaml` (and any future `orchestration/*.yaml`
board) both need the same guarantee: every persisted timestamp is
whole-second UTC in `%Y-%m-%dT%H:%M:%SZ` form, because that's what
`strptime`-based staleness checks and the serialization contract test
(`tests/test_serialization_contract.py`) both assume. D-263 found and
fixed one violation of this in `tasks.yaml`; this module exists so the
fix (validate at the single writer, not "by convention") is written
once and reused, instead of quietly re-diverging the next time a board
gets added. See `reports/D-263_report.md` for the original incident.
"""
import re
from datetime import datetime, timezone
from typing import Optional

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Matches the contract exactly: whole-second UTC, e.g. '2026-08-21T17:21:06Z'.
# Rejects fractional seconds, offsets other than 'Z', and any other drift.
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def now() -> str:
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def validate_timestamp_format(record_id: str, field: str, value: Optional[str]) -> None:
    """Raise if `value` isn't None and isn't a whole-second
    '%Y-%m-%dT%H:%M:%SZ' string. Call this from the single on-disk writer
    of a board (e.g. `_save_states`) so no path -- CLI, bulk migration
    script, or future caller -- can silently persist a timestamp that
    disagrees with the contract."""
    if value is None:
        return
    if not isinstance(value, str) or not TIMESTAMP_RE.match(value):
        raise ValueError(
            f"{record_id}.{field} = {value!r} does not match the required "
            f"'{TIMESTAMP_FORMAT}' timestamp format (whole seconds, UTC, "
            "trailing 'Z'). Use timestamps.now() to produce timestamps; "
            "do not hand-write or copy fractional-second/offset values."
        )
