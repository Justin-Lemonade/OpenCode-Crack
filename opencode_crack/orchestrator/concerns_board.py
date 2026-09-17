"""Concerns board: an escalation channel for agents to flag process,
board-integrity, technical, or environment problems to Primary Claude /
project managers — separate from `delegated_tasks/` (what to build) and
`reports/<TASK_ID>_report.md` (what was done on one specific task). A
concern is for "something about the project itself needs attention," not
"here's my task output."

Design mirrors task_board.py deliberately, for the same reasons:
`orchestration/concerns.yaml` holds ONLY state (status/timestamps/
resolution notes), keyed by concern ID. Concern *content* (title,
severity, category, body) always comes from
concern_parser.parse_concerns() — never duplicated here. See
concern_parser.py and concerns/README.md.

ID-collision prevention
------------------------
Filing a new concern pulls first and allocates the next CN-### ID against
the freshly-pulled state — same pattern task_board.claim_task() uses for
task claims, for the same reason: this project has already hit real
duplicate-ID pain from skipping the pull (see docs/AGENTS.md). Concerns
get that guard from day one.
"""
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

from opencode_crack.orchestrator.concern_parser import CONCERNS_DIR, parse_concerns
from opencode_crack.orchestrator.timestamps import now as _now
from opencode_crack.orchestrator.timestamps import validate_timestamp_format as _validate_timestamp_format

BOARD_PATH = Path("orchestration/concerns.yaml")

# One session ID per process lifetime, same purpose as task_board's: groups
# a single run's activity.log lines together.
SESSION_ID: str = uuid.uuid4().hex[:8]

VALID_STATUSES = {"open", "acknowledged", "resolved", "wontfix"}
OPEN_STATUSES = {"open", "acknowledged"}
VALID_SEVERITIES = {"BLOCKER", "HIGH", "MEDIUM", "LOW"}
VALID_CATEGORIES = {"process", "board-integrity", "technical", "environment", "quality", "other"}

ACTIVITY_LOG = CONCERNS_DIR / "activity.log"

# A concern sitting open/acknowledged this long with no further update is
# itself a coherence failure worth surfacing — mirrors task_board's
# STALE_AFTER_HOURS, but shorter: nobody is blocked on an unclaimed task
# existing, but someone IS waiting to be heard on a filed concern.
STALE_AFTER_HOURS = 24

_TIMESTAMP_FIELDS = ("created_at", "updated_at", "resolved_at")


@dataclass
class ConcernState:
    status: str = "open"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_notes: str = ""


@dataclass
class Concern:
    """A merged view: filed content + tracked state."""
    id: str
    title: str
    severity: str
    category: str
    raised_by: str
    related_task: Optional[str]
    body: str
    state: ConcernState = field(default_factory=ConcernState)


def _run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _load_states() -> dict:
    """Read raw state dict from concerns.yaml. Empty dict if file is new."""
    if not BOARD_PATH.exists():
        return {}
    with open(BOARD_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _save_states(states: dict) -> None:
    """The single on-disk writer for concerns.yaml. Validates every
    persisted timestamp against the shared contract before writing
    anything — see src.orchestrator.timestamps and reports/D-263_report.md
    for why this lives at the writer rather than being trusted by
    convention."""
    for concern_id, entry in states.items():
        if not isinstance(entry, dict):
            continue
        for f in _TIMESTAMP_FIELDS:
            _validate_timestamp_format(concern_id, f, entry.get(f))
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BOARD_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(states, fh, sort_keys=True, default_flow_style=False)


def _merge_concerns() -> list[Concern]:
    """Pure read: derive the current concern list from concerns/*.md +
    tracked state, with NO file writes. Safe to call as often as needed."""
    content = parse_concerns()
    states = _load_states()
    merged = []
    for c in content:
        raw_state = states.get(c.id, {})
        state = ConcernState(**raw_state) if raw_state else ConcernState()
        merged.append(Concern(
            id=c.id, title=c.title, severity=c.severity, category=c.category,
            raised_by=c.raised_by, related_task=c.related_task, body=c.body,
            state=state,
        ))
    return merged


def sync() -> list[Concern]:
    """Re-derive the concern list AND persist it: writes concerns.yaml
    (new files register as 'open'; existing state is preserved verbatim)
    and regenerates CONCERNS_BOARD.md. Reads (list_concerns/get_concern)
    use _merge_concerns() instead so simply looking at the board never
    creates a spurious local diff."""
    merged = _merge_concerns()
    existing = _load_states()
    new_states = {}
    for c in merged:
        if c.id in existing:
            new_states[c.id] = existing[c.id]
        else:
            ts = _now()
            new_states[c.id] = {
                "status": "open", "created_at": ts, "updated_at": ts,
                "resolved_at": None, "resolution_notes": "",
            }
    _save_states(new_states)
    merged = _merge_concerns()
    _write_board(merged)
    return merged


def _write_board(concerns: list) -> None:
    from opencode_crack.orchestrator import concerns_report
    concerns_report.write_board(concerns)


def list_concerns(status: Optional[str] = None, severity: Optional[str] = None) -> list[Concern]:
    concerns = _merge_concerns()
    if status:
        concerns = [c for c in concerns if c.state.status == status]
    if severity:
        concerns = [c for c in concerns if c.severity == severity.upper()]
    return concerns


def get_concern(concern_id: str) -> Optional[Concern]:
    for c in _merge_concerns():
        if c.id == concern_id:
            return c
    return None


def is_stale(concern: Concern, now: Optional[datetime] = None) -> bool:
    """True if an open/acknowledged concern hasn't been touched in
    STALE_AFTER_HOURS. Always False for resolved/wontfix."""
    if concern.state.status not in OPEN_STATUSES or not concern.state.updated_at:
        return False
    now = now or datetime.now(timezone.utc)
    updated = datetime.strptime(
        concern.state.updated_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    return (now - updated) > timedelta(hours=STALE_AFTER_HOURS)


def _append_activity(line: str) -> None:
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now()}  [{SESSION_ID}]  {line}\n")


def _git_publish(message: str, extra_paths: Optional[list] = None) -> bool:
    """Commit + push concerns.yaml, CONCERNS_BOARD.md, and the activity
    log together (so none of the three can drift apart), plus any
    extra_paths — e.g. a newly filed concerns/CN-###.md file."""
    from opencode_crack.orchestrator.concerns_report import BOARD_MD_PATH

    paths = [str(BOARD_PATH), str(BOARD_MD_PATH), str(ACTIVITY_LOG)]
    if extra_paths:
        paths.extend(str(p) for p in extra_paths)
    _run_git("add", *paths)
    # D-321/CN-007: fail closed if another lane has files staged -- a
    # pathspec-less `git commit` would sweep them into this commit.
    from opencode_crack.orchestrator.worktree_guard import assert_expected_staged_only
    assert_expected_staged_only(paths)
    commit = _run_git("commit", "-m", message)
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        raise RuntimeError(f"git commit failed: {commit.stderr}")
    push = _run_git("push")
    return push.returncode == 0


_ID_RE = re.compile(r"CN-(\d{3})")


def _next_id(existing_ids: list) -> str:
    max_n = 0
    for cid in existing_ids:
        m = _ID_RE.match(cid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"CN-{max_n + 1:03d}"


def file_concern(
    title: str,
    body: str,
    severity: str,
    raised_by: str,
    category: str = "other",
    related_task: Optional[str] = None,
    publish: bool = True,
) -> Concern:
    """File a new concern: pulls first (ID-collision guard), allocates the
    next CN-### ID against the freshly-pulled state, writes
    concerns/CN-###.md, registers it in concerns.yaml as 'open',
    regenerates the dashboard, and (by default) publishes immediately —
    an unpublished concern helps nobody. `publish=False` is for tests and
    for batch-filing multiple concerns before one combined push."""
    severity = severity.upper()
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity {severity!r}; must be one of {sorted(VALID_SEVERITIES)}.")
    category = category.lower()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category {category!r}; must be one of {sorted(VALID_CATEGORIES)}.")
    if not title.strip():
        raise ValueError("file_concern requires a non-empty title.")
    if not body.strip():
        raise ValueError("file_concern requires a non-empty body.")

    if publish:
        pull = _run_git("pull", "--rebase")
        if pull.returncode != 0:
            raise RuntimeError(f"git pull failed, resolve manually before filing: {pull.stderr}")

    existing = parse_concerns()
    concern_id = _next_id([c.id for c in existing])

    ts = _now()
    date_part = ts[:10]  # 'YYYY-MM-DD' prefix of the whole-second UTC timestamp

    CONCERNS_DIR.mkdir(parents=True, exist_ok=True)
    # Date is embedded in the filename (in addition to created_at in
    # concerns.yaml) so the creation date is visible directly from a file
    # listing/`git log`, without opening the file or the yaml — e.g.
    # browsing `concerns/` chronologically, or spotting at a glance how
    # old an unresolved concern is.
    path = CONCERNS_DIR / f"{concern_id}_{date_part}.md"

    lines = [
        f"## {concern_id} — {title.strip()}",
        "",
        f"**Severity:** {severity}",
        f"**Category:** {category}",
        f"**Raised by:** {raised_by}",
    ]
    if related_task:
        lines.append(f"**Related task:** {related_task}")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

    states = _load_states()
    states[concern_id] = {
        "status": "open", "created_at": ts, "updated_at": ts,
        "resolved_at": None, "resolution_notes": "",
    }
    _save_states(states)
    merged = _merge_concerns()
    _write_board(merged)

    _append_activity(f"{concern_id}  filed  {raised_by}  severity={severity}  \"{title.strip()}\"")
    if publish:
        _git_publish(f"concern: file {concern_id} ({severity}) - {title.strip()}", extra_paths=[path])

    return next(c for c in merged if c.id == concern_id)


def _transition(concern_id: str, new_status: str, notes: str, action_verb: str, valid_from: set) -> Concern:
    pull = _run_git("pull", "--rebase")
    if pull.returncode != 0:
        raise RuntimeError(f"git pull failed, resolve manually before {action_verb}: {pull.stderr}")

    concern = get_concern(concern_id)
    if concern is None:
        raise ValueError(f"No such concern: {concern_id}")
    if concern.state.status not in valid_from:
        raise RuntimeError(
            f"Cannot {action_verb} {concern_id}: status is {concern.state.status!r}, "
            f"expected one of {sorted(valid_from)}."
        )

    states = _load_states()
    entry = dict(states.get(concern_id, {}))
    entry.setdefault("status", concern.state.status)
    entry.setdefault("created_at", concern.state.created_at)
    entry["status"] = new_status
    entry["updated_at"] = _now()
    if new_status in ("resolved", "wontfix"):
        entry["resolved_at"] = _now()
    elif new_status == "open":
        # Reopening un-resolves it -- don't leave a stale resolved_at
        # sitting around implying it's still closed.
        entry["resolved_at"] = None
    if notes:
        entry["resolution_notes"] = notes
    states[concern_id] = entry
    _save_states(states)

    merged = _merge_concerns()
    _write_board(merged)
    note_suffix = f'  "{notes}"' if notes else ""
    _append_activity(f"{concern_id}  {concern.state.status} -> {new_status}{note_suffix}")
    _git_publish(f"concern: {concern_id} {concern.state.status} -> {new_status}")
    return next(c for c in merged if c.id == concern_id)


def acknowledge_concern(concern_id: str, notes: str = "") -> Concern:
    """Primary Claude (or a human) confirms they've seen the concern.
    Doesn't resolve it — just stops it reading as 'nobody has looked at
    this yet.'"""
    return _transition(concern_id, "acknowledged", notes, "acknowledge", {"open"})


def resolve_concern(concern_id: str, notes: str) -> Concern:
    """Mark a concern addressed. `notes` (what was done about it) is
    required — a resolution with no explanation is as useless as a task
    report with no content."""
    if not notes:
        raise ValueError("resolve_concern requires notes explaining what was done.")
    return _transition(concern_id, "resolved", notes, "resolve", {"open", "acknowledged"})


def dismiss_concern(concern_id: str, notes: str) -> Concern:
    """Acknowledge the concern but explicitly decide not to act on it.
    `notes` (why) is required, same reasoning as resolve_concern."""
    if not notes:
        raise ValueError("dismiss_concern requires notes explaining why.")
    return _transition(concern_id, "wontfix", notes, "dismiss", {"open", "acknowledged"})


def reopen_concern(concern_id: str, notes: str = "") -> Concern:
    """Send a resolved/wontfix concern back to open — e.g. the fix didn't
    actually land, or the situation recurred. Same 'verify before trusting
    a closed status' principle as task review."""
    return _transition(concern_id, "open", notes, "reopen", {"resolved", "wontfix"})
