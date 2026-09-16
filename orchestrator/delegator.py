"""
Decides whether a task should go to primary Claude or a delegated
(cheaper/local/free) agent, and formats the delegation contract.

Tier assignment is NOT re-invented here — it's inherited directly from the
roadmap doc's own D-/C-/H- prefixes and its explicit "Delegation rejection
list" (see iteration improvement ideas.md). This module just operationalizes
that existing decision instead of re-deciding it.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.orchestrator.task_board import Task

# Repo root: <root>/src/orchestrator/delegator.py -> <root>
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Mirrors the roadmap's "Delegation rejection list" — but as ACTION verbs
# combined with sensitive targets, not bare topic words. A task can safely
# be *about* security/credentials/auth (e.g. D-013's read-only scan for
# hard-coded secrets) while still being delegate-safe; what's actually
# dangerous is a delegated agent being told to CHANGE, ROTATE, DELETE, or
# MIGRATE something sensitive. Bare nouns like "security" or "auth" used to
# trip this net on legitimate read-only recon tasks — this is the fix.
DANGEROUS_ACTIONS = ("rotate", "delete", "migrate", "modify", "change", "reset", "revoke", "deploy")
SENSITIVE_TARGETS = ("secret", "credential", "production", "permission", "schema", "architecture")


@dataclass
class DelegationDecision:
    should_delegate: bool
    reason: str


def _get_relevant_knowledge_for_task(task: Task, limit: int = 5) -> list:
    """Retrieve durable knowledge relevant to a task (pure: no side effects).

    Searches by task ID, file references (both ``src/...``-style paths in
    the prose and the structured ``Relevant files:`` block), tags, and
    keywords; ranked per ``durable_knowledge.retrieve_for_task`` (exact task
    +50, exact file +30, related +20, tag overlap, keyword overlap, then
    useful_count/recency tiebreaks). Recording the retrieval is the
    caller's job (CLI ``contract`` / ``dispatcher`` do it) so contract
    builders stay byte-for-byte deterministic.
    """
    from src.orchestrator import durable_knowledge as dk

    # Try to get the section text for more targeted search
    try:
        from src.orchestrator.task_parser import get_section_text
        section_text = get_section_text(task.id)
    except (ValueError, Exception):
        section_text = ""

    haystack = f"{task.title or ''}\n{section_text}"
    file_refs = [m.strip("`'\".,;:()[]") for m in
                 re.findall(r"\b(?:src|tests|docs|orchestration|knowledge|scripts|config|delegated_tasks|reports|learning|concerns)/\S+", haystack)]
    try:
        for path, _expected_new in parse_relevant_files(section_text):
            cleaned = path.strip().strip("`'\".,;:()[]")
            if cleaned:
                file_refs.append(cleaned)
    except (ValueError, Exception):
        pass
    seen = set()
    ordered_refs = []
    for ref in file_refs:
        if ref and ref not in seen:
            seen.add(ref)
            ordered_refs.append(ref)

    return dk.retrieve_for_task(
        task_id=task.id,
        title=task.title or "",
        text=section_text,
        file_refs=ordered_refs,
        limit=limit,
        record=False,
    )


def _get_relevant_board_knowledge(task: Task, limit: int = 5) -> list:
    """Retrieve Knowledge Board (K-*.md) entries relevant to a task, via
    ``knowledge_board.search_relevant`` (C-75) -- a separate, simpler
    tag/keyword-overlap ranking from the durable-knowledge retrieval
    above (which uses exact task/file matching + tag/keyword scoring
    with different weights over a different entry schema). Records the
    retrieval itself (unlike ``_get_relevant_knowledge_for_task``, which
    defers that to the caller) because ``search_relevant`` always records
    on every call by design -- see its docstring.
    """
    from src.orchestrator import knowledge_board as kb
    try:
        from src.orchestrator.task_parser import get_section_text
        section_text = get_section_text(task.id)
    except (ValueError, Exception):
        section_text = ""
    return kb.search_relevant({"title": task.title or "", "body": section_text}, limit=limit)


def _render_board_knowledge_section(entries: list) -> str:
    """Render Knowledge Board entries as a short 'RELEVANT PRIOR KNOWLEDGE'
    block: one line per entry (id, status, title only — no body, unlike
    the durable-knowledge block, since these are meant as pointers an
    agent can look up with `orchestrate knowledge board show <id>` if
    actually needed, not inline context). Returns "" if entries is empty
    so callers can splice this straight into an f-string.
    """
    if not entries:
        return ""
    lines = ["RELEVANT PRIOR KNOWLEDGE", "------------------------"]
    for e in entries:
        lines.append(f"- {e.id} ({e.state.status}) — {e.title}")
    return "\n".join(lines) + "\n\n"


def decide(task: Task) -> DelegationDecision:
    if task.tier == "human":
        return DelegationDecision(False, "H-tasks require a human decision, not any AI agent.")
    if task.tier == "primary_claude":
        return DelegationDecision(False, "C-tasks need repo-wide/architectural context (primary Claude).")

    # tier == "delegate" (D-xxx) — still run the action+target safety net.
    title_lower = task.title.lower()
    has_dangerous_action = any(a in title_lower for a in DANGEROUS_ACTIONS)
    has_sensitive_target = any(t in title_lower for t in SENSITIVE_TARGETS)
    if has_dangerous_action and has_sensitive_target:
        return DelegationDecision(
            False,
            "Tagged D- but title combines a risky action with a sensitive target — "
            "routing to primary Claude instead of auto-delegating.",
        )
    return DelegationDecision(True, "D-task, narrow/testable/reversible per roadmap classification.")


def build_contract(task: Task, allowed_paths: str = "as scoped below",
                    forbidden_paths: str = "anything outside the stated scope; do not touch secrets, "
                                                "migrations, or architecture files") -> str:
    """Fill in the roadmap's own 'Suggested agent-task prompt contract'
    template for this specific task, with the task's full roadmap section
    embedded inline (not just referenced) so a delegated agent — including
    a weaker/free model without reliable long-document navigation — can
    work from this single prompt without a separate lookup step.

    Also retrieves relevant durable knowledge (KNOW-*.md entries) and
    injects a budgeted block of actual content (summary, key facts,
    evidence — not just IDs) into the contract, so the agent gets project
    context without loading the entire knowledge database.
    """
    from src.orchestrator.task_parser import get_section_text
    try:
        section_text = get_section_text(task.id)
    except ValueError:
        section_text = "(Section text unavailable — see iteration improvement ideas.md directly.)"

    # Retrieve relevant durable knowledge (content, budgeted — not ID-only).
    from src.orchestrator import durable_knowledge as dk
    relevant_knowledge = _get_relevant_knowledge_for_task(task)
    knowledge_section = ""
    if relevant_knowledge:
        rendered = dk.build_knowledge_section(
            relevant_knowledge, max_chars=3000, max_entries=5, detail=True)
        if rendered:
            knowledge_section = rendered + "\n\n"

    # Retrieve relevant Knowledge Board entries (C-75) — a lighter,
    # one-line-per-entry pointer list (id/status/title only, no body),
    # separate from the durable-knowledge block above.
    board_section = _render_board_knowledge_section(_get_relevant_board_knowledge(task))

    return f"""You are a delegated coding agent working on the "AI Brain" repo
(github.com/Justin-Lemonade/AI-Brain-). You have been assigned exactly ONE
task below. Primary Claude (the project supervisor) has already claimed
this task on the shared task board on your behalf — you do not need to
claim it yourself, but you DO need to report the result back (see END OF
SESSION below) so the board reflects what actually happened.

{knowledge_section}{board_section}TASK
----
{section_text}

SCOPE
-----
Allowed files/directories: {allowed_paths}
Forbidden files/directories: {forbidden_paths}

TOOLS
-----
Allowed commands: read-only inspection + tests unless the task text above says otherwise
Network: only if the task text above requires it
Git writes: if you have push access, commit and push your work directly —
just don't mark the task done yourself (see END OF SESSION). If you don't
have push access, describe the change as a diff/patch in your report instead.

EXPECTED OUTPUT
---------------
Write your report to reports/{task.id}_report.md with:
1. Summary
2. Files changed (as a diff/commit — see SCOPE above for push rules)
3. Tests run
4. Failures
5. Questions/blockers

SUCCESS CRITERIA
----------------
Matches the acceptance criteria (if stated) in the task text above.

STOP CONDITIONS
----------------
Do not continue if the task requires touching anything on the "Delegation
rejection list" (architecture, security, secrets, migrations, permissions) —
stop and report back instead of improvising.

END OF SESSION
---------------
Once your report is written to reports/{task.id}_report.md, submit it for
review — do NOT mark it complete yourself:
  python -m src.main orchestrate submit {task.id} --notes "<one-line summary>" --report reports/{task.id}_report.md --agent "<the exact agent string from your claim>"
If you got stuck instead of finishing:
  python -m src.main orchestrate block {task.id} --notes "<what's blocking it>" --agent "<the exact agent string from your claim>"
  python -m src.main orchestrate release {task.id} --notes "<why you stopped>" --agent "<the exact agent string from your claim>"
Only Primary Claude or a human runs `approve`/`reject` — submitting moves
this to review, not done, and keeps it locked to you either way.
Worker-side moves require your ownership token (--agent must match the
claim); another agent's active task rejects you instead of letting you
move it (D-363).
"""


# The task-relevant safety boundary, kept as the compact contract's stop
# conditions. It is short, execution-critical, and points at the roadmap's
# own "Delegation rejection list" rather than restating the find_work.md
# protocol prose (claim/submit/report rules live there already).
STOP_CONDITIONS = (
    'Do not continue if the task requires touching anything on the "Delegation '
    'rejection list" (architecture, security, secrets, migrations, permissions) — '
    "stop and report back instead of improvising."
)

_TASK_REF_RE = re.compile(r"[HDC]-\d{3}")

_RELEVANT_FILES_HEADER_RE = re.compile(
    r"^\s*\*{0,2}\s*(?:relevant\s+files|relevant\s+file)\s*\*{0,2}\s*:\s*(.*)$",
    re.IGNORECASE,
)
_RELEVANT_FILES_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


def parse_relevant_files(section_text: str) -> list[tuple[str, bool]]:
    """Extract the optional `Relevant files:` block from a task section.

    Returns `(path, expected_new)` pairs in declaration order. Both a
    single-line form (`Relevant files: a.py, b.py (expected-new)`) and a
    bulleted form (`Relevant files:` followed by `- path` lines) are
    accepted; a trailing `(expected-new)` marks a path that does not exist
    yet. Returns [] when the section declares no relevant files.
    """
    entries: list[tuple[str, bool]] = []
    in_block = False
    for line in section_text.splitlines():
        header = _RELEVANT_FILES_HEADER_RE.match(line.strip())
        if header:
            in_block = True
            inline = header.group(1).strip()
            if inline:
                for raw in _split_paths(inline):
                    entries.append(_parse_path_entry(raw))
            continue
        if in_block:
            bullet = _RELEVANT_FILES_BULLET_RE.match(line.strip())
            if bullet:
                for raw in _split_paths(bullet.group(1)):
                    entries.append(_parse_path_entry(raw))
            elif line.strip() == "":
                continue
            else:
                in_block = False
    return entries


def _split_paths(text: str) -> list[str]:
    """Split a comma-or-whitespace separated path list, keeping
    `(expected-new)` suffixes attached to their path."""
    parts = [part for part in re.split(r"[\s,]+", text.strip()) if part]
    merged: list[str] = []
    for part in parts:
        if merged and part.lower() == "(expected-new)":
            merged[-1] = f"{merged[-1]} (expected-new)"
        elif part.lower().endswith("(expected-new)"):
            merged.append(part)
        else:
            merged.append(part)
    return merged


def _parse_path_entry(raw: str) -> tuple[str, bool]:
    text = raw.strip()
    expected_new = text.lower().endswith("(expected-new)")
    path = text[: -len("(expected-new)")].rstrip() if expected_new else text
    return path, expected_new


def validate_relevant_files(
    entries: list[tuple[str, bool]], root: "Path | None" = None
) -> list[dict]:
    """Validate relevant-file paths against the repo working tree.

    Each entry becomes `{"path", "expected_new", "status"}` where status is
    `exists`, `expected-new`, or `missing`. A path that exists on disk is
    `exists`; a path explicitly marked `(expected-new)` is `expected-new`
    even when absent; anything else is `missing`. Never raises for a bad
    path — the file map is advisory and the contract must still render.
    """
    base = root or _PROJECT_ROOT
    result: list[dict] = []
    for path, expected_new in entries:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = base / candidate
        if expected_new:
            status = "expected-new"
        elif candidate.exists():
            status = "exists"
        else:
            status = "missing"
        result.append({"path": path, "expected_new": expected_new, "status": status})
    return result


def _render_relevant_file(entry: dict) -> str:
    """Render one validated relevant-file entry for the compact contract.

    Existing paths render bare; `(expected-new)` paths keep their marker;
    missing paths get an explicit `(missing)` so a stale path is visible
    rather than silently pointing an agent at nothing.
    """
    if entry["status"] == "expected-new":
        return f"{entry['path']} (expected-new)"
    if entry["status"] == "missing":
        return f"{entry['path']} (missing)"
    return entry["path"]


def _parse_section(section_text: str, own_id: str) -> dict:
    """Extract execution-critical fields from a roadmap section.

    Returns the body prose, acceptance-criteria bullets, out-of-scope
    boundary, and dependency task IDs — the structured content the compact
    contract keeps. Generic protocol prose (preamble, TOOLS, EXPECTED
    OUTPUT, SUCCESS CRITERIA, END OF SESSION) is not here because it is not
    in the roadmap section; it is supplied by find_work.md instead.
    """
    body_lines: list[str] = []
    acceptance: list[str] = []
    out_of_scope = ""
    in_origin = in_acceptance = in_out_of_scope = in_relevant_files = False
    for line in section_text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if stripped.startswith("## "):
            continue
        if _RELEVANT_FILES_HEADER_RE.match(stripped):
            # The relevant-files block is extracted separately by
            # parse_relevant_files(); never treat its lines as body prose.
            in_relevant_files = True
            continue
        if in_relevant_files:
            if _RELEVANT_FILES_BULLET_RE.match(stripped) or stripped == "":
                continue
            in_relevant_files = False
        if lower.startswith("**priority:"):
            continue
        if lower.startswith("**origin:"):
            if not stripped.endswith("**"):
                in_origin = True
            continue
        if in_origin:
            if stripped.endswith("**"):
                in_origin = False
            continue
        if lower.startswith("acceptance:") or lower.startswith("acceptance criteria:"):
            in_acceptance = True
            continue
        if in_acceptance:
            if stripped.startswith("- ") or stripped.startswith("* "):
                acceptance.append(stripped.lstrip("-* ").strip())
                continue
            if stripped == "":
                continue
            in_acceptance = False
        if lower.startswith("**out of scope") or lower.startswith("out of scope:"):
            in_out_of_scope = True
            out_of_scope = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            continue
        if in_out_of_scope:
            if stripped == "" or stripped.startswith("**") or stripped == "---":
                in_out_of_scope = False
            else:
                out_of_scope = f"{out_of_scope} {stripped}".strip()
            continue
        if stripped in ("", "---"):
            continue
        body_lines.append(stripped)
    dependencies = sorted({ref for ref in _TASK_REF_RE.findall(section_text) if ref != own_id})
    return {
        "body": "\n".join(body_lines),
        "acceptance": acceptance,
        "out_of_scope": out_of_scope.replace("**", "").strip(),
        "dependencies": dependencies,
        "relevant_files": parse_relevant_files(section_text),
    }


def compact_contract_fields(task: Task, allowed_paths: str = "as scoped below",
                            forbidden_paths: str = "anything outside the stated scope; do not touch secrets, "
                                                   "migrations, or architecture files") -> dict:
    """Structured execution-critical fields for a compact delegation contract.

    Keeps task identity/tier/priority, scope, acceptance criteria,
    dependencies, out-of-scope boundary, and stop conditions; omits the
    generic protocol prose (claim/submit/report/TOOLS) that find_work.md
    already supplies to every agent on this repo. Shared by the
    human-readable compact form and the JSON form so the two never drift.
    Both carry the same budgeted durable-knowledge references as one-liners
    (verbatim the same lines the full contract's knowledge block carries,
    so the compact form stays a strict subset of the full form).
    """
    from src.orchestrator.task_parser import get_section_text
    try:
        section_text = get_section_text(task.id)
    except ValueError:
        section_text = ""
    parsed = _parse_section(section_text, task.id)
    from src.orchestrator import durable_knowledge as dk
    knowledge_cards = []
    for scored in _get_relevant_knowledge_for_task(task, limit=3):
        entry = scored.entry
        oneline = dk.render_entry_lines(scored)
        knowledge_cards.append({
            "id": entry.id,
            "title": entry.title,
            "type": entry.type,
            "status": entry.status,
            "confidence": entry.confidence,
            "why_relevant": list(scored.reasons),
            "reference": oneline[0] if oneline else f"- [{entry.id}] {entry.title}",
        })
    board_cards = [
        {"id": e.id, "title": e.title, "status": e.state.status}
        for e in _get_relevant_board_knowledge(task, limit=3)
    ]
    return {
        "task_id": task.id,
        "tier": task.tier,
        "priority": task.priority,
        "title": task.title,
        "scope": {"allowed": allowed_paths, "forbidden": forbidden_paths},
        "task": parsed["body"] or "(Section text unavailable — see iteration improvement ideas.md directly.)",
        "acceptance_criteria": parsed["acceptance"],
        "dependencies": parsed["dependencies"],
        "out_of_scope": parsed["out_of_scope"],
        "relevant_files": validate_relevant_files(parsed["relevant_files"]),
        "relevant_knowledge": knowledge_cards,
        "relevant_board_knowledge": board_cards,
        "stop_conditions": STOP_CONDITIONS,
    }


def build_compact_contract(task: Task, allowed_paths: str = "as scoped below",
                           forbidden_paths: str = "anything outside the stated scope; do not touch secrets, "
                                                  "migrations, or architecture files") -> str:
    """Human-readable compact delegation contract: the execution-critical
    fields only, without the generic protocol prose from find_work.md that
    the full contract duplicates. Carries the same durable-knowledge
    one-liners the full contract carries (no detail lines), so every line
    here also appears in the full form."""
    from src.orchestrator import durable_knowledge as dk
    f = compact_contract_fields(task, allowed_paths, forbidden_paths)
    lines = [
        f"# {f['task_id']} — {f['title']}",
        f"Tier: {f['tier']} | Priority: {f['priority']}",
        "",
        "SCOPE",
        f"Allowed files/directories: {f['scope']['allowed']}",
        f"Forbidden files/directories: {f['scope']['forbidden']}",
        "",
        "TASK",
        "----",
        f["task"],
    ]
    if f["relevant_knowledge"]:
        knowledge_block = dk.build_knowledge_section(
            _get_relevant_knowledge_for_task(task, limit=3),
            max_chars=1500, max_entries=3, detail=False)
        if knowledge_block:
            lines += ["", knowledge_block]
    if f["relevant_board_knowledge"]:
        lines += ["", "RELEVANT PRIOR KNOWLEDGE"]
        lines += [f"- {c['id']} ({c['status']}) — {c['title']}" for c in f["relevant_board_knowledge"]]
    if f["acceptance_criteria"]:
        lines += ["", "ACCEPTANCE CRITERIA"]
        lines += [f"- {a}" for a in f["acceptance_criteria"]]
    if f["relevant_files"]:
        lines += ["", "RELEVANT FILES"]
        lines += [f"- {_render_relevant_file(r)}" for r in f["relevant_files"]]
    if f["dependencies"]:
        lines += ["", "DEPENDENCIES"]
        lines += [f"- {d}" for d in f["dependencies"]]
    if f["out_of_scope"]:
        lines += ["", "OUT OF SCOPE", f["out_of_scope"]]
    lines += ["", "STOP CONDITIONS", f["stop_conditions"]]
    return "\n".join(lines)


def build_compact_contract_json(task: Task, allowed_paths: str = "as scoped below",
                                forbidden_paths: str = "anything outside the stated scope; do not touch secrets, "
                                                       "migrations, or architecture files") -> str:
    """Machine-readable compact delegation contract (same fields as the
    human-readable form, serialized as JSON)."""
    return json.dumps(compact_contract_fields(task, allowed_paths, forbidden_paths), indent=2, sort_keys=True)
