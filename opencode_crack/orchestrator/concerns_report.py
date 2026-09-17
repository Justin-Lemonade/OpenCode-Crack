"""Generates `CONCERNS_BOARD.md` — a human-readable, always-current
dashboard of concerns agents have filed. Regenerated on every
concerns_board state change (file/acknowledge/resolve/dismiss/reopen/
sync) — see concerns_board.py. Never hand-edit; it will be overwritten
on the next `orchestrate concern` command, exactly like STATUS.md (see
status_report.py, which this mirrors).
"""
from datetime import datetime, timezone
from pathlib import Path

BOARD_MD_PATH = Path("CONCERNS_BOARD.md")

SEVERITY_ORDER = {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
SEVERITY_ICON = {"BLOCKER": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}


def render_markdown(concerns: list) -> str:
    from opencode_crack.orchestrator.concerns_board import is_stale  # local import avoids a circular import

    total = len(concerns)
    by_status: dict = {}
    for c in concerns:
        by_status.setdefault(c.state.status, []).append(c)

    open_ = by_status.get("open", [])
    acknowledged = by_status.get("acknowledged", [])
    resolved = by_status.get("resolved", [])
    wontfix = by_status.get("wontfix", [])

    needs_attention = open_ + acknowledged
    needs_attention.sort(key=lambda c: (SEVERITY_ORDER.get(c.severity, 9), c.state.created_at or ""))

    lines = []
    lines.append("# Concerns Board")
    lines.append("")
    lines.append(
        f"_Auto-generated {_now_str()} — do not hand-edit. This is the "
        "escalation channel for agents to flag process, board-integrity, "
        "technical, or environment problems to Primary Claude / project "
        "managers — separate from task reports in `reports/`. See "
        "[concerns/README.md](concerns/README.md) for how to file one. "
        "Full history: [concerns/activity.log](concerns/activity.log)._"
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- **{total}** concerns total — **{len(open_)}** open, "
        f"**{len(acknowledged)}** acknowledged, **{len(resolved)}** resolved, "
        f"**{len(wontfix)}** won't fix"
    )
    if needs_attention:
        by_sev: dict = {}
        for c in needs_attention:
            by_sev[c.severity] = by_sev.get(c.severity, 0) + 1
        sev_line = ", ".join(
            f"{n} {s}" for s, n in sorted(by_sev.items(), key=lambda kv: SEVERITY_ORDER.get(kv[0], 9))
        )
        lines.append(f"  - Needs attention ({len(needs_attention)}): {sev_line}")
    lines.append("")

    lines.append("## Needs attention")
    lines.append("")
    if not needs_attention:
        lines.append("_Nothing open or acknowledged. Board is clear._")
    else:
        lines.append("| | Concern | Severity | Category | Status | Raised by | Related | Since | |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in needs_attention:
            icon = SEVERITY_ICON.get(c.severity, "")
            stale_flag = "**STALE**" if is_stale(c) else ""
            related = c.related_task or "—"
            lines.append(
                f"| {icon} | **{c.id}** — {c.title} | {c.severity} | {c.category} | "
                f"{c.state.status} | {c.raised_by} | {related} | "
                f"{c.state.created_at or '—'} | {stale_flag} |"
            )
    lines.append("")

    if resolved or wontfix:
        lines.append("## Recently closed")
        lines.append("")
        closed = sorted(resolved + wontfix, key=lambda c: c.state.updated_at or "", reverse=True)[:10]
        for c in closed:
            note = f" — {c.state.resolution_notes}" if c.state.resolution_notes else ""
            since = f" (filed {c.state.created_at or '—'}, closed {c.state.resolved_at or '—'})"
            lines.append(f"- **{c.id}** ({c.state.status}){since} — {c.title}{note}")
        lines.append("")

    recent = _recent_activity()
    if recent:
        lines.append(recent)

    return "\n".join(lines)


def _recent_activity() -> str:
    """Last 10 lines of concerns/activity.log, or "" if it doesn't exist
    yet. Local import to avoid a circular import with concerns_board."""
    from opencode_crack.orchestrator.concerns_board import ACTIVITY_LOG

    if not ACTIVITY_LOG.exists():
        return ""
    lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    recent = lines[-10:]
    joined = chr(10).join(recent)
    return f"## Recent Activity\n\n```\n{joined}\n```"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_board(concerns: list) -> None:
    BOARD_MD_PATH.write_text(render_markdown(concerns), encoding="utf-8")
