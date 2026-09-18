# Agent Coordination Guide

Read this first if you are an AI agent (Claude, ChatGPT, Gemini, DeepSeek,
a local Ollama model, or any future agent) working on a project built on
`opencode_crack`.

This file describes the protocol this package provides. A consuming
project may extend or restate parts of this for its own repo — if it
does, that project's own copy of this file takes precedence, since it
knows about local specifics (its own roadmap doc's name and location,
its own rejection list, its own project owner) that this generic version
can't.

## Startup

If you were told to "run the find work protocol" or similar, open
[`.agent_prompts/find_work.md`](../.agent_prompts/find_work.md) and follow it —
it's a self-contained loop: find an open task, claim it, do the work,
report, submit, repeat. That file is the one place this workflow is written
out in full; this document covers the rules behind it.

If you're the primary agent (or a human) clearing a backlog of tasks in
`review`, use [`.agent_prompts/review_work.md`](../.agent_prompts/review_work.md)
instead — it's the other half of the loop: verify submitted work,
approve/reject, and mine the same pass for problems to fix in
`find_work.md` so the whole system keeps improving rather than
repeating the same mistakes.

If the delegate-tier queue is running dry while the C-tier backlog is
still large (check `orchestrate status`), use
[`.agent_prompts/task_breakdown.md`](../.agent_prompts/task_breakdown.md) —
it's how bounded, safe slices get extracted out of architecture-level
C-tasks and turned into new D-tasks, so there's always real work for
agents to pull instead of the queue just sitting empty.

If a batch of new D-tasks is about to be dispatched (typically right
after a `task_breakdown.md` session produces a large set of new open
tasks), run
[`.agent_prompts/critique_batch.md`](../.agent_prompts/critique_batch.md)
first — it's a pre-implementation peer review that reads task *specs*
before any code is written, catches ambiguities, dependency ordering
risks, merge conflict hotspots, and spec gaps that would otherwise
produce reject cycles. The output is a single `reports/critique_YYYY-MM-DD.md`
document that the primary agent reviews before dispatching. One or two
Critic Agents can run this simultaneously (see Section 6 in the file
for the parallel-write protocol).

If you were told "you are the delegation agent" (or similar), read
[`.agent_prompts/delegation_agent_identity.md`](../.agent_prompts/delegation_agent_identity.md) —
it's a template identity prompt for the role that audits board/test
health, drafts and sequences new D-tasks, and hands them off, with the
verification and git-discipline habits baked in. A consuming project
should copy it and fill in its own placeholders (project name, repo
path, owner name, GitHub App credentials) rather than using the
bracketed template as-is.

If you notice something wrong with the *project itself* — not just your
current task — read
[`.agent_prompts/raise_concern.md`](../.agent_prompts/raise_concern.md)
and file it on the concerns board (`orchestrate concern file`). This is
separate from a task report: a concern is for board-integrity problems,
process friction, technical/environment issues, or quality drift that
would still matter even if the specific task it came from were deleted.
Don't let a real finding disappear into a task report that gets read
once and forgotten — check `orchestrate concern list --status open` /
`orchestrate concern board` for what's currently open.

If you were handed a specific task directly (e.g. via a generated
delegation contract), skip straight to **Rule 2** below.

**Looking for something and not sure which file it's in?** Two search
tools exist, indexed with BM25 (not grep) — reach for whichever one
fits instead of hand-browsing (full detail:
[`../docs/LOCAL_SEARCH.md`](../docs/LOCAL_SEARCH.md)):
`orchestrate knowledge search` for durable knowledge (`KNOW-*.md`:
architecture facts, decisions, blockers, learnings — has type/tag/task
filters); `orchestrate search` for everything else Markdown (task
reports, design docs, concerns, `.agent_prompts/`). Neither is a
required step — if you already know the file, just read it.

## The four rules

**1. Startup — read `.agent_prompts/find_work.md` when told to find your
own work.** Don't ask what to do next; the protocol file already answers
that.

**2. Locking — always `claim` before doing anything else.**
```
orchestrate claim <TASK_ID> --agent "<your name>"
```
Never work on a task you haven't successfully claimed, and never work on
a task that's already claimed/in_progress/review by someone else. If
`claim` says the task is already claimed, pick a different one. If it says
the remote is unreachable or `git pull` failed, this is a synchronization
blocker, not evidence that the task is taken: do not work around it or edit
the board locally. Restore GitHub connectivity, then retry. The claim IS the
lock: it pulls the latest board state, refuses anything not open/blocked,
and publishes immediately so other agents see it right away.

**3. Off-chat reporting — write to `reports/`, not chat.**
Write your findings/diff/results to `reports/<TASK_ID>_report.md` (see
`.agent_prompts/find_work.md` step 7 for the full field-by-field format:
environment, summary, files changed, tests run, failures, verification,
questions/blockers, optional knowledge extraction). Don't paste the full
report back into chat for the primary agent to re-process; leave it on
disk and reference the path when you submit. Do post a short chat message
per task ("### ✅ D-007 submitted" + one line) and a final session
summary when you stop — that's a scan, not a re-post of the report; see
`.agent_prompts/find_work.md` steps 8–9 for the exact format.

**4. Completion — `submit`, not `complete`.**
```
orchestrate submit <TASK_ID> --notes "..." --report reports/<TASK_ID>_report.md --agent "<the exact same agent string you claimed with>"
```
This moves the task to **review**, not done, and keeps it locked to you —
nobody can re-claim it while it's awaiting review. Only the primary agent
or a human runs `approve` (→ done) or `reject --notes "..."` (→ open
again, with feedback, for anyone to pick up). You cannot mark your own
work done; that split exists on purpose (see "Why the review gate
exists" below).

## Why the review gate exists

A system that lets delegated agents mark their own work "complete"
directly has a specific, observed failure mode: a report claims several
tasks finished, but the board itself was never actually updated to
match, so it silently drifts from reality — the underlying work can be
completely real, but the board can't be trusted without independent
verification. Splitting "agent says done" (`review`) from "verified
done" (`done`, via `approve`) fixes that without slowing anyone down: the
task stays locked during review either way, so there's no overlap risk
on either side of that gate — only a brief window where "done" means
"someone actually looked," not "an agent said so."

## Where tasks come from

This package parses task definitions out of Markdown files with a
`## D-NNN — Title` / `## H-NNN` / `## C-NNN` double-hash header format
(`opencode_crack/orchestrator/task_parser.py`) — a project's own roadmap
document, plus (optionally) a `delegated_tasks/*.md` extension directory
for the delegate-tier subset. `orchestration/tasks.yaml` tracks only
generated state; the roadmap document(s) are the source of truth for
task *content*. Don't hand-edit `tasks.yaml` or any generated status
output — both are regenerated by the CLI and will just be overwritten.
The same split exists for the concerns board: `concerns/CN-###.md` files
(once a project starts filing them) are content;
`orchestration/concerns.yaml` and the generated concerns dashboard are
state — don't hand-edit those either.

New tasks get appended to the roadmap or its delegated extension files as
work surfaces them — e.g. a recon task finding a real security gap
becomes a new, scoped follow-up task rather than a loose finding that
nobody acts on. New tasks should use the same format and include an
`Origin:` line pointing back to what found it.

## Am I supposed to delegate this task, or do it myself?

```
orchestrate contract <TASK_ID>
```
Prints a DELEGATE / DO NOT DELEGATE verdict plus the full task brief
(scope, tools, expected output, stop conditions), built from the task's
own roadmap section — reusing the roadmap's own tiers and its
project-specific "Delegation rejection list" as a keyword-plus-action
safety net, not re-deciding policy on the fly.

## On ambition

Delegate-tier tasks aren't limited to read-only recon. Writing real
code, fixing real bugs, adding real tests — all fair game within a
task's stated scope, precisely because the review gate means nothing
ships without a second look, and git means anything can be reverted if
it's wrong. Don't artificially under-scope a task out of excess caution;
do stay inside what the task actually asks for, and use the STOP
CONDITIONS in `find_work.md` when something goes beyond that.

Ambition is about scope within a task, not about which task to pick —
see `find_work.md` step 3 for how to choose. Short version: pick what
matters most to the project (priority, what it unblocks, unresolved
rejections), never what merely looks easiest or hardest.

## What NOT to touch without a human decision

Every consuming project should define its own "Delegation rejection
list" in its roadmap doc — typically things like core data/memory schema
redesign, provider/router architecture, the security model itself,
secret handling, and authentication. These stay C-tier (or H-tier)
regardless of how bounded a particular change might look, because what
matters is what the change touches, not how small the diff is.

If a claimed/in_progress task shows **STALE** in `orchestrate status`
(48+ hours with no update, per `task_board.py`'s `STALE_AFTER_HOURS`),
don't just take it over — check its notes/commit history for why it
stalled first, then have it released (by a human, the primary agent, or
yourself if you're confident it's abandoned) before re-claiming it. Full
evidence checklist: `find_work.md` step 9.5.

## Concurrency & worktree isolation

Most sessions share one checkout — the strict "stop on any dirty state
you didn't create" rule in `.agent_prompts/find_work.md` step 0.75 is the
default and still applies in full there.

Some sessions run inside an **isolated git worktree** instead (created
via `git worktree add`, including ones provisioned by the `orchestrate
swarm` CLI path — see `.opencode/opencode-swarm.json` if your project
uses the OpenCode Swarm runtime). Git isolates worktrees from each other
for `git status` purposes by design, so another lane's files
structurally cannot appear in yours. In that case:

1. **The clean-tree check applies to your own worktree only.** Dirty
   state elsewhere (the shared/main checkout, or a sibling lane) is not
   your concern and is not contamination.
2. **Unexpected changes inside your own worktree still stop you.** This
   never becomes blanket trust — see Test C in
   `tests/test_worktree_guard.py`.
3. **Never manipulate another lane's worktree** — same rule as the
   shared-checkout case, just scoped: don't `git worktree remove`,
   `checkout`, `reset`, or edit anything outside your own worktree path.
4. **Coordinators are responsible for integration and cleanup**, not
   individual lane agents. `opencode_crack/orchestrator/worktree_guard.py::safe_cleanup_worktree()`
   is the only sanctioned removal path — it refuses to remove anything
   dirty or unmerged, and refuses the main worktree outright.
5. **If isolation provisioning fails or you're unsure which mode you're
   in**, check `python -c "from opencode_crack.orchestrator import worktree_guard as wg; print(wg.is_isolated_worktree())"`.
   If it returns `False` (or raises), treat yourself as being in the
   shared checkout and apply the strict rule — never assume isolation
   without verifying it.

Only the `orchestrate swarm` CLI path enforces worktree isolation before
launching a swarm — see [`SWARM_USER_GUIDE.md`](SWARM_USER_GUIDE.md) §B4
for the lower-level path that does not, and why you should default to
the CLI one. The underlying git-worktree primitive `worktree_guard.py`
uses has direct test coverage (`tests/test_worktree_guard.py`) and works
regardless of platform or whether the OpenCode Swarm runtime is
installed.

For actually running a swarm (installing the runtime, registering
agents, launching a bounded task, reading the dashboard), see
[`SWARM_USER_GUIDE.md`](SWARM_USER_GUIDE.md) — Part A is a full beginner
walkthrough with every command verified against this package's actual
CLI; Part B is the reference material. If you're guiding a human through
their first run interactively, use
[`.agent_prompts/guided_swarm_run.md`](../.agent_prompts/guided_swarm_run.md).

## Reporting back

Leave the board honest: every task you touch ends in `submit`, `release`,
or `block` by the end of your session — never left dangling as
"claimed"/"in_progress" with no further action. An activity log is
generated automatically on every transition, so the audit trail exists
independent of any single agent's summary — but it only reflects
transitions that actually happened through the CLI, not ones described in
chat after the fact.

## Reading order for a new project adopting this package

If you're setting up a fresh project on `opencode_crack` rather than
just working inside one that's already running:

1. [`NEW_USER_GUIDE.md`](NEW_USER_GUIDE.md) — install, task board basics,
   where everything lives.
2. This file — the core agent-facing rules.
3. [`LOCAL_SEARCH.md`](LOCAL_SEARCH.md) — the two search tools.
4. `.agent_prompts/find_work.md` and `.agent_prompts/raise_concern.md` —
   what a delegated agent actually runs.
5. [`SWARM_USER_GUIDE.md`](SWARM_USER_GUIDE.md) — only if you intend to
   run live agents through OpenCode Swarm rather than coordinating
   human-run or externally-run agents by hand.
6. [`KNOWLEDGE_AUDIT_PROMPT.md`](KNOWLEDGE_AUDIT_PROMPT.md) — once the
   knowledge base has enough entries to be worth periodically auditing.
7. `.agent_prompts/delegation_agent_identity.md` and
   `.agent_prompts/task_breakdown.md` and
   `.agent_prompts/critique_batch.md` — once you have a primary
   agent/owner role actively managing the board, not just delegated
   agents pulling from it.
