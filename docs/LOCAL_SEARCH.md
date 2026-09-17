# Local Search Tools — for finding things already in this repo

Both tools below are BM25 keyword search over a local SQLite index
(Python's stdlib `sqlite3`, no external service, works offline). They
answer "does something already exist about X" faster and more
precisely than hand-grepping — grep finds every line containing a
substring; these rank *whole documents* by relevance, so the most
on-topic result surfaces first even when the exact words differ
slightly.

**Neither is a required step.** For a simple, well-scoped task ("run
the tests and fix the failure," "add a missing null check") there's
often nothing to search for — just do the work. Reach for these when
you're not sure whether something relevant already exists, not as a
ritual before every task.

## 1. `orchestrate knowledge search` — durable knowledge

Searches `knowledge/**/KNOW-*.md`: structured entries with a type
(architecture / decision / blocker / learning / task), tags, and a
confidence rating. This is the project's accumulated "things we
learned the hard way" layer.

```bash
# Keyword search across all durable knowledge
orchestrate knowledge search --query "worktree isolation"

# Narrow by type, task, tag, or file (filters are strict — AND, not fuzzy)
orchestrate knowledge search --type decision --task D-XXX
orchestrate knowledge search --tags "worktree,isolation"
orchestrate knowledge search --file opencode_crack/orchestrator/worktree_guard.py

# Full content of one entry once you've found its ID
orchestrate knowledge durable-show KNOW-YYYYMMDD-NNN

# Machine-readable output (for scripts/contract-building, not manual reading)
orchestrate knowledge search --query "worktree isolation" --json
```

Use this when you want a *fact* — a decision that was already made, an
architectural constraint, a blocker someone already hit, a lesson
already learned — not just "where is this discussed."

## 2. `orchestrate search` — everything else (Markdown, repo-wide)

Searches `reports/`, `delegated_tasks/`, `docs/`, `concerns/`,
`.agent_prompts/` — anything else in the repo written in Markdown.
No structured schema, just path + title + full text.

```bash
orchestrate search "worktree cleanup"
orchestrate search "cross-lane contamination" --path reports/
orchestrate search "search integration" --limit 5 --json
```

Use this when you want *context* — has anyone already attempted
something like this, how was a similar design decision reasoned
through, what did a past task's report actually say — and you don't
know which specific file to open.

## Which one do I use?

| You're looking for... | Use |
|---|---|
| A specific fact/decision/gotcha that should be "durable knowledge" | `knowledge search` |
| An old task's report, a design doc, "has this come up before" | `search` |
| Not sure | Try `knowledge search` first (narrower, higher-signal); fall back to `search` if it comes up empty |

Neither tool does external/web search — both index only files already
in this repo's own working tree. A project consuming this package is
free to add its own external-search layer on top; that's outside this
package's scope.

## When it's worth checking mid-task, not just before claiming

Search isn't only a pre-work step. If you hit something unfamiliar
partway through — an unfamiliar pattern in the code, a design choice
that looks odd, a test failure that doesn't obviously relate to your
change — a quick `knowledge search` or `search` for the relevant term
can save a wrong guess. Treat it the same as you would `grep`: a tool
you reach for whenever it might save time, not a fixed checklist item.

## Confidence bar for citing results in written output (task specs, reports)

Both tools return best-effort ranked matches — not everything returned
is actually relevant. Before citing a result in a task spec or report
(e.g. "**Relevant Knowledge:** `KNOW-...`"), confirm it's genuinely
on-topic by reading it, not just skimming the title/snippet. If
nothing found is a good match, or the task is simple enough that prior
context wouldn't change how you'd do it, say nothing rather than
padding the output with a weak citation — a wrong or tenuous
reference is worse than none, because the next agent will trust it.

## Related

- `.agent_prompts/find_work.md` — where these tools fit into the normal work loop
- `.agent_prompts/task_breakdown.md` — how to cite search results when writing new task specs
