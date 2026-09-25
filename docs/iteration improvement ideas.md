# Iteration Improvement Ideas — local roadmap starter

This is the primary roadmap parsed by `opencode_crack/orchestrator/task_parser.py`
(`ROADMAP_PATH = docs/iteration improvement ideas.md`).

Fresh-clone starter: no tasks yet. Add tasks as `## D-NNN — Title` / `## H-NNN` / `## C-NNN`
sections. See `docs/AGENTS.md` for format and `delegated_tasks/` for bounded D-tier slices.

## D-001 — Verify orchestration setup

**Priority: HIGH.**
**Origin:** Fresh clone setup verification.
**Out of scope:** Live model dispatch; local CLI verification only.

Verify `orchestrate list`, `orchestrate briefing`, and
`python -m pytest tests/ -q --import-mode=importlib` pass from this checkout.
