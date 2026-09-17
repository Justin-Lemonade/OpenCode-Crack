# Task Breakdown Protocol

What to do when the delegate-tier (`D-*`) queue runs dry but the C-tier
(`C-*`) backlog is still large. C-tier tasks are architecture/design work
by nature; they don't get delegated whole. This is how to find the
bounded, safe slices inside them that DO belong in the D-tier queue,
without turning a large architecture roadmap into hundreds of shallow
busywork tickets.

Task breakdown integrates with the durable knowledge system: each
breakdown should reference relevant knowledge from `knowledge/architecture/`
and `knowledge/decisions/`, and completed breakdowns should extract
knowledge to `knowledge/tasks/completed/`.

Pairs with `review_work.md` (verify submitted work, fix the prompt) and
`find_work.md` (how agents pick work). This is the third piece: keeping
the pool of pickable work healthy in the first place.

**Before assigning new IDs:** re-check the *entire* `delegated_tasks/`
tree for the highest existing D-ID, not just the most recent file. A
real ID collision — two files independently claiming the same ID with
different scopes — will break board sync for everyone until it's caught
and fixed. Cheap to avoid, expensive to untangle after the fact.

**Concerns:** if you find a systemic issue while breaking down tasks
(e.g. several C-tasks share an ambiguity, or the roadmap doc itself has
drifted from reality), file it — see [`raise_concern.md`](raise_concern.md).

---

## When to run this

Check `orchestrate status`. If **Delegatable work** is near 100% done and
**primary-agent work** isn't, the queue is about to run dry (or already
has) — that's the trigger, not a calendar schedule.

## Step 1 — Survey, don't skim

```
orchestrate list --tier primary_claude --status open
```

Read every title. You're not picking the "interesting" ones — you're
looking for a specific shape: **a C-task that contains at least one piece
which is bounded, testable, and doesn't require a design decision that
hasn't been made yet.** Most won't have one. That's expected — architecture
tasks are architecture tasks. Don't force it.

## Step 2 — For each candidate, check before you assume

This is the part that's easy to skip and expensive to skip. Before
writing a new task:

1. **Read the actual C-task section in the roadmap doc**, not just the
   title — a title can undersell that a task is really "one big design
   decision plus several sub-features," and the sub-features aren't all
   equally hard.
2. **Check whether it's already partly done.** Grep for related existing
   code, existing tests, existing xfail markers. A project that pins
   known gaps as `pytest.mark.xfail(strict=True, reason="...")` has
   already written a contract for exactly what's missing, authored by
   someone who already thought through the shape of the fix. A task
   built on top of an existing xfail needs far less spec-writing and is
   far less likely to be misunderstood by whoever picks it up. Search
   for these before writing a task from scratch:
   ```
   grep -rn "xfail" tests/
   ```
3. **Check whether a different completed task already covers most of
   it.** Re-delegating already-covered ground wastes an agent's session
   and produces a report that looks productive but adds nothing.
   `grep -rn "^def test_" tests/` and read through what's already there
   before assuming a gap exists.
4. **Check what it depends on.** If the bounded-looking piece actually
   needs an interface that doesn't exist yet, it's not ready — note that
   explicitly and move on rather than writing a task that will just get
   blocked.

## Step 3 — Decide: break it down, or leave it whole

**Break down** when you can point at a specific piece and say, concretely
and without hedging: "here is the exact function/file, here is what
'done' looks like, here is why it doesn't need the undecided part of the
parent task." If you can't say that plainly, it's not ready — don't write
a task that just moves the ambiguity downstream to whoever claims it.

**Leave whole (don't force a breakdown)** when:
- The task IS the design decision (there's no implementation to delegate
  until someone decides the shape).
- Every piece depends on another in-progress or undecided C-task.
- The only way to scope a "bounded" piece would be to invent
  requirements the roadmap doesn't actually specify — that's writing
  fiction, not delegating.

Getting this wrong in the direction of "break down everything" is worse
than getting it wrong in the direction of "leave too much whole" — a
vague delegated task produces a report you then have to untangle in
review, which costs more than it saved.

## Step 4 — Write the new task(s)

Same format as every other roadmap task, plus:
- `**Origin: broken out of C-0XX via .agent_prompts/task_breakdown.md
  process, <date>.**` — so anyone reading it later knows this wasn't an
  original independent task, and can trace back to the parent's full
  context if they need it.
- If an xfail test already specifies the contract, say so explicitly and
  point at it by name — "flip it to passing, don't rewrite it" heads off
  an agent redesigning something that's already been designed.
- Explicitly state what's OUT of scope — the parts still belonging to
  the parent C-task — so the new task doesn't quietly balloon back into
  the thing you just extracted it from.

Then go back to the parent C-task's section and add a short
**"Partially delegated"** note pointing at the new task(s), so the split
is visible from both directions — someone reading the C-task later
shouldn't have to already know a breakdown happened.

If a C-task turns out to be almost entirely already covered by existing
work, say so plainly in its own section rather than leaving it looking
like 100% outstanding work.

## Step 5 — Verify before shipping

```
python -m opencode_crack.orchestrator.task_parser   # new tasks parse, right tier/priority
pytest tests/ -q
```
Also run the delegator's decision on each new ID to confirm the
action+sensitive-target safety net doesn't false-positive on your
wording:
```python
from opencode_crack.orchestrator import task_board, delegator
for tid in [...]: print(tid, delegator.decide(task_board.get_task(tid)))
```

## On "assigning" tasks — don't manually claim them

With `find_work.md`'s selection criteria in place, agents pull their own
work; git's push-rejection is the collision lock across however many
agent windows are running at once. Manually pre-claiming tasks on an
agent's behalf *shrinks* the pool the real agents can self-select from —
it fights the system instead of using it. "Assigning" work in this setup
means: **write well-scoped tasks and leave them open.** That's the whole
mechanism. Only claim something yourself if you (the primary agent) are
about to do it yourself right now.

---

## Knowledge integration

Task breakdown feeds the durable knowledge system. When breaking down
a C-tier task, use the production CLI — never hand-browse or hand-write
`KNOW-*.md` files (the writer allocates globally-unique IDs and validates
the format).

### Before breakdown

Search each layer explicitly (all filters are strict) — see
[`../docs/LOCAL_SEARCH.md`](../docs/LOCAL_SEARCH.md) for full usage and
when to use which tool:

```bash
orchestrate knowledge search --type architecture --query "<subsystem>"
orchestrate knowledge search --type decision --query "<area>"
orchestrate knowledge search --type blocker --query "<subsystem>"
orchestrate knowledge search --type learning --query "<area>"
```

If those don't turn up what you need — e.g. checking whether a similar
task was already attempted, or how a past design decision was reasoned
through in its report — also try the repo-wide search, which covers
`reports/`, `delegated_tasks/`, `docs/`, and `concerns/` (not just the
knowledge tree):

```bash
orchestrate search "<subsystem or prior task ID>"
```

### During breakdown

Include knowledge references in the task spec **only when they clear a
real confidence bar** — you've actually read the entry (not just the
title/snippet) and it's genuinely relevant to the task being written,
not merely returned by the search. If nothing found is a solid match,
or the D-task is simple enough that prior context wouldn't change how
it's done (e.g. "add a test for the null-input case"), leave the
section out entirely rather than padding it with a weak citation — a
wrong reference costs the next agent more than no reference does:

```markdown
**Relevant Knowledge:**
- `KNOW-YYYYMMDD-NNN` — Architecture fact
- `KNOW-YYYYMMDD-NNN` — Related decision

**Dependencies:**
- Blockers to resolve first
- Prerequisites from other tasks
```

### After breakdown

File genuinely new understanding through the production writer (one entry
per real finding — never one entry per task as a ritual):

```bash
orchestrate knowledge durable-file --type architecture \
  --title "<finding>" --body "<facts, evidence>" --task C-XXX --agent "<you>"
```

For example, after breaking a C-task down into two or more D-tasks, file
any component boundary discovered along the way (`--type architecture`)
and, if it's worth preserving, the breakdown rationale itself
(`--type task`). If the breakdown replaces an older entry, retire it
with `durable-supersede OLD --with NEW` instead of leaving two
contradictory active entries.

### Knowledge extraction targets

When breaking down complex tasks, extract:

| Category | What to Extract |
|----------|----------------|
| Architecture | Component relationships, data flow, boundaries |
| Decisions | Why this breakdown approach, rejected alternatives |
| Blockers | Any new blockers discovered during analysis |
| Learnings | Insights about the subsystem's structure |
