"""
Compact, deterministic startup briefing for delegated agents (roadmap D-149).

A delegated agent should be able to read this single small document and
start picking a task without re-reading AGENTS.md + find_work.md + STATUS.md
in full. It contains only:

    - the current queue summary (counts per status),
    - the top eligible D-tier tasks (open, highest priority first),
    - the essential claim/submit/review workflow rules (short),
    - pointers to the full protocol documents (not reproduced).

The output is deterministic for a given board state: no timestamps, no
random ordering, and keys are sorted. `build_briefing()` returns the
human-readable form; `build_briefing_json()` returns the same fields as
JSON so machine consumers can parse it. Both share one
`briefing_fields()` source of truth so they never drift.
"""
import json

from src.orchestrator.task_board import Task

# Mirrors status_report.py's ordering vocabulary (the roadmap's own
# priority ladder). Anything outside it sorts below all real priorities.
PRIORITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM-HIGH": 2,
    "MEDIUM": 3,
    "LOW-MEDIUM": 4,
    "LOW": 5,
}

# How many top tasks to include. The point is a *compact* briefing — the
# full backlog lives in STATUS.md / `orchestrate list`, not here.
TOP_TASK_LIMIT = 10

# The essential workflow rules, kept short and stable. These are the
# invariants a delegated agent must not break; the full protocol documents
# (AGENTS.md, find_work.md) are pointed to rather than reproduced.
ESSENTIAL_RULES = [
    "Claim before work: `python -m src.main orchestrate claim <TASK_ID> --agent \"<your name>\"`.",
    "Never work a task you have not claimed; never work a task claimed by someone else.",
    "Worker-side moves need your ownership token: `start`/`submit`/`block`/`release` take `--agent \"<your claim string>\"` (D-363).",
    "Work one task at a time; submit or block it before claiming the next.",
    "Write your report to `reports/<TASK_ID>_report.md` (off-chat evidence).",
    "Submit, never complete: `python -m src.main orchestrate submit <TASK_ID> --notes \"...\" --report reports/<TASK_ID>_report.md --agent \"<your claim string>\"`.",
    "Only Primary Claude or a human runs `approve`/`reject`; you cannot mark your own work done.",
    "Never touch the Delegation rejection list (architecture, security, secrets, permissions).",
]

# Pointers to the full documents — the briefing references them instead of
# embedding them, which is what keeps it substantially smaller than the sum.
PROTOCOL_POINTERS = {
    "docs/AGENTS.md": "agent coordination guide (the four rules)",
    ".agent_prompts/find_work.md": "full find-work protocol (task selection loop)",
    "STATUS.md": "full board snapshot (auto-generated)",
    "reports/README.md": "report format (5-point)",
}


def briefing_fields(tasks: list[Task]) -> dict:
    """Structured briefing content: queue summary, top eligible tasks,
    essential rules, protocol pointers. Shared by the human-readable and
    JSON forms so the two never drift."""
    by_status = {}
    for t in tasks:
        by_status.setdefault(t.state.status, []).append(t)

    open_tasks = sorted(
        by_status.get("open", []),
        key=lambda t: (PRIORITY_ORDER.get(t.priority, 9), t.id),
    )
    top_delegate = [t for t in open_tasks if t.tier == "delegate"][:TOP_TASK_LIMIT]

    return {
        "queue_summary": {
            "total": len(tasks),
            "open": len(by_status.get("open", [])),
            "active": len(by_status.get("claimed", [])) + len(by_status.get("in_progress", [])),
            "review": len(by_status.get("review", [])),
            "blocked": len(by_status.get("blocked", [])),
            "done": len(by_status.get("done", [])),
        },
        "top_eligible_tasks": [
            {"task_id": t.id, "priority": t.priority, "title": t.title}
            for t in top_delegate
        ],
        "essential_rules": ESSENTIAL_RULES,
        "protocol_pointers": PROTOCOL_POINTERS,
    }


def build_briefing(tasks: list[Task]) -> str:
    """Human-readable compact startup briefing."""
    f = briefing_fields(tasks)
    q = f["queue_summary"]
    lines = [
        "# AI Brain — Delegated Agent Briefing",
        "",
        f"Queue: {q['total']} tasks — {q['done']} done, {q['review']} review, "
        f"{q['active']} active, {q['blocked']} blocked, {q['open']} open",
        "",
        "## Top eligible D-tier tasks",
    ]
    if f["top_eligible_tasks"]:
        lines += [
            f"- {t['task_id']} ({t['priority']}) — {t['title']}"
            for t in f["top_eligible_tasks"]
        ]
    else:
        lines.append("- (none open)")
    lines += ["", "## Essential workflow rules"]
    lines += [f"- {rule}" for rule in f["essential_rules"]]
    lines += ["", "## Full protocol documents (see these for detail)"]
    lines += [f"- {name}: {desc}" for name, desc in f["protocol_pointers"].items()]
    return "\n".join(lines)


def build_briefing_json(tasks: list[Task]) -> str:
    """Machine-readable briefing form (same fields as the human form)."""
    return json.dumps(briefing_fields(tasks), indent=2, sort_keys=True)
