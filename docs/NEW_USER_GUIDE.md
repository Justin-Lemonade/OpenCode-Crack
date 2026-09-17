# opencode_crack — New User Guide

`opencode_crack` is a local-first, git-backed multi-agent task
orchestration framework: a task board, a knowledge board, a concerns
board, an agent runtime, and a delegator/dispatcher that routes work to
any CLI-invocable model backend. It doesn't do any ingestion, memory
storage, or chat-bot work itself — it's the coordination layer a project
puts on top of its own codebase to run multiple AI agents against a
shared, reviewable task board.

If you're looking for a specific product's own user guide — how to feed
it documents, how its chatbot works, what its own domain commands do —
that lives in the consuming project's own docs, not here. This guide
covers what `opencode_crack` itself provides.

## 1. Install

```bash
pip install -e .          # from a checkout of this repo
# or, once published:
pip install opencode-crack
```

Requirements: Python 3.10+, `git`. The only runtime dependencies are
`pyyaml` and `python-dotenv` — nothing else is required for the task
board itself.

Verify the CLI is on your path:

```bash
orchestrate --help
```

## 2. The task board, in one pass

Every unit of work is a task on a YAML-backed board
(`orchestration/tasks.yaml`, generated — never hand-edit it). Task
content itself lives in Markdown files under `delegated_tasks/`.

```bash
orchestrate status                    # dashboard: active, stale, blocked, left
orchestrate list --status open        # what's unclaimed
orchestrate claim D-188 --agent "my-agent"
orchestrate start D-188               # mark it in_progress (owner only)
orchestrate submit D-188 --notes "..." --report reports/D-188_report.md
orchestrate approve D-188 --notes "verified: ..."   # reviewer/human only
```

The lifecycle is: `claim → start → implement → test → report → submit →
approve`. Only `approve` marks a task genuinely done — `submit` hands it
to review, it doesn't complete it. `reject` sends it back to `open` with
feedback instead.

## 3. Reading the docs in order

This package's own protocol docs are written to be read by an agent, not
just a human — if you're setting up a project to actually run agents
against this board, point them at these in this order:

1. [`AGENTS.md`](AGENTS.md) — the core rules: startup, task locking,
   off-chat reporting, completion-via-submit, and where tasks come from.
2. [`../.agent_prompts/find_work.md`](../.agent_prompts/find_work.md) —
   the self-service loop a delegated agent runs: claim → work → report →
   submit → loop.
3. [`LOCAL_SEARCH.md`](LOCAL_SEARCH.md) — the two local search tools
   (durable knowledge, repo-wide Markdown) agents use to check what
   already exists before duplicating work.
4. [`../.agent_prompts/raise_concern.md`](../.agent_prompts/raise_concern.md) —
   how an agent files a problem with the project itself, not just its
   own task.

If your project runs a primary/reviewing agent as well as delegated
ones, also see
[`../.agent_prompts/review_work.md`](../.agent_prompts/review_work.md)
(clearing the review queue) and
[`../.agent_prompts/task_breakdown.md`](../.agent_prompts/task_breakdown.md)
(slicing larger work into bounded delegate-tier tasks).

## 4. Knowledge and search

Two coexisting systems, deliberately separate — see
[`LOCAL_SEARCH.md`](LOCAL_SEARCH.md) for full detail:

- `orchestrate knowledge search` — structured durable-knowledge entries
  (`KNOW-*.md`): architecture facts, decisions, blockers, learnings.
- `orchestrate search` — repo-wide keyword search over everything else
  written in Markdown (reports, task specs, docs, concerns).

## 5. Concerns board

Separate from the task board — for problems with the *project*, not a
specific task:

```bash
orchestrate concern file --title "..." --body "..." --severity medium --category process --raised-by "my-agent"
orchestrate concern list --status open
orchestrate concern board       # dashboard view
```

See [`../.agent_prompts/raise_concern.md`](../.agent_prompts/raise_concern.md)
for the full lifecycle (acknowledge/resolve/dismiss/reopen).

## 6. Running agents through OpenCode Swarm (optional)

If you want this package to actually launch and coordinate live AI
agents (rather than just tracking work humans do by hand), see
[`SWARM_USER_GUIDE.md`](SWARM_USER_GUIDE.md) — a full walkthrough from
installing the runtime dependencies through running your first bounded
manager/worker/tester task and watching it on the built-in dashboard.

## 7. Testing

```bash
pytest tests/ -q
```

The suite is offline by design — no network, no live model calls, no
external services required to run it.

## 8. Where to look next

| Document | Scope |
|---|---|
| [`AGENTS.md`](AGENTS.md) | core development protocol for agents |
| [`LOCAL_SEARCH.md`](LOCAL_SEARCH.md) | the two local search tools |
| [`SWARM_USER_GUIDE.md`](SWARM_USER_GUIDE.md) | running live agents via OpenCode Swarm |
| [`KNOWLEDGE_AUDIT_PROMPT.md`](KNOWLEDGE_AUDIT_PROMPT.md) | periodic knowledge-base hygiene pass |
| `../.agent_prompts/` | the actual agent-facing protocol files |
