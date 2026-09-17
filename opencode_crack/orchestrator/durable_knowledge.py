"""Durable institutional knowledge (Layer 2: ``KNOW-YYYYMMDD-NNN`` entries).

Architecture (see knowledge/README.md "Relationship to Existing Systems")
------------------------------------------------------------------------
This repo has TWO knowledge layers with different jobs:

* **Layer 1 — operational knowledge (``K-###``)**: short discoveries owned by
  ``knowledge_parser.py`` (content) + ``knowledge_board.py`` (state in
  ``orchestration/knowledge.yaml``). Verification ladder
  ``unverified -> agent-verified -> reviewer-verified -> confirmed`` and the
  ``retrieval_count``/``useful_count`` counters live there. Do NOT re-create
  that machinery here.
* **Layer 2 — durable institutional knowledge (``KNOW-YYYYMMDD-NNN``)**: project
  understanding, architecture, decisions, blockers, learnings, and task
  context curated for fresh agents. THIS module owns its full lifecycle::

      CREATE -> VALIDATE -> STORE -> DISCOVER -> SEARCH -> RANK -> RETRIEVE
        -> INJECT -> USE -> VERIFY -> UPDATE / SUPERSEDE

Content authority is the Markdown file's YAML front matter (human-readable,
git-versioned). The only machine sidecar is ``orchestration/
durable_knowledge.yaml`` which tracks ``retrieval_count``/``useful_count``
per KNOW ID plus transition notes — mirroring why ``knowledge.yaml`` exists
for Layer 1 (state separate from content). No git operations happen on reads.

Verification policy (explicit — differs from Layer 1 by design)
---------------------------------------------------------------
Durable entries are curated at write time (by the worker) and checked at
review time (``review_work.md``), so there is no second ``unverified -> ...``
ladder here. Retrieval eligibility:

* automatic worker context (``retrieve_for_task`` / contracts):
  ``status`` in ``{active, resolved}`` only. ``resolved`` blockers stay
  eligible because "how we fixed X" is reusable context.
* explicit/manual search (``search_entries`` / CLI): same, plus opt-in
  ``include_superseded`` / ``include_retracted`` flags.
* ``superseded``: excluded in favor of the superseding entry
  (``superseded_by``); ``retracted``: excluded from normal retrieval.
* ``confidence`` (high/medium/low) is a *ranking* signal, not a gate:
  low-confidence entries still surface, ranked lower.

Scoring (exact — ``retrieve_for_task``)
---------------------------------------
Hard relevance (large, additive):

* exact task relationship (``task_id`` in ``entry.task_ids``): ``+50``
* exact file relationship (query path equals an entry ``files`` path, or
  basenames match, or one path endswith the other): ``+30``
* explicit relationship expansion (entry references, or is referenced by,
  an already-selected entry via ``related``): ``+20``

Soft relevance (smaller, additive):

* tag overlap: ``+8`` per shared tag (lowercased), capped at ``+24``
* keyword overlap per keyword (stopwords skipped): title hit ``+3``,
  tags/keywords-element hit ``+2``, body hit ``+1``
* confidence: high ``+2``, medium ``+1``, low ``+0``
* ``useful_count``: ``+1`` each, capped at ``+5`` (secondary signal)

Ordering: score desc, ``useful_count`` desc, ``retrieval_count`` asc (prefer
less-seen entries among equally useful ones), ``updated_at`` desc,
``id`` asc. Score-0 entries are dropped, never padded. Returned entries get
``record_entry_retrieval()`` when ``record`` is set (the default); pure
contract builders pass ``record=False`` and the CLI ``contract`` /
``dispatcher`` handoff paths do the recording, so rendering stays
deterministic.

``search_entries`` (explicit search) uses the same per-keyword weights but
treats ``task_ids``/``tags``/``files``/``knowledge_types``/``status``/
``after`` as HARD filters (CLI semantics: "show me X"), while
``retrieve_for_task`` treats task/files as score boosts (contract semantics:
"whatever is relevant to this task").

Contract hygiene: excerpt lines containing generic delegation-protocol marker
phrases (``TOOLS``, ``EXPECTED OUTPUT``, ...) are dropped from rendered
knowledge so a knowledge excerpt can never smuggle protocol prose into the
compact contract (which promises to exclude it). Do not put such phrases in
titles/summaries either — titles travel verbatim.

IDs: ``KNOW-YYYYMMDD-NNN`` with ``NNN`` globally unique per date across the
whole ``knowledge/`` tree (NOT per subdirectory — per-directory counters
caused real duplicate-ID collisions). Filenames must start with the entry ID:
``<ID>-<slug>.md``.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DURABLE_DIR = Path("knowledge")
SIDECAR_PATH = Path("orchestration/durable_knowledge.yaml")

ID_RE = re.compile(r"^KNOW-(\d{8})-(\d{3})$")

VALID_TYPES = {"project", "architecture", "decision", "blocker", "learning", "task"}
VALID_STATUSES = {"active", "superseded", "resolved", "retracted"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_CONFIDENCE = {"high", "medium", "low"}

# Eligible for automatic injection into worker contracts.
RETRIEVABLE_STATUSES = {"active", "resolved"}

TYPE_SUBDIR = {
    "project": "project",
    "architecture": "architecture",
    "decision": "decisions",
    "blocker": "blockers",
    "learning": "learnings",
    "task": "tasks/active",
}

STOPWORDS = frozenset({
    # Articles, prepositions, conjunctions, pronouns, auxiliaries.
    "the", "and", "for", "with", "from", "that", "this", "into", "are",
    "was", "were", "been", "has", "have", "had", "not", "but", "you",
    "your", "our", "its", "via", "per", "all", "any", "can", "will",
    "over", "under", "out", "new", "about", "above", "after", "again",
    "against", "also", "because", "before", "between", "both", "each",
    "how", "more", "most", "other", "same", "several", "should", "such",
    "than", "then", "there", "these", "they", "them", "too", "very",
    "what", "when", "where", "which", "while", "who", "just", "only",
    "down", "own", "once", "here", "now", "get", "got", "make", "made",
    "may", "might", "must", "would", "could", "every", "many", "much",
    "like", "well", "done", "does", "did", "say", "across", "within",
    "without", "upon", "task", "use", "using",
    # Two-letter glue: kept out because substring matching makes them match
    # almost everything ("to" is inside "stoneware", "is" inside "history").
    # Genuine 2-letter technical tokens (db, os, ui, go) still reach
    # retrieval via file references and tags, not keyword substrings.
    "to", "of", "in", "on", "at", "or", "is", "it", "as", "by", "be",
    "an", "no", "so", "up", "if", "do", "go", "we", "us", "ok", "am",
    "my", "me", "he", "re", "or",
})

# Phrases belonging to the delegation protocol, never to knowledge excerpts.
# Keeps the compact contract's "no generic protocol prose" promise even when
# a knowledge body quotes protocol text (e.g. a workflow doc showing an
# `orchestrate submit` example command).
PROTOCOL_MARKERS = (
    "You are a delegated coding agent",
    "TOOLS",
    "EXPECTED OUTPUT",
    "SUCCESS CRITERIA",
    "END OF SESSION",
    "orchestrate submit",
)

CONFIDENCE_BONUS = {"high": 2, "medium": 1, "low": 0}

TASK_ID_BONUS = 50
FILE_BONUS = 30
RELATED_BONUS = 20
TAG_HIT = 8
TAG_CAP = 24
TITLE_HIT = 3
TAGKW_HIT = 2
BODY_HIT = 1
USEFUL_CAP = 5


@dataclass
class DurableKnowledge:
    """One parsed durable knowledge entry (content view)."""

    id: str
    type: str
    title: str
    created_at: str
    updated_at: str
    status: str = "active"
    priority: str = "medium"
    tags: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    task_ids: list = field(default_factory=list)
    phase: Optional[str] = None
    agents: list = field(default_factory=list)
    files: list = field(default_factory=list)
    commits: list = field(default_factory=list)
    related: list = field(default_factory=list)
    supersedes: list = field(default_factory=list)
    superseded_by: Optional[str] = None
    confidence: str = "medium"
    body: str = ""
    path: Optional[str] = None


@dataclass
class ScoredKnowledge:
    """A retrieved entry plus its score and human-readable reasons."""

    entry: DurableKnowledge
    score: int
    reasons: list


# --------------------------------------------------------------------------
# Parsing / validation
# --------------------------------------------------------------------------

def _split_front_matter(content: str):
    """Split ``(front_matter_dict, body)``; tolerant of CRLF line endings."""
    normalized = content.replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", normalized, re.DOTALL)
    if not match:
        return None, normalized
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, normalized
    if not isinstance(data, dict):
        return None, normalized
    return data, match.group(2).strip()


def _as_str_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def validate_entry_dict(data: dict) -> list:
    """Return a list of validation problems (empty == valid)."""
    problems = []
    if not isinstance(data, dict):
        return ["front matter is not a mapping"]
    for f in ("id", "type", "title", "created_at", "updated_at", "status"):
        if f not in data or data[f] is None or (isinstance(data[f], str) and not data[f].strip()):
            problems.append(f"missing required field '{f}'")
    if data.get("id") is not None and not ID_RE.match(str(data["id"])):
        problems.append(f"invalid id {data.get('id')!r}: expected KNOW-YYYYMMDD-NNN")
    if data.get("type") is not None and str(data.get("type")) not in VALID_TYPES:
        problems.append(f"invalid type {data.get('type')!r}")
    if data.get("status") is not None and str(data.get("status")) not in VALID_STATUSES:
        problems.append(f"invalid status {data.get('status')!r}")
    if data.get("priority") is not None and str(data.get("priority")) not in VALID_PRIORITIES:
        problems.append(f"invalid priority {data.get('priority')!r}")
    if data.get("confidence") is not None and str(data.get("confidence")) not in VALID_CONFIDENCE:
        problems.append(f"invalid confidence {data.get('confidence')!r}")
    for f in ("tags", "keywords", "task_ids", "agents", "files", "commits", "related"):
        if f in data and data[f] is not None and not isinstance(data[f], list):
            problems.append(f"field '{f}' must be a list")
    supersedes = data.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, (list, str)):
        problems.append("field 'supersedes' must be a list or string")
    return problems


def validate_file(path: Path) -> list:
    """Validate one KNOW file on disk; returns problems (empty == valid)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"unreadable: {e}"]
    data, _ = _split_front_matter(content)
    if data is None:
        return ["missing or unparsable YAML front matter"]
    problems = validate_entry_dict(data)
    entry_id = str(data.get("id", ""))
    if ID_RE.match(entry_id) and entry_id not in path.name:
        problems.append(f"filename {path.name!r} does not contain entry id {entry_id!r}")
    return problems


def _entry_from_dict(data: dict, body: str, path: Optional[Path] = None) -> DurableKnowledge:
    return DurableKnowledge(
        id=str(data.get("id", "")),
        type=str(data.get("type", "")),
        title=str(data.get("title", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        status=str(data.get("status", "active")),
        priority=str(data.get("priority", "medium") or "medium"),
        tags=_as_str_list(data.get("tags")),
        keywords=_as_str_list(data.get("keywords")),
        task_ids=_as_str_list(data.get("task_ids")),
        phase=data.get("phase"),
        agents=_as_str_list(data.get("agents")),
        files=_as_str_list(data.get("files")),
        commits=_as_str_list(data.get("commits")),
        related=_as_str_list(data.get("related")),
        supersedes=_as_str_list(data.get("supersedes")),
        superseded_by=str(data["superseded_by"]) if data.get("superseded_by") else None,
        confidence=str(data.get("confidence", "medium") or "medium"),
        body=body,
        path=str(path) if path else None,
    )


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _resolve_dir(directory: Optional[Path] = None) -> Path:
    return directory if directory is not None else DURABLE_DIR


def discover(directory: Optional[Path] = None) -> list:
    """Read every ``KNOW-*.md`` file under ``directory`` (recursive).

    Invalid files are skipped (never crash discovery); duplicate IDs resolve
    deterministically to the first sorted path — see :func:`duplicates` to
    surface the underlying data problem instead of hiding it.
    """
    base = _resolve_dir(directory)
    if not base.exists():
        return []
    by_id: dict = {}
    for path in sorted(base.rglob("KNOW-*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        data, body = _split_front_matter(content)
        if data is None or validate_entry_dict(data):
            continue
        entry = _entry_from_dict(data, body, path)
        by_id.setdefault(entry.id, entry)
    return [by_id[k] for k in sorted(by_id)]


def duplicates(directory: Optional[Path] = None) -> dict:
    """Map duplicated KNOW IDs to the sorted list of paths claiming them."""
    base = _resolve_dir(directory)
    if not base.exists():
        return {}
    claimants: dict = {}
    for path in sorted(base.rglob("KNOW-*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        data, _ = _split_front_matter(content)
        if data is None or not ID_RE.match(str(data.get("id", ""))):
            continue
        claimants.setdefault(str(data["id"]), []).append(str(path))
    return {k: v for k, v in claimants.items() if len(v) > 1}


def get_entry(knowledge_id: str, directory: Optional[Path] = None) -> Optional[DurableKnowledge]:
    """Retrieve one entry by ID (deterministic first-sorted-path on dupes)."""
    for entry in discover(directory):
        if entry.id == knowledge_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Sidecar counters (retrieval_count / useful_count + transition notes)
# --------------------------------------------------------------------------

def _resolve_sidecar(sidecar_path: Optional[Path] = None) -> Path:
    return sidecar_path if sidecar_path is not None else SIDECAR_PATH


def _load_sidecar(sidecar_path: Optional[Path] = None) -> dict:
    path = _resolve_sidecar(sidecar_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _save_sidecar(states: dict, sidecar_path: Optional[Path] = None) -> None:
    path = _resolve_sidecar(sidecar_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(states, fh, sort_keys=True, default_flow_style=False)


def get_counts(knowledge_id: str, sidecar_path: Optional[Path] = None) -> dict:
    row = _load_sidecar(sidecar_path).get(knowledge_id, {})
    return {
        "retrieval_count": int(row.get("retrieval_count", 0) or 0),
        "useful_count": int(row.get("useful_count", 0) or 0),
    }


def record_entry_retrieval(knowledge_id: str, sidecar_path: Optional[Path] = None) -> None:
    """Increment ``retrieval_count``. No git operations (same trade-off as
    Layer 1's ``record_retrieval``): the increment is published by whichever
    task commit picks up the sidecar next. Never raises for I/O problems."""
    try:
        states = _load_sidecar(sidecar_path)
        row = dict(states.get(knowledge_id, {}))
        row["retrieval_count"] = int(row.get("retrieval_count", 0) or 0) + 1
        states[knowledge_id] = row
        _save_sidecar(states, sidecar_path)
    except OSError:
        pass


def mark_entry_useful(knowledge_id: str, directory: Optional[Path] = None,
                      sidecar_path: Optional[Path] = None) -> dict:
    """Increment ``useful_count`` for an entry workers actually used."""
    if get_entry(knowledge_id, directory) is None:
        raise ValueError(f"No such durable knowledge entry: {knowledge_id}")
    states = _load_sidecar(sidecar_path)
    row = dict(states.get(knowledge_id, {}))
    row["useful_count"] = int(row.get("useful_count", 0) or 0) + 1
    states[knowledge_id] = row
    _save_sidecar(states, sidecar_path)
    return get_counts(knowledge_id, sidecar_path)


# --------------------------------------------------------------------------
# CREATE (production writer — generates exactly what the reader parses)
# --------------------------------------------------------------------------

def _slugify(title: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower())
    return slug.strip("-")[:max_len].strip("-") or "entry"


def _now_ts() -> str:
    from opencode_crack.orchestrator.timestamps import now as _now
    return _now()


def _next_number(date_str: str, directory: Optional[Path] = None) -> int:
    """Next globally-unique NNN for ``date_str`` across the whole tree."""
    max_n = 0
    for entry in discover(directory):
        m = ID_RE.match(entry.id)
        if m and m.group(1) == date_str:
            max_n = max(max_n, int(m.group(2)))
    return max_n + 1


def _inline(items: list) -> str:
    return "[" + ", ".join(str(i) for i in items) + "]"


def file_entry(title: str, body: str, knowledge_type: str,
               tags: Optional[list] = None, keywords: Optional[list] = None,
               task_ids: Optional[list] = None, files: Optional[list] = None,
               related: Optional[list] = None, supersedes: Optional[list] = None,
               priority: str = "medium", confidence: str = "medium",
               agents: Optional[list] = None, phase: Optional[str] = None,
               date_str: Optional[str] = None,
               directory: Optional[Path] = None) -> DurableKnowledge:
    """CREATE a durable knowledge entry through the production writer.

    Allocates the next globally-unique ``KNOW-<date>-NNN`` ID, writes
    ``<ID>-<slug>.md`` under the type's subdirectory, and re-reads the file
    to prove writer/reader compatibility. No git operations and no sidecar
    writes: the filing task's own commit publishes the file, and counters
    start at zero on first retrieval/use.
    """
    knowledge_type = (knowledge_type or "").lower()
    if knowledge_type not in VALID_TYPES:
        raise ValueError(f"Invalid type {knowledge_type!r}; must be one of {sorted(VALID_TYPES)}.")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority {priority!r}.")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"Invalid confidence {confidence!r}.")
    if not (title or "").strip():
        raise ValueError("file_entry requires a non-empty title.")
    if not (body or "").strip():
        raise ValueError("file_entry requires a non-empty body.")

    base = _resolve_dir(directory)
    if date_str is None:
        date_str = _now_ts()[:10].replace("-", "")
    if not re.match(r"^\d{8}$", date_str):
        raise ValueError(f"date_str must be YYYYMMDD, got {date_str!r}.")
    knowledge_id = f"KNOW-{date_str}-{_next_number(date_str, base):03d}"

    subdir = base / TYPE_SUBDIR[knowledge_type]
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{knowledge_id}-{_slugify(title)}.md"
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite existing {path}.")

    ts = _now_ts()
    front = [
        "---",
        f"id: {knowledge_id}",
        f"type: {knowledge_type}",
        f"title: {title.strip()}",
        f"created_at: {ts}",
        f"updated_at: {ts}",
        "status: active",
        f"priority: {priority}",
        f"tags: {_inline([t.strip().lower() for t in (tags or []) if str(t).strip()])}",
        f"keywords: {_inline([k.strip() for k in (keywords or []) if str(k).strip()])}",
        f"task_ids: {_inline(task_ids or [])}",
        f"phase: {phase if phase else ''}",
        f"agents: {_inline(agents or [])}",
        f"files: {_inline(files or [])}",
        "commits: []",
        f"related: {_inline(related or [])}",
        f"supersedes: {_inline(supersedes or [])}",
        f"confidence: {confidence}",
        "---",
        "",
        f"# {title.strip()}",
        "",
        "## Summary",
        "",
        body.strip(),
        "",
    ]
    path.write_text("\n".join(front), encoding="utf-8")

    # Writer/reader compatibility by construction: re-read what we wrote.
    reread = get_entry(knowledge_id, base)
    if reread is None or reread.title != title.strip():
        raise RuntimeError(f"Wrote {path} but could not read it back.")
    return reread


# --------------------------------------------------------------------------
# UPDATE / SUPERSEDE (front-matter transitions; body stays immutable)
# --------------------------------------------------------------------------

def _rewrite_front_matter(path: Path, updates: dict, sidecar_note: Optional[dict] = None,
                          sidecar_path: Optional[Path] = None,
                          directory: Optional[Path] = None) -> DurableKnowledge:
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", content, re.DOTALL)
    if not match:
        raise ValueError(f"{path} has no parseable front matter.")
    data = yaml.safe_load(match.group(1))
    data.update(updates)
    problems = validate_entry_dict(data)
    if problems:
        raise ValueError(f"Transition would invalidate {path}: {problems}")
    new_content = "---\n" + yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    ) + "---\n" + match.group(2).lstrip("\n")
    path.write_text(new_content, encoding="utf-8")
    if sidecar_note:
        states = _load_sidecar(sidecar_path)
        row = dict(states.get(data["id"], {}))
        row.update(sidecar_note)
        states[data["id"]] = row
        _save_sidecar(states, sidecar_path)
    # Re-resolve through the known tree root (never by walking up from the
    # file: a caller-supplied tree may not be named "knowledge").
    return get_entry(str(data["id"]), _resolve_dir(directory))


def _locate(knowledge_id: str, directory: Optional[Path] = None) -> Path:
    base = _resolve_dir(directory)
    for path in sorted(base.rglob("KNOW-*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        data, _ = _split_front_matter(content)
        if data and str(data.get("id")) == knowledge_id:
            return path
    raise ValueError(f"No such durable knowledge entry: {knowledge_id}")


def supersede_entry(old_id: str, new_id: str, notes: str = "",
                    directory: Optional[Path] = None,
                    sidecar_path: Optional[Path] = None) -> DurableKnowledge:
    """Mark ``old_id`` superseded by ``new_id``. Normal retrieval afterwards
    prefers ``new_id`` (``superseded`` entries are ineligible)."""
    _locate(new_id, directory)  # fail fast on unknown successor
    old_path = _locate(old_id, directory)
    if old_id == new_id:
        raise ValueError("An entry cannot supersede itself.")
    return _rewrite_front_matter(
        old_path,
        {"status": "superseded", "superseded_by": new_id, "updated_at": _now_ts()},
        {"supersede_note": notes} if notes else None,
        sidecar_path,
        directory,
    )


def resolve_entry(knowledge_id: str, notes: str = "",
                  directory: Optional[Path] = None,
                  sidecar_path: Optional[Path] = None) -> DurableKnowledge:
    """Mark an entry (typically a blocker) resolved. Resolved entries stay
    eligible for retrieval — "how we fixed X" is reusable context."""
    path = _locate(knowledge_id, directory)
    return _rewrite_front_matter(
        path,
        {"status": "resolved", "updated_at": _now_ts()},
        {"resolve_note": notes} if notes else None,
        sidecar_path,
        directory,
    )


def retract_entry(knowledge_id: str, reason: str,
                  directory: Optional[Path] = None,
                  sidecar_path: Optional[Path] = None) -> DurableKnowledge:
    """Retract a wrong entry. ``reason`` is required and kept in the sidecar;
    the body is left untouched."""
    if not (reason or "").strip():
        raise ValueError("retract_entry requires a reason explaining why it was wrong.")
    path = _locate(knowledge_id, directory)
    return _rewrite_front_matter(
        path,
        {"status": "retracted", "updated_at": _now_ts()},
        {"retract_reason": reason.strip()},
        sidecar_path,
        directory,
    )


# --------------------------------------------------------------------------
# Tokenization / matching helpers
# --------------------------------------------------------------------------

def tokenize(text: str) -> list:
    """Lowercased alphanumeric tokens minus stopwords (deterministic order).

    Minimum length 3: shorter tokens are almost always glue ("to", "of") or
    match by accident as substrings; real short technical tokens (file
    stems, tags, IDs) enter retrieval through the file/tag channels.
    """
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 3]


def _basename(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1].lower()


def _file_hit(query_file: str, entry_files: list) -> Optional[str]:
    """Match a query file against entry files (exact, basename, affix).

    Affix matches must fall on a path boundary (``worktree_guard.py``
    matches ``src/orchestrator/worktree_guard.py`` but ``x.py`` never
    matches ``complex.py``).
    """
    q = query_file.replace("\\", "/").strip().strip("`'\".,;:()[]")
    if not q:
        return None
    qb = _basename(q)
    for ef in entry_files:
        e = str(ef).replace("\\", "/").strip()
        if not e:
            continue
        eb = _basename(e)
        if q.lower() == e.lower() or qb == eb:
            return str(ef)
        ql, el = q.lower(), e.lower()
        if (ql.endswith("/" + el) or el.endswith("/" + ql)):
            return str(ef)
    return None


def _section_text(body: str, headings: tuple) -> str:
    """Extract the text under any of ``headings`` (case-insensitive ``##``)."""
    lines = body.splitlines()
    wanted = {h.lower() for h in headings}
    capture: list = []
    active = False
    for line in lines:
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line.strip())
        if m:
            active = m.group(1).strip().lower() in wanted
            continue
        if active:
            capture.append(line)
    return "\n".join(capture).strip()


def _bullets(text: str) -> list:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*+]\s+(.+?)\s*$", line)
        if not m:
            continue
        item = re.sub(r"\[ \]\s*", "", m.group(1)).strip()
        if item and not any(marker in item for marker in PROTOCOL_MARKERS):
            out.append(item)
    return out


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


# --------------------------------------------------------------------------
# SEARCH (explicit: filters are hard) + RANK
# --------------------------------------------------------------------------

def _counts_map(ids: list, sidecar_path: Optional[Path] = None) -> dict:
    states = _load_sidecar(sidecar_path)
    out = {}
    for i in ids:
        row = states.get(i, {})
        out[i] = (int(row.get("retrieval_count", 0) or 0),
                  int(row.get("useful_count", 0) or 0))
    return out


def _ranked(pairs: list, counts: dict) -> list:
    """Deterministic ranking via stable multi-pass sort (least significant
    key first): id asc, then updated_at desc (recency), then
    retrieval_count asc (prefer less-seen), then useful_count desc, then
    score desc. ``pairs`` is a list of ``(entry, score)``."""
    ranked = list(pairs)
    ranked.sort(key=lambda p: p[0].id)
    ranked.sort(key=lambda p: p[0].updated_at or "", reverse=True)
    ranked.sort(key=lambda p: counts.get(p[0].id, (0, 0))[0])
    ranked.sort(key=lambda p: -counts.get(p[0].id, (0, 0))[1])
    ranked.sort(key=lambda p: -p[1])
    return ranked


def search_entries(keywords: Optional[list] = None,
                   task_ids: Optional[list] = None,
                   tags: Optional[list] = None,
                   knowledge_types: Optional[list] = None,
                   files: Optional[list] = None,
                   status: Optional[str] = None,
                   confidence: Optional[str] = None,
                   after: Optional[str] = None,
                   include_superseded: bool = False,
                   include_retracted: bool = False,
                   limit: int = 10,
                   directory: Optional[Path] = None,
                   sidecar_path: Optional[Path] = None) -> list:
    """Explicit search: filters are HARD (an entry missing a requested
    task/tag/file/type is excluded); ``keywords`` only affect ranking.

    Returns dicts ``{id, title, type, status, confidence, path,
    relevance_score, tags, task_ids, files, useful_count,
    retrieval_count}`` ordered best first. With no keywords and no filters,
    all eligible entries are returned ordered by usefulness/recency.
    """
    entries = discover(directory)
    if not include_superseded:
        entries = [e for e in entries if e.status != "superseded"]
    if not include_retracted:
        entries = [e for e in entries if e.status != "retracted"]
    if status:
        entries = [e for e in entries if e.status == status]
    if knowledge_types:
        entries = [e for e in entries if e.type in knowledge_types]
    if confidence:
        entries = [e for e in entries if e.confidence == confidence]
    if tags:
        wanted = {t.lower() for t in tags}
        entries = [e for e in entries if wanted & {t.lower() for t in e.tags}]
    if task_ids:
        entries = [e for e in entries if any(t in e.task_ids for t in task_ids)]
    if files:
        entries = [e for e in entries if any(_file_hit(f, e.files) for f in files)]
    if after:
        entries = [e for e in entries if str(e.created_at or "") >= after]

    kws = [k.lower() for k in (keywords or []) if k and k.lower() not in STOPWORDS]
    counts = _counts_map([e.id for e in entries], sidecar_path)

    # BM25 ranking over a persistent SQLite FTS5 index (see
    # knowledge_search.py) rather than a per-call substring scan --
    # real term-frequency/field-weighted relevance instead of a flat
    # per-hit bonus, and no full re-read of every entry's body per
    # search. Falls back to "no keywords" (all entries pass) when kws
    # is empty, same as before.
    fts_scores = {}
    if kws:
        from . import knowledge_search  # local import: avoid a hard
        # dependency on sqlite3/FTS5 availability at module import time
        fts_scores = knowledge_search.bm25_scores(kws, directory)

    scored = []
    for e in entries:
        retrieval, useful = counts.get(e.id, (0, 0))
        useful_bonus = min(useful, USEFUL_CAP)
        if kws:
            if e.id not in fts_scores:
                continue  # keywords given but nothing matched: not relevant
            score = fts_scores[e.id] + useful_bonus
        else:
            score = useful_bonus
        scored.append((e, score))
    out = []
    for e, score in _ranked(scored, counts)[:max(0, limit)]:
        retrieval, useful = counts.get(e.id, (0, 0))
        out.append({
            "id": e.id, "title": e.title, "type": e.type, "status": e.status,
            "confidence": e.confidence, "path": e.path or "",
            "relevance_score": score, "tags": list(e.tags),
            "task_ids": list(e.task_ids), "files": list(e.files),
            "useful_count": useful, "retrieval_count": retrieval,
        })
    return out


def list_entries(knowledge_type: Optional[str] = None,
                 tag: Optional[str] = None,
                 task_id: Optional[str] = None,
                 status: Optional[str] = None,
                 limit: int = 1000,
                 directory: Optional[Path] = None,
                 sidecar_path: Optional[Path] = None) -> list:
    """List eligible entries with optional hard filters (no keywords)."""
    return search_entries(
        keywords=[], tags=[tag] if tag else None,
        knowledge_types=[knowledge_type] if knowledge_type else None,
        task_ids=[task_id] if task_id else None, status=status,
        limit=limit, directory=directory, sidecar_path=sidecar_path,
    )


# --------------------------------------------------------------------------
# RETRIEVE for a task (automatic: task/files boost, nothing hard-filters)
# --------------------------------------------------------------------------

def retrieve_for_task(task_id: str, title: str, text: str = "",
                      file_refs: Optional[list] = None,
                      tags: Optional[list] = None,
                      limit: int = 5,
                      record: bool = True,
                      directory: Optional[Path] = None,
                      sidecar_path: Optional[Path] = None) -> list:
    """Rank durable entries for ``task_id``. Task ID and file references are
    score boosts (never hard filters — a task with no linked entries must
    still find keyword-relevant knowledge). Score-0 entries are dropped.
    Returned entries get ``record_entry_retrieval()`` when ``record`` is set.
    """
    entries = [e for e in discover(directory) if e.status in RETRIEVABLE_STATUSES]
    query_tags = {t.lower() for t in (tags or [])}
    query_tags |= {b.lower() for b in [ _basename(f) for f in (file_refs or []) ]}
    query_tags |= set(tokenize(title))
    kws = tokenize(f"{title}\n{text}")
    # File-reference basenames/stems are strong query tags too.
    file_query_tags = set()
    for f in (file_refs or []):
        b = _basename(f)
        file_query_tags.add(b)
        stem = b.rsplit(".", 1)[0]
        file_query_tags.add(stem)
        for part in re.split(r"[_-]+", stem):
            if part and part not in STOPWORDS:
                file_query_tags.add(part)

    counts = _counts_map([e.id for e in entries], sidecar_path)
    scored: list = []
    for e in entries:
        score = 0
        reasons: list = []
        if task_id and task_id in e.task_ids:
            score += TASK_ID_BONUS
            reasons.append(f"task {task_id}")
        matched_files = []
        for f in (file_refs or []):
            hit = _file_hit(f, e.files)
            if hit and hit not in matched_files:
                matched_files.append(hit)
        if matched_files:
            score += FILE_BONUS
            reasons.append("file " + ", ".join(matched_files[:3]))
        entry_tags = {t.lower() for t in e.tags}
        overlap = sorted((entry_tags & query_tags) | (entry_tags & file_query_tags))
        if overlap:
            score += min(len(overlap) * TAG_HIT, TAG_CAP)
            reasons.append("tags: " + ", ".join(overlap[:4]))
        title_l = e.title.lower()
        tagkw_l = " ".join(e.tags + e.keywords).lower()
        body_l = (e.body or "").lower()
        hit_kws = []
        for kw in kws:
            gained = 0
            if kw in title_l:
                gained += TITLE_HIT
            if kw in tagkw_l:
                gained += TAGKW_HIT
            if kw in body_l:
                gained += BODY_HIT
            if gained:
                score += gained
                hit_kws.append(kw)
        if hit_kws:
            reasons.append("keywords: " + ", ".join(list(dict.fromkeys(hit_kws))[:5]))
        if score <= 0:
            continue  # no task/file/tag/keyword/related relevance: never pad
        # Secondary signals only — they can order relevant entries, never
        # rescue an irrelevant one (otherwise every medium+ entry scores).
        score += CONFIDENCE_BONUS.get(e.confidence, 0)
        retrieval, useful = counts.get(e.id, (0, 0))
        score += min(useful, USEFUL_CAP)
        scored.append(ScoredKnowledge(entry=e, score=score, reasons=reasons))

    # One expansion round: entries explicitly related to a selected entry.
    selected_ids = {s.entry.id for s in scored}
    by_id = {e.id: e for e in entries}
    extras: list = []
    for s in list(scored):
        for rel in s.entry.related:
            if rel in selected_ids or rel not in by_id:
                continue
            target = by_id[rel]
            counts.setdefault(target.id, (0, 0))
            extras.append(ScoredKnowledge(
                entry=target, score=RELATED_BONUS, reasons=[f"related to {s.entry.id}"]))
            selected_ids.add(rel)
    scored.extend(extras)

    # Same ranking as _ranked() but over ScoredKnowledge (stable multi-pass,
    # least significant key first).
    scored.sort(key=lambda s: s.entry.id)
    scored.sort(key=lambda s: s.entry.updated_at or "", reverse=True)
    scored.sort(key=lambda s: counts.get(s.entry.id, (0, 0))[0])
    scored.sort(key=lambda s: -counts.get(s.entry.id, (0, 0))[1])
    scored.sort(key=lambda s: -s.score)
    result = scored[:max(0, limit)]
    if record:
        for s in result:
            record_entry_retrieval(s.entry.id, sidecar_path)
    return result


# --------------------------------------------------------------------------
# INJECT (render compact, budgeted knowledge blocks for contracts)
# --------------------------------------------------------------------------

KNOWLEDGE_HEADER = "RELEVANT PROJECT KNOWLEDGE"
KNOWLEDGE_UNDERLINE = "----------------------------"


def _entry_summary(entry: DurableKnowledge, max_chars: int) -> str:
    summary = _section_text(entry.body, ("summary",))
    if not summary:
        # Fallback: first non-heading, non-empty, marker-free lines.
        fallback = []
        for line in entry.body.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if any(marker in s for marker in PROTOCOL_MARKERS):
                continue
            fallback.append(_clean_line(s))
            if len(" ".join(fallback)) >= max_chars:
                break
        summary = " ".join(fallback)
    summary = _clean_line(summary)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "..."
    return summary


def _entry_facts(entry: DurableKnowledge, max_facts: int, max_chars: int) -> list:
    sections = _section_text(entry.body, (
        "finding / knowledge", "finding", "knowledge",
        "what we discovered", "key decisions", "architecture facts",
        "learnings", "decision", "reason", "root cause",
    ))
    facts = _bullets(sections)
    if not facts:
        facts = _bullets(entry.body)
    picked = []
    for fact in facts:
        fact = _clean_line(fact)
        if len(fact) > max_chars:
            fact = fact[:max_chars].rsplit(" ", 1)[0] + "..."
        picked.append(fact)
        if len(picked) >= max_facts:
            break
    return picked


def _entry_evidence(entry: DurableKnowledge, max_chars: int) -> str:
    evidence = _clean_line(_section_text(entry.body, ("evidence",)))
    if not evidence:
        bits = []
        if entry.files:
            bits.append("Files: " + ", ".join(entry.files[:5]))
        if entry.task_ids:
            bits.append("Tasks: " + ", ".join(entry.task_ids[:5]))
        evidence = "; ".join(bits)
    if len(evidence) > max_chars:
        evidence = evidence[:max_chars].rsplit(" ", 1)[0] + "..."
    return evidence


def render_entry_lines(scored: ScoredKnowledge,
                       summary_chars: int = 400,
                       max_facts: int = 4,
                       fact_chars: int = 220,
                       evidence_chars: int = 200,
                       oneliner_chars: int = 400) -> list:
    """Render one entry as contract lines. Line 0 is the self-contained
    one-liner (shared verbatim by full and compact contracts so the compact
    form stays a strict subset of the full form); the rest is detail.
    The one-liner is length-capped so pathological titles cannot blow the
    compact contract's character budget."""
    e = scored.entry
    why = "; ".join(scored.reasons) if scored.reasons else "keyword overlap"
    oneliner = f"- [{e.id}] {e.title} ({e.type}, confidence {e.confidence}, status {e.status}) — why: {why}"
    if len(oneliner) > oneliner_chars:
        oneliner = oneliner[:oneliner_chars].rsplit(" ", 1)[0] + "..."
    lines = [oneliner]
    summary = _entry_summary(e, summary_chars)
    if summary:
        lines.append(f"  Summary: {summary}")
    for fact in _entry_facts(e, max_facts, fact_chars):
        lines.append(f"  Fact: {fact}")
    evidence = _entry_evidence(e, evidence_chars)
    if evidence:
        lines.append(f"  Evidence: {evidence}")
    return [_clean_line(ln) for ln in lines if _clean_line(ln)]


def build_knowledge_section(scored_list: list, max_chars: int = 3000,
                            max_entries: int = 5,
                            detail: bool = True) -> str:
    """Render the contract knowledge block within ``max_chars``.

    ``detail=True`` (full contract): one-liner + summary + facts + evidence.
    ``detail=False`` (compact contract): the one-liner only — verbatim the
    same line the full contract carries, so compact stays a strict subset.
    Returns ``""`` when there is nothing to inject (no empty headers).
    """
    if not scored_list:
        return ""
    lines = [KNOWLEDGE_HEADER, KNOWLEDGE_UNDERLINE]
    used = sum(len(ln) + 1 for ln in lines)
    shown = 0
    for scored in scored_list[:max_entries]:
        entry_lines = render_entry_lines(scored)
        if not detail:
            entry_lines = entry_lines[:1]
        need = sum(len(ln) + 1 for ln in entry_lines)
        if used + need > max_chars and shown > 0:
            continue  # budget: prefer dropping an entry over truncating one
        lines.extend(entry_lines)
        used += need
        shown += 1
    if shown == 0:
        return ""
    return "\n".join(lines)
