"""Generates `KNOWLEDGE_BOARD.md` — a human-readable, always-current
dashboard of filed knowledge entries. Regenerated on every knowledge_board
state change (file/verify/confirm/supersede/retract/sync) — see
knowledge_board.py. Never hand-edit; it will be overwritten on the next
`orchestrate knowledge` command, exactly like CONCERNS_BOARD.md (see
concerns_report.py, which this mirrors in structure and visual style so
that someone familiar with one board immediately understands the other).
"""
from datetime import datetime, timezone
from pathlib import Path

BOARD_MD_PATH = Path("KNOWLEDGE_BOARD.md")

STATUS_ORDER = ["unverified", "agent-verified", "reviewer-verified",
                "confirmed", "superseded", "retracted"]
STATUS_ICON = {"unverified": "⚪", "agent-verified": "🟡",
               "reviewer-verified": "🟢", "confirmed": "✅",
               "superseded": "↪️", "retracted": "❌"}


def render_markdown(entries: list) -> str:
    from opencode_crack.orchestrator.knowledge_board import is_stale  # local import avoids a circular import

    total = len(entries)
    by_status: dict = {}
    for e in entries:
        by_status.setdefault(e.state.status, []).append(e)

    by_category: dict = {}
    for e in entries:
        by_category[e.category] = by_category.get(e.category, 0) + 1

    needs_review = [e for e in entries if e.state.status in ("unverified", "agent-verified")]
    needs_review.sort(key=lambda e: (e.state.created_at or ""))

    lines = []
    lines.append("# Knowledge Board")
    lines.append("")
    lines.append(
        f"_Auto-generated {_now_str()} — do not hand-edit. This is the "
        "persistent peer-learning and institutional-memory board: durable "
        "lessons agents have filed for other agents. See "
        "[knowledge/README.md](knowledge/README.md) for how to file one. "
        "Full history: [knowledge/activity.log](knowledge/activity.log)._"
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    status_bits = ", ".join(
        f"**{len(by_status.get(s, []))}** {s}" for s in STATUS_ORDER
    )
    lines.append(f"- **{total}** entries total — {status_bits}")
    if by_category:
        cat_line = ", ".join(
            f"{n} {c}" for c, n in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        lines.append(f"  - By category: {cat_line}")
    lines.append("")

    lines.append("## Needs review")
    lines.append("")
    if not needs_review:
        lines.append("_Nothing unverified or agent-verified. Board is clear._")
    else:
        lines.append("| | Entry | Category | Tags | Submitted by | Related | Since | |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for e in needs_review:
            icon = STATUS_ICON.get(e.state.status, "")
            stale_flag = "**STALE**" if is_stale(e) else ""
            tags = ", ".join(e.tags) if e.tags else "—"
            related = e.related_task or "—"
            lines.append(
                f"| {icon} | **{e.id}** — {e.title} | {e.category} | "
                f"{tags} | {e.submitted_by} | {related} | "
                f"{e.state.created_at or '—'} | {stale_flag} |"
            )
    lines.append("")

    useful_pool = [e for e in entries if e.state.status in ("reviewer-verified", "confirmed")]
    useful_pool.sort(key=lambda e: (-e.state.useful_count, -e.state.retrieval_count, e.id))
    lines.append("## Most useful")
    lines.append("")
    top = useful_pool[:10]
    if not top:
        lines.append("_No reviewer-verified or confirmed entries yet._")
    else:
        lines.append("| Entry | Category | Useful | Retrieved |")
        lines.append("|---|---|---|---|")
        for e in top:
            lines.append(
                f"| **{e.id}** — {e.title} | {e.category} | "
                f"{e.state.useful_count} | {e.state.retrieval_count} |"
            )
    lines.append("")

    lines.append("## Recently added")
    lines.append("")
    if not entries:
        lines.append("_No entries filed yet._")
    else:
        recent = sorted(entries, key=lambda e: e.state.created_at or "", reverse=True)[:10]
        for e in recent:
            lines.append(f"- **{e.id}** ({e.state.status}, {e.state.created_at or '—'}) — {e.title}")
    lines.append("")

    recent_activity = _recent_activity()
    if recent_activity:
        lines.append(recent_activity)

    return "\n".join(lines)


def _recent_activity() -> str:
    """Last 10 lines of knowledge/activity.log, or "" if it doesn't exist
    yet. Local import to avoid a circular import with knowledge_board."""
    from opencode_crack.orchestrator.knowledge_board import ACTIVITY_LOG

    if not ACTIVITY_LOG.exists():
        return ""
    lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    recent = lines[-10:]
    joined = chr(10).join(recent)
    return f"## Recent Activity\n\n```\n{joined}\n```"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_board(entries: list) -> None:
    BOARD_MD_PATH.write_text(render_markdown(entries), encoding="utf-8")
