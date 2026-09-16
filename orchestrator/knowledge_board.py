"""Knowledge board: persistent peer learning and institutional memory.

Design mirrors concerns_board.py deliberately, for the same reasons:
`orchestration/knowledge.yaml` holds ONLY state (status/timestamps/
verification provenance/counters), keyed by knowledge ID. Entry *content*
(title, category, tags, body) always comes from
knowledge_parser.parse_knowledge() — never duplicated here. See
knowledge_parser.py and docs/design/KNOWLEDGE_BOARD_DESIGN.md.

ID-collision prevention
------------------------
Filing a new entry pulls first and allocates the next K-### ID against
the freshly-pulled state — same pattern concerns_board.file_concern()
uses, for the same reason: this project has already hit real
duplicate-ID pain from skipping the pull (see docs/AGENTS.md).

Status ladder (longer than concerns' open/acknowledged/resolved/wontfix):
unverified -> agent-verified -> reviewer-verified -> confirmed, with
superseded and retracted as terminal side-states.

`record_retrieval` is the one genuine design deviation from the concerns
pattern: it performs no git operations at all (no pull, no commit, no
push). It fires on every `search_relevant()` call from C-75, and pushing
to origin on every read would turn routine briefings into a commit storm
plus a collision magnet. Trade-off: retrieval counts are eventually
consistent — the increment lands in the local `knowledge.yaml` and
whichever task's own commit picks it up next publishes it. A lost
increment on a discarded checkout is acceptable; a push race on every
read is not.
"""
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from src.orchestrator.knowledge_parser import KNOWLEDGE_DIR, parse_knowledge
from src.orchestrator.timestamps import now as _now
from src.orchestrator.timestamps import validate_timestamp_format as _validate_timestamp_format

BOARD_PATH = Path("orchestration/knowledge.yaml")

# One session ID per process lifetime, same purpose as concerns_board's:
# groups a single run's activity.log lines together.
SESSION_ID: str = uuid.uuid4().hex[:8]

VALID_STATUSES = {"unverified", "agent-verified", "reviewer-verified",
                  "confirmed", "superseded", "retracted"}

# Entries waiting on review surface as STALE after this long with no
# update. Longer than concerns' 24h: knowledge review is not as
# time-critical as an open concern, so the concern threshold is not
# reused verbatim. (D-316 addition — additive constant, no existing
# surface changed.)
KNOWLEDGE_STALE_AFTER_HOURS = 168
REVIEW_PENDING_STATUSES = {"unverified", "agent-verified"}
VALID_CATEGORIES = {"environment", "technique", "gotcha", "architecture",
                    "tooling", "process", "other"}

ACTIVITY_LOG = KNOWLEDGE_DIR / "activity.log"

_TIMESTAMP_FIELDS = ("created_at", "updated_at", "agent_verified_at",
                     "reviewer_verified_at")


@dataclass
class KnowledgeState:
    status: str = "unverified"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    agent_verified_by: Optional[str] = None
    agent_verified_at: Optional[str] = None
    verification_notes: Optional[str] = None
    reviewer_verified_by: Optional[str] = None
    reviewer_verified_at: Optional[str] = None
    superseded_by: Optional[str] = None
    retrieval_count: int = 0
    useful_count: int = 0


@dataclass
class Knowledge:
    """A merged view: filed content + tracked state."""
    id: str
    title: str
    status: str
    category: str
    tags: list[str] = field(default_factory=list)
    submitted_by: str = "unknown"
    related_task: Optional[str] = None
    supersedes: Optional[str] = None
    body: str = ""
    state: KnowledgeState = field(default_factory=KnowledgeState)


def _run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _load_states() -> dict:
    """Read raw state dict from knowledge.yaml. Empty dict if file is new."""
    if not BOARD_PATH.exists():
        return {}
    with open(BOARD_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _save_states(states: dict) -> None:
    """The single on-disk writer for knowledge.yaml. Validates every
    persisted timestamp against the shared contract before writing
    anything — see src.orchestrator.timestamps and reports/D-263_report.md
    for why this lives at the writer rather than being trusted by
    convention."""
    for knowledge_id, entry in states.items():
        if not isinstance(entry, dict):
            continue
        for f in _TIMESTAMP_FIELDS:
            _validate_timestamp_format(knowledge_id, f, entry.get(f))
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BOARD_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(states, fh, sort_keys=True, default_flow_style=False)


def _merge_knowledge() -> list[Knowledge]:
    """Pure read: derive the current knowledge list from knowledge/*.md +
    tracked state, with NO file writes. Safe to call as often as needed."""
    content = parse_knowledge()
    states = _load_states()
    merged = []
    for e in content:
        raw_state = states.get(e.id, {})
        state = KnowledgeState(**raw_state) if raw_state else KnowledgeState()
        merged.append(Knowledge(
            id=e.id, title=e.title, status=e.status, category=e.category,
            tags=e.tags, submitted_by=e.submitted_by,
            related_task=e.related_task, supersedes=e.supersedes, body=e.body,
            state=state,
        ))
    return merged


def sync() -> list[Knowledge]:
    """Re-derive the knowledge list AND persist it: writes knowledge.yaml
    (new files register as 'unverified'; existing state is preserved
    verbatim) and regenerates KNOWLEDGE_BOARD.md. Reads
    (list_knowledge/get_knowledge) use _merge_knowledge() instead so
    simply looking at the board never creates a spurious local diff."""
    merged = _merge_knowledge()
    existing = _load_states()
    new_states = {}
    for e in merged:
        if e.id in existing:
            new_states[e.id] = existing[e.id]
        else:
            ts = _now()
            new_states[e.id] = {
                "status": "unverified", "created_at": ts, "updated_at": ts,
                "agent_verified_by": None, "agent_verified_at": None,
                "verification_notes": None, "reviewer_verified_by": None,
                "reviewer_verified_at": None, "superseded_by": None,
                "retrieval_count": 0, "useful_count": 0,
            }
    _save_states(new_states)
    merged = _merge_knowledge()
    _write_board(merged)
    return merged


def _write_board(entries: list) -> None:
    from src.orchestrator import knowledge_report
    knowledge_report.write_board(entries)


def list_knowledge(status: Optional[str] = None, category: Optional[str] = None,
                   tags: Optional[list[str]] = None) -> list[Knowledge]:
    entries = _merge_knowledge()
    if status:
        entries = [e for e in entries if e.state.status == status]
    if category:
        entries = [e for e in entries if e.category == category.lower()]
    if tags:
        wanted = {t.strip().lower() for t in tags}
        entries = [e for e in entries if wanted & {t.lower() for t in e.tags}]
    return entries


def get_knowledge(knowledge_id: str) -> Optional[Knowledge]:
    for e in _merge_knowledge():
        if e.id == knowledge_id:
            return e
    return None


def is_stale(entry: Knowledge, now: Optional[datetime] = None) -> bool:
    """True if an unverified/agent-verified entry hasn't been touched in
    KNOWLEDGE_STALE_AFTER_HOURS. Always False for reviewer-verified and
    beyond — those are no longer waiting on a review look."""
    if entry.state.status not in REVIEW_PENDING_STATUSES or not entry.state.updated_at:
        return False
    now = now or datetime.now(timezone.utc)
    updated = datetime.strptime(
        entry.state.updated_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    return (now - updated) > timedelta(hours=KNOWLEDGE_STALE_AFTER_HOURS)


def _append_activity(line: str) -> None:
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now()}  [{SESSION_ID}]  {line}\n")


def _git_publish(message: str, extra_paths: Optional[list] = None) -> bool:
    """Commit + push knowledge.yaml, KNOWLEDGE_BOARD.md, and the activity
    log together (so none of the three can drift apart), plus any
    extra_paths — e.g. a newly filed knowledge/K-###_slug.md file."""
    from src.orchestrator.knowledge_report import BOARD_MD_PATH

    paths = [str(BOARD_PATH), str(BOARD_MD_PATH), str(ACTIVITY_LOG)]
    if extra_paths:
        paths.extend(str(p) for p in extra_paths)
    _run_git("add", *paths)
    # D-321/CN-007: fail closed if another lane has files staged -- a
    # pathspec-less `git commit` would sweep them into this commit.
    from src.orchestrator.worktree_guard import assert_expected_staged_only
    assert_expected_staged_only(paths)
    commit = _run_git("commit", "-m", message)
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        raise RuntimeError(f"git commit failed: {commit.stderr}")
    push = _run_git("push")
    return push.returncode == 0


_ID_RE = re.compile(r"K-(\d{3})")


def _next_id(existing_ids: list) -> str:
    max_n = 0
    for kid in existing_ids:
        m = _ID_RE.match(kid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"K-{max_n + 1:03d}"


def _slugify(title: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower())
    slug = slug.strip("-")[:max_len].strip("-")
    return slug or "entry"


def file_knowledge(
    title: str,
    body: str,
    category: str,
    submitted_by: str,
    tags: list[str],
    related_task: Optional[str] = None,
    supersedes: Optional[str] = None,
    publish: bool = True,
) -> Knowledge:
    """File a new knowledge entry: pulls first (ID-collision guard),
    allocates the next K-### ID against the freshly-pulled state, writes
    knowledge/K-###_<slug>.md (slug is a lowercased, hyphenated,
    ~40-char truncation of the title so filenames stay grep-friendly —
    knowledge entries are looked up by topic far more than by filing
    date, unlike concerns which embed the date), registers it in
    knowledge.yaml as 'unverified', regenerates the dashboard, and (by
    default) publishes immediately. `publish=False` is for tests and for
    batch-filing multiple entries before one combined push."""
    category = category.lower()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category {category!r}; must be one of {sorted(VALID_CATEGORIES)}.")
    if not title.strip():
        raise ValueError("file_knowledge requires a non-empty title.")
    if not body.strip():
        raise ValueError("file_knowledge requires a non-empty body.")

    if publish:
        pull = _run_git("pull", "--rebase")
        if pull.returncode != 0:
            raise RuntimeError(f"git pull failed, resolve manually before filing: {pull.stderr}")

    existing = parse_knowledge()
    knowledge_id = _next_id([e.id for e in existing])

    ts = _now()
    slug = _slugify(title)

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = KNOWLEDGE_DIR / f"{knowledge_id}_{slug}.md"

    tags_str = ", ".join(t.strip().lower() for t in (tags or []) if t.strip())
    lines = [
        f"## {knowledge_id} — {title.strip()}",
        "",
        "**Status:** unverified",
        f"**Category:** {category}",
        f"**Tags:** {tags_str}",
        f"**Submitted by:** {submitted_by}",
    ]
    if related_task:
        lines.append(f"**Related task:** {related_task}")
    if supersedes:
        lines.append(f"**Supersedes:** {supersedes}")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

    states = _load_states()
    states[knowledge_id] = {
        "status": "unverified", "created_at": ts, "updated_at": ts,
        "agent_verified_by": None, "agent_verified_at": None,
        "verification_notes": None, "reviewer_verified_by": None,
        "reviewer_verified_at": None, "superseded_by": None,
        "retrieval_count": 0, "useful_count": 0,
    }
    _save_states(states)
    merged = _merge_knowledge()
    _write_board(merged)

    _append_activity(f"{knowledge_id}  filed  {submitted_by}  category={category}  \"{title.strip()}\"")
    if publish:
        _git_publish(f"knowledge: file {knowledge_id} ({category}) - {title.strip()}", extra_paths=[path])

    return next(e for e in merged if e.id == knowledge_id)


def _transition(knowledge_id: str, new_status: str, notes: str, action_verb: str,
                valid_from: set, extra_updates: Optional[dict] = None) -> Knowledge:
    pull = _run_git("pull", "--rebase")
    if pull.returncode != 0:
        raise RuntimeError(f"git pull failed, resolve manually before {action_verb}: {pull.stderr}")

    entry_view = get_knowledge(knowledge_id)
    if entry_view is None:
        raise ValueError(f"No such knowledge entry: {knowledge_id}")
    if entry_view.state.status not in valid_from:
        raise RuntimeError(
            f"Cannot {action_verb} {knowledge_id}: status is {entry_view.state.status!r}, "
            f"expected one of {sorted(valid_from)}."
        )

    states = _load_states()
    entry = dict(states.get(knowledge_id, {}))
    entry.setdefault("status", entry_view.state.status)
    entry.setdefault("created_at", entry_view.state.created_at)
    entry["status"] = new_status
    entry["updated_at"] = _now()
    if extra_updates:
        # Provenance fields (agent_verified_*, reviewer_verified_*) are set
        # only by their own transition via extra_updates — never generically
        # from `notes`, so mark_reviewer_verified can never clobber the
        # agent's verification_notes set by the earlier mark_agent_verified.
        entry.update(extra_updates)
    states[knowledge_id] = entry
    _save_states(states)

    merged = _merge_knowledge()
    _write_board(merged)
    note_suffix = f'  "{notes}"' if notes else ""
    _append_activity(f"{knowledge_id}  {entry_view.state.status} -> {new_status}{note_suffix}")
    _git_publish(f"knowledge: {knowledge_id} {entry_view.state.status} -> {new_status}")
    return next(e for e in merged if e.id == knowledge_id)


def mark_agent_verified(knowledge_id: str, verified_by: str, verified_how: str) -> Knowledge:
    """Agent self-verification: unverified -> agent-verified. `verified_how`
    (what was actually checked) is required — mirrors resolve_concern's
    required notes. Records the agent provenance event; never touches the
    reviewer_* fields."""
    if not verified_how or not verified_how.strip():
        raise ValueError("mark_agent_verified requires verified_how explaining what was checked.")
    ts = _now()
    return _transition(
        knowledge_id, "agent-verified", verified_how, "agent-verify", {"unverified"},
        extra_updates={
            "agent_verified_by": verified_by,
            "agent_verified_at": ts,
            "verification_notes": verified_how,
        },
    )


def mark_reviewer_verified(knowledge_id: str, verified_by: str, notes: str = "") -> Knowledge:
    """Reviewer verification: agent-verified -> reviewer-verified only.
    Primary-Claude-only by convention, enforced here only via the
    status-transition guard. Records the reviewer provenance event and
    does NOT overwrite agent_verified_by/agent_verified_at/
    verification_notes — both provenance events stay readable after this
    call."""
    ts = _now()
    return _transition(
        knowledge_id, "reviewer-verified", notes, "reviewer-verify", {"agent-verified"},
        extra_updates={
            "reviewer_verified_by": verified_by,
            "reviewer_verified_at": ts,
        },
    )


def confirm_knowledge(knowledge_id: str, confirmed_by: str, notes: str = "") -> Knowledge:
    """Promote a reviewer-verified entry to confirmed institutional memory."""
    return _transition(
        knowledge_id, "confirmed", notes, "confirm", {"reviewer-verified"},
    )


def mark_useful(knowledge_id: str) -> Knowledge:
    """Increment `useful_count` only — any status, no status change. A
    lightweight counter bump, not a state transition: persists to
    knowledge.yaml and appends one activity line, but skips the git
    pull --rebase guard (concurrent useful-count bumps aren't a collision
    risk the way ID allocation is). Still publishes so the count isn't
    lost."""
    entry_view = get_knowledge(knowledge_id)
    if entry_view is None:
        raise ValueError(f"No such knowledge entry: {knowledge_id}")
    states = _load_states()
    entry = dict(states.get(knowledge_id, {}))
    entry["useful_count"] = int(entry.get("useful_count", 0) or 0) + 1
    entry["updated_at"] = _now()
    states[knowledge_id] = entry
    _save_states(states)
    merged = _merge_knowledge()
    _write_board(merged)
    _append_activity(f"{knowledge_id}  marked useful")
    _git_publish(f"knowledge: {knowledge_id} marked useful")
    return next(e for e in merged if e.id == knowledge_id)


def record_retrieval(knowledge_id: str) -> None:
    """Increment `retrieval_count` only. Called by the retrieval code in
    C-75, not directly by agents. Performs NO git operations at all — no
    pull, no commit, no push (see module docstring for the trade-off).
    The increment lands in the local knowledge.yaml and is published by
    whichever task's own commit picks it up next."""
    states = _load_states()
    if knowledge_id not in states:
        # Entries filed but never synced have no state row yet; create a
        # minimal one rather than dropping the signal.
        ts = _now()
        states[knowledge_id] = {
            "status": "unverified", "created_at": ts, "updated_at": ts,
            "agent_verified_by": None, "agent_verified_at": None,
            "verification_notes": None, "reviewer_verified_by": None,
            "reviewer_verified_at": None, "superseded_by": None,
            "retrieval_count": 0, "useful_count": 0,
        }
    states[knowledge_id]["retrieval_count"] = int(states[knowledge_id].get("retrieval_count", 0) or 0) + 1
    _save_states(states)


def supersede_knowledge(old_id: str, new_id: str, notes: str = "") -> Knowledge:
    """Mark `old_id` as superseded by `new_id`. Does not change `new_id`'s
    status."""
    if get_knowledge(new_id) is None:
        raise ValueError(f"No such knowledge entry: {new_id}")
    return _transition(
        old_id, "superseded", notes, "supersede",
        {"unverified", "agent-verified", "reviewer-verified", "confirmed"},
        extra_updates={"superseded_by": new_id},
    )


def retract_knowledge(knowledge_id: str, notes: str) -> Knowledge:
    """Retract an entry from any status. `notes` (why it was wrong) is
    required — a retraction with no explanation is as useless as a task
    report with no content."""
    if not notes or not notes.strip():
        raise ValueError("retract_knowledge requires notes explaining why it was wrong.")
    return _transition(
        knowledge_id, "retracted", notes, "retract",
        {"unverified", "agent-verified", "reviewer-verified", "confirmed", "superseded"},
    )


# --- Durable Knowledge (Layer 2: KNOW-* entries) ---
# Compatibility wrappers over src.orchestrator.durable_knowledge, which owns
# the Layer 2 lifecycle (create/validate/store/discover/search/rank/
# retrieve/inject/supersede). Layer 1 (K-### board above) is unchanged.
# New code should import durable_knowledge directly; these wrappers keep the
# earlier `search_durable_knowledge` / `get_durable_knowledge` /
# `list_durable_knowledge` call sites (delegator, CLI, tests) working.

from src.orchestrator import durable_knowledge as _durable

_DURABLE_KNOWLEDGE_DIR = _durable.DURABLE_DIR


def search_durable_knowledge(
    keywords: list[str],
    task_ids: list[str] | None = None,
    tags: list[str] | None = None,
    knowledge_types: list[str] | None = None,
    files: list[str] | None = None,
    after: str | None = None,
    limit: int = 10,
    include_superseded: bool = False,
    include_retracted: bool = False,
) -> list[dict]:
    """Explicit search over durable KNOW entries (see durable_knowledge).

    Filters (task IDs, tags, types, files, dates) are HARD: an entry missing
    a requested attribute is excluded. ``keywords`` affect ranking only.
    Superseded/retracted entries are excluded by default.
    """
    return _durable.search_entries(
        keywords=keywords, task_ids=task_ids, tags=tags,
        knowledge_types=knowledge_types, files=files, after=after,
        limit=limit, include_superseded=include_superseded,
        include_retracted=include_retracted,
    )


def get_durable_knowledge(knowledge_id: str) -> dict | None:
    """Retrieve a single durable knowledge entry by ID.

    Returns dict with id, title, type, status, confidence, path,
    front_matter, content — or None when unknown.
    """
    entry = _durable.get_entry(knowledge_id)
    if entry is None:
        return None
    counts = _durable.get_counts(knowledge_id)
    front_matter = {
        "id": entry.id, "type": entry.type, "title": entry.title,
        "created_at": entry.created_at, "updated_at": entry.updated_at,
        "status": entry.status, "priority": entry.priority,
        "tags": list(entry.tags), "keywords": list(entry.keywords),
        "task_ids": list(entry.task_ids), "phase": entry.phase,
        "agents": list(entry.agents), "files": list(entry.files),
        "commits": list(entry.commits), "related": list(entry.related),
        "supersedes": list(entry.supersedes), "confidence": entry.confidence,
    }
    if entry.superseded_by:
        front_matter["superseded_by"] = entry.superseded_by
    content = "---\n" + yaml.safe_dump(
        front_matter, sort_keys=False, default_flow_style=False,
        allow_unicode=True) + "---\n" + (entry.body or "")
    return {
        "id": entry.id, "title": entry.title, "type": entry.type,
        "status": entry.status, "confidence": entry.confidence,
        "path": entry.path or "", "front_matter": front_matter,
        "content": content,
        "retrieval_count": counts["retrieval_count"],
        "useful_count": counts["useful_count"],
    }


def list_durable_knowledge(
    knowledge_type: str | None = None,
    tag: str | None = None,
    task_id: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """List durable knowledge entries with optional hard filters."""
    return _durable.list_entries(
        knowledge_type=knowledge_type, tag=tag, task_id=task_id,
        limit=limit,
    )


_WORD_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split into a keyword set. Deliberately
    naive (see C-75 notes): tag/keyword overlap only, no stemming, no
    embeddings."""
    return set(_WORD_RE.findall(text.lower()))


def search_relevant(task, limit: int = 5, include_unverified: bool = False) -> list:
    """Rank knowledge board entries relevant to `task` for automatic
    contract injection (C-75). `task` may be any object/dict with
    `.title`/`.body` (or `["title"]`/`["body"]`) attributes, or a plain
    string treated as the combined title+body text.

    Scoring: for each eligible entry, score = count of the entry's tags
    that appear as substrings in the task's keyword set, +1 if any tag
    matches a filename/module token mentioned in the task text. Entries
    scoring 0 are dropped entirely (never pad results with irrelevant
    entries to fill `limit`). Sort by score desc, then useful_count desc,
    then retrieval_count asc (prefer less-seen entries among equally
    useful ones).

    Eligibility: by default (include_unverified=False, the path used by
    automatic contract injection) only 'reviewer-verified' and
    'confirmed' entries are eligible, per the design doc's status-ladder
    rationale. When include_unverified=True (e.g. an explicit manual
    search), 'unverified'/'agent-verified' entries are also eligible --
    callers doing this must visually distinguish them in their own
    output; this function does not do that for you.

    Contradiction handling: structural supersession only. Among
    candidates sharing >=2 tags, if one's `superseded_by` points at
    another candidate, the superseded entry is dropped and only the
    current one is kept. Two confirmed, unlinked, possibly-disagreeing
    entries are NOT treated as contradictory and are both returned
    independently -- true contradiction-without-supersession is a known,
    intentionally out-of-scope gap (see design doc point 10, the
    deferred K-maintenance task); this function does not attempt to
    detect disagreement from body text.

    `record_retrieval()` is called on every entry actually returned (not
    every candidate considered).
    """
    if isinstance(task, str):
        task_text = task
    elif isinstance(task, dict):
        task_text = f"{task.get('title', '')} {task.get('body', '')}"
    else:
        task_text = f"{getattr(task, 'title', '')} {getattr(task, 'body', '')}"

    task_keywords = _tokenize(task_text)
    if not task_keywords:
        return []

    eligible_statuses = {"reviewer-verified", "confirmed"}
    if include_unverified:
        eligible_statuses |= {"unverified", "agent-verified"}

    entries = [e for e in list_knowledge() if e.state.status in eligible_statuses]

    def _score(e) -> int:
        tags_lower = [t.lower() for t in e.tags]
        tag_hits = sum(1 for t in tags_lower if t in task_keywords)
        if tag_hits == 0:
            return 0
        # Filename/module mention bonus: any tag also appearing verbatim
        # in the task keyword set (e.g. task mentions "worktree_guard.py",
        # entry tagged "worktree") counts once, on top of tag overlap.
        bonus = 1 if any(t in task_keywords for t in tags_lower) else 0
        return tag_hits + bonus

    scored = [e for e in entries if _score(e) > 0]

    # Structural supersession: among candidates, drop an entry if its
    # successor is also a candidate.
    scored_ids = {e.id for e in scored}
    survivors = [
        e for e in scored
        if not (e.state.superseded_by and e.state.superseded_by in scored_ids)
    ]

    survivors.sort(
        key=lambda e: (-_score(e), -e.state.useful_count, e.state.retrieval_count)
    )
    results = survivors[:max(0, limit)]

    for e in results:
        record_retrieval(e.id)

    return results

