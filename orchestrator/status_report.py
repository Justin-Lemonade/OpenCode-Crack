"""
Generates `STATUS.md` — a human-readable, always-current snapshot of the
task board. This is what answers, at a glance, without running any command:

    - What's currently being worked on, and by whom?
    - Is any of that actually stalled (claimed but gone quiet)?
    - What's blocked, and why?
    - How much is left vs. done?

It is regenerated (and committed alongside tasks.yaml) on every task_board
state change — see task_board.sync() and _git_publish(). Never hand-edit
STATUS.md; it will be overwritten on the next orchestrator command.
"""
from datetime import datetime, timezone
from pathlib import Path

STATUS_PATH = Path("STATUS.md")

TIER_LABELS = {
    "human": "Human decisions (H-*)",
    "delegate": "Delegatable work (D-*)",
    "primary_claude": "Primary Claude work (C-*)",
}


def render_markdown(tasks: list) -> str:
    from src.orchestrator.task_board import is_stale  # local import avoids a circular import

    total = len(tasks)
    by_status = {}
    for t in tasks:
        by_status.setdefault(t.state.status, []).append(t)

    active = by_status.get("claimed", []) + by_status.get("in_progress", [])
    active.sort(key=lambda t: t.state.updated_at or "")
    review = by_status.get("review", [])
    review.sort(key=lambda t: t.state.updated_at or "")
    blocked = by_status.get("blocked", [])
    done = by_status.get("done", [])
    open_ = by_status.get("open", [])

    lines = []
    lines.append("# AI Brain — Orchestration Status")
    lines.append("")
    lines.append(f"_Auto-generated {_now_str()} — do not hand-edit. "
                  f"See [AGENTS.md](docs/AGENTS.md) for the workflow this reflects. "
                  f"Full change history: [reports/activity.log](reports/activity.log)._")
    lines.append("")

    # --- Summary counts -------------------------------------------------
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **{total}** tasks total — **{len(done)}** done, "
                  f"**{len(review)}** awaiting review, **{len(active)}** active, "
                  f"**{len(blocked)}** blocked, **{len(open_)}** open")
    for tier, label in TIER_LABELS.items():
        tier_tasks = [t for t in tasks if t.tier == tier]
        tier_done = len([t for t in tier_tasks if t.state.status == "done"])
        lines.append(f"  - {label}: {tier_done}/{len(tier_tasks)} done")
    lines.append("")

    # --- Awaiting review --------------------------------------------------
    # This is the trust boundary: an agent finishing work moves a task
    # here, NOT to done. Nothing counts as done until someone (Primary
    # Claude or a human) reviews the report and runs `orchestrate approve`.
    lines.append("## Awaiting review")
    lines.append("")
    if not review:
        lines.append("_Nothing waiting on review._")
    else:
        lines.append("| Task | Submitted by | Since | Report | Notes |")
        lines.append("|---|---|---|---|---|")
        for t in review:
            report_link = f"[{t.state.report_path}]({t.state.report_path})" if t.state.report_path else "—"
            lines.append(
                f"| {t.id} — {t.title} | {t.state.assignee or '—'} | "
                f"{t.state.updated_at or '—'} | {report_link} | {t.state.notes or '—'} |"
            )
    lines.append("")

    # --- Active work -----------------------------------------------------
    lines.append("## Active right now")
    lines.append("")
    if not active:
        lines.append("_Nothing currently claimed or in progress._")
    else:
        lines.append("| Task | Tier | Status | Assignee | Since | |")
        lines.append("|---|---|---|---|---|---|")
        for t in active:
            stale_flag = "**STALE**" if is_stale(t) else ""
            lines.append(
                f"| {t.id} — {t.title} | {t.tier} | {t.state.status} | "
                f"{t.state.assignee or '—'} | {t.state.claimed_at or '—'} | {stale_flag} |"
            )
    lines.append("")

    # --- Blocked -----------------------------------------------------------
    if blocked:
        lines.append("## Blocked")
        lines.append("")
        for t in blocked:
            lines.append(f"- **{t.id}** — {t.title}: {t.state.notes or 'no reason recorded'}")
        lines.append("")

    # --- Recently completed -------------------------------------------------
    if done:
        lines.append("## Recently completed")
        lines.append("")
        recent_done = sorted(done, key=lambda t: t.state.updated_at or "", reverse=True)[:10]
        for t in recent_done:
            note = f" — {t.state.notes}" if t.state.notes else ""
            lines.append(f"- **{t.id}** — {t.title}{note}")
        lines.append("")

    # --- Open backlog, grouped, so it reads as "what isn't being worked on" -
    lines.append("## Not yet started")
    lines.append("")
    for tier, label in TIER_LABELS.items():
        tier_open = [t for t in open_ if t.tier == tier]
        if not tier_open:
            continue
        lines.append(f"**{label}** ({len(tier_open)} open):")
        # Keep this scannable — highest priority first, capped so the file
        # doesn't balloon into a second copy of the roadmap doc.
        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM-HIGH": 2, "MEDIUM": 3, "LOW-MEDIUM": 4, "LOW": 5}
        tier_open.sort(key=lambda t: priority_order.get(t.priority, 9))
        for t in tier_open[:15]:
            lines.append(f"- {t.id} ({t.priority}) — {t.title}")
        if len(tier_open) > 15:
            lines.append(f"- _...and {len(tier_open) - 15} more (see `orchestrate list --tier {tier} --status open`)_")
        lines.append("")

    recent = _recent_activity()
    if recent:
        lines.append(recent)

    return "\n".join(lines)


def _recent_activity() -> str:
    """Last 10 lines of reports/activity.log as a ``## Recent Activity``
    section (all lines if fewer than 10), or "" if the log doesn't exist
    yet. Local import to avoid a circular import with task_board."""
    from src.orchestrator.task_board import ACTIVITY_LOG

    if not ACTIVITY_LOG.exists():
        return ""
    lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    recent = lines[-10:]
    return (
        "## Recent Activity\n"
        "\n"
        "```\n"
        f"{'\n'.join(recent)}\n"
        "```"
    )


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_status(tasks: list) -> None:
    STATUS_PATH.write_text(render_markdown(tasks), encoding="utf-8")
