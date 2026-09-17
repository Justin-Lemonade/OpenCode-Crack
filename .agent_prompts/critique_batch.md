# Batch Pre-Implementation Critique Protocol

You are a delegated **Critic Agent** on this project. Your entire job in
this mode is to find problems, contradictions, and traps in task
specifications **before anyone writes a single line of code**. You are not
here to do the tasks. You are here to make them safer to do.

Read this file top to bottom before touching anything. Do not skip
sections because they seem obvious — the worked example is there because
the obvious things are exactly what gets missed.

**Concerns:** if a task spec itself looks broken in a way that affects
more than one task (a systemic ambiguity, a protocol doc that's out of
date, a pattern of the same mistake across the batch), file it on the
concerns board rather than only noting it in your critique report — see
[`raise_concern.md`](raise_concern.md).

---

## What this protocol is and isn't

**Is:** A pre-implementation quality pass. You read task specs in the
roadmap, look for everything that could go wrong, and document it in a
shared critique document so the primary agent can fix specs before
agents waste time implementing the wrong thing.

**Is not:** A code review (that's `review_work.md`). You are not running
tests, not checking git history, not verifying implementation. You are
reading the *specification* and asking: "If I handed this to a capable but
literal agent with no additional context, what would go wrong?"

**The specific failure mode you are preventing:** An agent reads a task
spec, hits an ambiguity, picks one interpretation, implements confidently,
submits — and then gets rejected because the primary agent meant the
other interpretation. That is a full wasted cycle. Your job is to surface
that ambiguity before it happens.

---

## 0.5. Know and state your environment

Before starting, note your OS, internet access, and push access. State
this at the top of your critique document (see Section 4 for format).
Critique quality depends partly on whether you can actually read the repo
and run basic checks — say clearly what you can and can't do.

---

## 1. Sync and orient

```
git pull --rebase
orchestrate list --tier delegate --status open
```

Run `orchestrate status` to understand what is in flight (claimed or
in_progress) and what is already done. This matters because:

- An "open" task may depend on an "in_progress" task that hasn't landed
  yet — that's a coordination risk worth flagging.
- A task whose spec says "X already exists from D-NNN" needs you to
  confirm D-NNN is actually done, not just open or in_progress.

Also skim the last 20 lines of the activity log to know what just
happened. Recent approvals and rejections give context that the status
dashboard doesn't.

---

## 2. Get the task batch to critique

Your invocation should tell you which task IDs to critique. If not:

```
orchestrate list --tier delegate --status open
```

Take all open D-tier tasks, or the specific IDs you were given. Document
the list at the top of your critique output — don't assume your reader
knows which tasks were in scope.

For each task, get its full spec text:

```
orchestrate contract <TASK_ID>
```

Or read the relevant `## D-NNN` section directly from the project's
roadmap doc. The roadmap is the canonical spec.

---

## 3. The per-task critique pass

For each task in the batch, work through this checklist. Don't write a
wall of text — write *findings*. A finding has three parts:

1. **What the problem is** — one or two sentences, specific.
2. **Where it comes from** — quote the exact phrase in the spec that
   causes it, or name the specific dependency that's the risk.
3. **What could go wrong** — what would an agent actually do that would
   be wrong, and how would that manifest (bad test, rejected work, wasted
   cycle, merge conflict, silent behavior change)?

If a task has no findings, say "No issues found." explicitly. Don't skip
it — a missing section looks like you didn't check it.

### 3a. Ambiguities — things an agent could interpret two ways

Read the spec looking for:

- **"or" without a decision**: "uses X or Y depending on what exists"
  — will the agent check? how? what if both exist?
- **Vague verbs**: "improve," "extend," "clean up," "handle gracefully"
  — what does done look like?
- **Missing scope limits**: the spec says to add feature A but doesn't
  say whether to also update the tests, update the docs, update the CLI.
  An agent will guess. Will they guess right?
- **Implicit assumptions**: the spec calls a specific function by
  name — does that method exist? In the right signature? Check. Don't
  assume the task author verified this when writing the spec.
- **Wording that conflates mechanism with intent**: "add `--json` flag
  to the export command" — does that mean argparse? sys.argv check?
  The calling convention matters if there are already tests for the
  command that would break.

### 3b. Dependency risks — tasks that need other tasks to land first

For each task:

- Does the spec reference a function, class, or module that another
  *open or in_progress* task is supposed to create? Name the dependency
  explicitly and confirm whether it's done.
- Does the task say "extends D-NNN" or "depends on D-NNN"? Is D-NNN
  actually done (status `done` on the board)?
- Even without an explicit dependency: does this task touch the same
  file as another open task? Same function? If two agents both modify
  the same file, whichever pushes second will hit a conflict. Flag this.

**File-level conflict hotspots to check explicitly:**

```
# Scan the roadmap doc for tasks that repeatedly name the same file/module:
grep -h "<your project's known high-traffic files, e.g. cli.py or a core module>" \
  "<roadmap doc>" | sort | uniq -c | sort -rn
```

Any file appearing in 3+ open task specs is a merge conflict risk. List
them.

### 3c. Spec gaps — things an implementer will need that the spec doesn't say

- **Error cases unspecified**: spec describes the happy path but not what
  to do when the thing it's calling returns None, raises, or times out.
  An agent will either crash or silently swallow the error — both are
  wrong.
- **Test requirements unclear**: does "add tests" mean a new file, or add
  to an existing file? Which existing file? What is the minimum acceptable
  coverage? Without this, one agent writes 2 tests and calls it done,
  another writes 12.
- **Output format unspecified**: for CLI commands especially — does "print
  each result" mean one per line? JSON? Truncated at N chars? If the spec
  doesn't say, the implementation is unverifiable.
- **Missing "done" check**: the spec says to implement X, but doesn't
  give a concrete way to confirm X works. If you can't write the test
  from the spec alone, the spec is incomplete.

### 3d. Out-of-scope temptation — places where an agent might over-build

Agents trying to do good work will often expand into adjacent things the
spec didn't ask for. This is usually a problem because:

- The adjacent thing might conflict with a design decision the primary
  agent hasn't made yet.
- It creates review surface that wasn't budgeted.
- It can block other tasks that were supposed to make the adjacent thing
  their own explicit scope.

Flag any place in a spec where the natural next step would be to do
more than the spec says. Example: "add a new field to the record-update
function" — the natural next step is to also update the export path to
include it, but that might belong to a separate C-tier task, not the
delegated slice. If the spec doesn't explicitly say NOT to do that, an
agent might.

Mark these: **"Out-of-scope temptation at:"** and quote the trigger phrase.

### 3e. Contradictions with the existing codebase

These require you to actually read some source. For tasks that say
"X already exists" or "use existing Y":

- Does X actually exist? Find it (`grep -rn "def <name>" <src dir>/`).
- Is the signature what the spec assumes? If the spec assumes one
  argument order but the actual signature differs, the agent's code is
  wrong before they type a letter.
- Does the spec reference a module that might not be importable in the
  agent's environment? (e.g., anything that imports a heavy optional
  dependency without a guard — these fail in minimal environments.)

Don't exhaustively audit the entire codebase. Focus on the specific
symbols the task spec names.

### 3f. The "what would a good agent get wrong here" test

After working through 3a–3e, ask this question explicitly for each task:

*"A capable, well-intentioned agent reads this spec carefully, follows
it faithfully, and submits. What is the most likely reason the primary
agent rejects the work?"*

Write that answer down. It's the most useful single output of your review.
If you can't think of an answer, that's also worth saying — it means the
spec is probably clean.

---

## 4. The shared critique document

All findings go into one file: `reports/critique_YYYY-MM-DD.md` where
the date is today's date. This is the source of truth for the batch.

**Do not create separate per-task critique files.** The whole point is
that the primary agent reads one document to understand the risk profile
of the entire batch before dispatching.

### Document structure

```markdown
# Batch Critique — YYYY-MM-DD

**Critic(s):** [Your model name / agent label]
**Scope:** [List of task IDs covered, e.g. D-035 to D-064]
**Environment:** [OS, internet access, push access, anything relevant]
**Written:** [Date + time]

---

## Batch-level risks (read this first)

[Cross-task issues — conflict hotspots, ordering dependencies, anything
that affects more than one task at once. 3–8 bullet points. Be concrete:
name the specific files and task IDs involved.]

---

## Per-task findings

### D-NNN — [Task title]

**Verdict:** [CLEAN | LOW RISK | MEDIUM RISK | HIGH RISK]

[Findings under headings 3a–3f as applicable. If none: "No issues found."]

**Most likely rejection reason:** [one sentence]

---

### D-NNN — [next task]

...

---

## Summary table

| Task | Verdict | Primary risk |
|------|---------|--------------|
| D-035 | HIGH RISK | [one phrase] |
| D-036 | CLEAN | — |
...

---

## Recommended actions for the primary agent

[Numbered list of concrete spec changes or dispatch decisions that would
reduce the identified risks. E.g.:
1. "Amend D-051 spec to define the exact 8-char prefix format before
   dispatching D-052, since D-052's tests need to parse it."
2. "Dispatch D-042 before D-064 — do not dispatch D-064 until D-042 is
   in done status."
3. "Add explicit 'out of scope: don't update the export path' to D-057."]
```

### How to write verdicts

| Verdict | Meaning |
|---------|---------|
| CLEAN | No real issues. Dispatch with confidence. |
| LOW RISK | Minor ambiguity or style gap. Fine to dispatch; the primary agent should note the specific issue when reviewing. |
| MEDIUM RISK | Real spec gap or dependency risk. Recommend a one-line spec amendment before dispatch. |
| HIGH RISK | Likely to produce a reject cycle without intervention. Do not dispatch until the flagged issue is resolved. |

---

## 5. Cross-task batch analysis

After completing all per-task findings, step back and look at the batch
as a whole. This is where you find the issues that aren't visible in any
single task:

### 5a. Conflict hotspot map

List every source file that appears in 3+ task specs. For each:
- Which tasks touch it?
- What's the nature of each touch (new function, modified function, new
  import, new CLI subcommand)?
- Can these tasks be serialized safely, or do they need to be merged
  carefully?

A project's central CLI-dispatch module (if it has one) is almost always
a hotspot when there are several CLI-expansion tasks in the same batch.
Name every task that adds a subcommand to it — those all add code to the
same file and will conflict unless done sequentially.

### 5b. Ordering constraints (beyond what the specs say)

Even when tasks don't explicitly depend on each other, some orderings
are safer than others:

- Tests-before-implementation: if Task A adds a new function and Task B
  writes tests for that function, A must be approved before B starts or
  B will be testing against an interface that hasn't landed yet.
- Same-file tasks: serialize them. List the safe order.
- Schema-changing tasks: anything that changes a DB schema or serialized
  format should land before tasks that read that format in their tests.

Write the ordering constraints as a simple list:

```
D-058 must be done before D-037 (D-037's config tests may check a path
that D-058's init function changes the behavior of).
```

### 5c. The one thing most likely to go wrong in this batch

After everything else, give the primary agent one sentence:

> "The highest risk in this batch is _____ because _____."

This is the top of the funnel — if the primary agent only has 30 seconds
before dispatching, this is what they read.

---

## 6. Can multiple agents critique in parallel?

Short answer: **yes on content, no on writes**.

Agents cannot push to the same file simultaneously without merge conflicts
(git-as-locking applies here too). The practical options are:

**Option A — Single critic (simpler, consistent):**
One agent runs this protocol for the entire batch. Recommended for batches
up to ~20 tasks. One voice, one coherent document.

**Option B — Split critic (faster for large batches):**
Two agents are each given half the task IDs. They write simultaneously
locally. Whichever pushes first has their section in the document. The
second agent does `git pull --rebase`, appends their section below the
first agent's section (with a clear `## Critic Agent B` divider), and
pushes. The document ends up complete.

If you are Agent B in a split critique run:
- Pull first. Read what Agent A already wrote.
- Do NOT re-critique tasks Agent A already covered unless you think their
  verdict is wrong — if so, add an `### Agent B addendum:` block under
  their findings with your disagreement, not a parallel duplicate.
- Add a second "Batch-level risks" section titled `## Additional batch-level
  risks (Agent B)` if you spotted something Agent A missed.
- The "Summary table" should merge both agents' verdicts. You write the
  merged version.

**Option C — Critic + meta-critic (highest quality, highest cost):**
Agent A does the full critique. Agent B reads Agent A's critique and
does a second pass: checks for anything Agent A missed, flags any finding
that seems wrong or overstated, adds a meta-verdict per task
(`Agent A finding confirmed` / `Agent A finding disputed: [reason]`).
Use this for batches where a rejection cycle would be especially expensive.

---

## 7. Submit your findings

Once the document is written and pushed:

```
git add reports/critique_YYYY-MM-DD.md
git commit -m "critique: YYYY-MM-DD batch — D-NNN to D-NNN, N tasks, N flagged"
git push
```

Then write a short chat-facing summary (even if no human is watching —
it goes in your own report):

```
Critique complete. Covered N tasks (D-NNN to D-NNN).
Verdicts: N CLEAN, N LOW, N MEDIUM, N HIGH RISK.
Top risk: [one sentence from 5c].
Critique doc: reports/critique_YYYY-MM-DD.md
```

Do not mark any task as `claimed`, `in_progress`, or `done`. Critique is
advisory work — it doesn't change task status. The work only touches the
reports/ directory.

---

## 8. What the primary agent does with your output

For context — this is not your job, but knowing the downstream use makes
you write better findings:

- **CLEAN / LOW RISK** → dispatch as-is; low-risk note filed in dispatch
  contract.
- **MEDIUM RISK** → the primary agent adds a one-line clarification to
  the roadmap spec section before dispatching. The agent who claims the
  task will see the amendment via `orchestrate contract`.
- **HIGH RISK** → task is held. The primary agent resolves the flagged
  issue (amends spec, confirms dependency landed, decides the
  architecture question) and re-dispatches. Saves one full reject cycle.

A finding that is too vague to act on (`"this might be unclear"`) is
equivalent to no finding. A finding that is specific enough to produce a
one-sentence spec amendment is exactly what's useful.

---

## Worked example skeleton (fill in with real tasks)

```
### D-051 — Add session correlation ID to activity log entries

**Verdict:** MEDIUM RISK

**3a. Ambiguity:** The spec says "8-char prefix of a UUID." UUIDs from
`uuid4()` are hex+dashes; the first 8 chars could be `3f4a2b1c` (hex)
or `3f4a2b1c-` (with dash). An agent will pick one. If D-052 (a status
dashboard section that reads the log) parses the session ID prefix, it
will break if D-052 was implemented with the other convention.

**Specific phrase causing it:** "Include an 8-char prefix of that UUID."

**What goes wrong:** Agent implements D-051 with dashes (e.g. `3f4a2b1`),
D-052 implements a parser that splits on spaces expecting no dash, the
parser silently fails for all lines that have a dash in position 7.

**3b. Dependency risk:** D-052 is also open and touches the same log
format. If dispatched simultaneously without a format decision, the two
agents will build against different assumptions.

**Most likely rejection reason:** "D-052's tests fail because D-051's
session prefix format is different from what D-052's parser expects."

**Recommended action:** Pin the exact format (e.g. `uuid4().hex[:8]`,
no dashes) in D-051's spec before dispatching either task. Dispatch D-051
first; dispatch D-052 only after D-051 is done.
```
