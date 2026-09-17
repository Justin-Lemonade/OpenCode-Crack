# Find Work Protocol

You are a delegated coding agent working on this repository. You were
told to run this file. Follow it top to bottom. When you reach the end,
loop back to step 1 unless you were told to do a fixed number of tasks or
to stop.

**Concerns:** if you hit something wrong with the *project itself* (not
just your current task) — board integrity, process friction, a
technical/environment issue, quality drift — file it on the concerns
board instead of letting it disappear into a task report nobody re-reads.
See [`raise_concern.md`](raise_concern.md); check `orchestrate concern
list --status open` for what's already open before filing a duplicate.

Do not ask the person who invoked you what to work on — that's what this
file is for. Only stop and ask a human if you hit a STOP CONDITION below.

---

This protocol is the accumulated result of iterating on real, observed
failure modes in a multi-agent task board over many rounds — not a
first-draft design. If you're revising this file yourself, keep a
changelog entry stating what changed and why (not just what), the same
way the rules below expect precision from your own task reports.

## 0.5. Know (and state) your actual environment

Before doing anything, note: your OS, whether you have live internet
access, whether you have push access to this repo, and whether any local
model runtimes (Ollama, llama.cpp, etc.) are actually reachable from
where you're running. Don't assume — check. You'll restate this at the
top of your report (step 7); whoever reviews your work cannot see your
runtime and reviewers have been wrong before guessing at it.

Also record whether the repository is inside a cloud-sync location such as
OneDrive. Prefer a local, non-synced working copy for delegated work. If the
repository is under active sync or its Git metadata appears damaged, do not
continue working around it; stop and report the condition.

## 0.75. Git preflight — protect the shared working tree

Before pulling, claiming, or editing anything:

```
git status --short --branch
git branch --show-current
git remote -v
```

**If you're in an isolated worktree** (created via `git worktree add`,
including one provisioned by `orchestrate swarm` — check with
`python -c "from opencode_crack.orchestrator import worktree_guard as wg; print(wg.is_isolated_worktree())"`,
or just: `git rev-parse --absolute-git-dir` contains `/worktrees/`),
dirty state in *other* worktrees or the shared/main checkout is not your
concern — git isolates worktrees from each other for `git status`
purposes, so another lane's files structurally cannot appear in yours.
The clean-tree rule below applies to **your own worktree only**. See
`opencode_crack/orchestrator/worktree_guard.py::check_contamination()`
for the exact logic.

**If you're in the shared/main checkout** (the common case — isolated
worktree provisioning currently only happens automatically through the
`orchestrate swarm` CLI path, see `docs/SWARM_USER_GUIDE.md` §B4):
dirt left by *another agent* is not automatically a stop condition —
only stop if it's actually risky to proceed past. Concretely:

- **Known-harmless, self-healing generated state** — e.g. a durable
  knowledge sidecar file that a script explicitly documents as
  safe-to-leave-uncommitted — is **not** a stop condition. Proceed; if
  your own commit happens to include it, that's fine; if not, leave it
  for the next commit that does.
- **Anything else uncommitted or untracked that you did not create** —
  real WIP changes, an in-progress edit to a file you'd also need to
  touch, anything not documented as self-healing generated state — is
  still a hard stop. Do NOT modify, stash, reset, clean, checkout, or
  commit it. Report the exact state instead.
- If you're not sure which category something is in, treat it as the
  hard-stop case — this distinction only lowers the bar for cases with
  a clear, documented reason, not a general license to guess.

This is especially important when multiple agent windows share one clone.

**Either way**, unexpected changes *inside your own workspace* (isolated
worktree or shared checkout) are never something to silently ignore —
isolation changes whose dirty state you're responsible for investigating,
it does not create blanket trust that anything you find there is fine.

If Git reports an invalid repository, missing/corrupt objects, merge
conflicts, detached/unexpected branch state, or another repository-integrity
problem, stop before changing anything. Do not run destructive recovery
commands. A healthy clone should be used instead.

Do not create temporary workaround files, probe scripts, generated artifacts,
or test fixtures merely to bypass a shell/tooling failure unless they are
explicitly inside the task's scope and will be removed or retained as part
of the task. A tooling failure is not permission to alter the repository.

## 1. Sync

```
git pull --rebase
```

If this fails specifically because of a documented, known-harmless
self-healing sidecar file (per step 0.75) and nothing else is dirty,
commit just that file with a plain message and retry the rebase — a
stuck rebase over it otherwise wrongly blocks all forward progress. For
any other failure (network, real conflicts, auth, repository integrity,
or unstaged changes you can't attribute to a documented harmless case),
stop and report the error — don't try to force past it. A network
failure is a synchronization blocker, not proof that a task is taken; do
not claim or edit the board offline. Retry once the remote is reachable.

After syncing, verify the branch and working tree again. Do not assume the
pull succeeded merely because the command returned without an obvious error.

## 2. See what's actually going on

Run `orchestrate status`. Note in particular:
- Anything in **Awaiting review** that's yours from a previous run and got
  rejected — that has feedback waiting for you in its notes.
- Anything flagged **STALE** — don't touch it yet; that's for a human or
  the primary agent to look at, not something to silently take over.
- Tasks whose notes say they are superseded, duplicated, already completed,
  or otherwise no longer actionable — do not claim them merely because they
  appear in an open list.

If the status/board tooling itself errors, first distinguish a transient
command problem from a repository/board problem. Do not silently work around
board state and do not claim a task you cannot reliably verify.

## 3. Pick a task

```
orchestrate list --tier delegate --status open
```

**Do not pick based on how easy or hard a task looks.** Pick based on what
actually matters most to the project right now. Work through these in
order — the first one that clearly favors a task wins:

1. **Rejected tasks with feedback come first.** If a task's notes explain
   why it bounced from review, that's unresolved work someone is actively
   waiting on, and you likely already have partial context on it. Prefer
   these over anything fresh.
2. **Unblocking > independent.** If completing task A is a prerequisite
   the roadmap doc mentions for other open tasks (same subsystem, same
   file area, or explicitly referenced), prefer A — finishing it creates
   more downstream options than an equally-priority task that unblocks
   nothing.
3. **Priority label**, as the roadmap doc states it (e.g. CRITICAL > HIGH
   > MEDIUM > LOW, or whatever scale this project uses). This is the
   project's own judgment of importance — don't second-guess it downward
   because a task looks tedious, or upward because it looks impressive.
4. **Fit for right now**, as a tiebreaker only: don't claim a task that
   needs something you don't have (e.g. a doc-research task needing live
   internet access, if you're running fully offline) just because it's
   next on the list — skip to the next-highest-priority task you can
   actually do, and note in your eventual report that you skipped it and
   why, so it doesn't look silently ignored.
5. **Never let "I already have momentum here" override 1–3.** Related
   context is a fine tiebreaker between two similarly-important tasks; it
   is not a reason to pick a lower-priority task over an open
   higher-priority one.

Before claiming, perform a **fresh task-state sanity check** for the
candidate: confirm it is still open, delegate-tier, not marked stale,
not superseded, and not already represented by a newer task covering the
same scope. Do not infer this from an old report, commit message, or memory.
If two tasks appear to cover the same work, prefer the existing/newer
canonical task and report the duplication rather than implementing both.

When a task references a prior implementation or claims something is already
wired/fixed, verify that claim against the current repository before treating
it as complete. A commit message or previous agent report is evidence, not
proof. In particular, verify public-entrypoint reachability for claimed
wiring/features, not merely the existence of a parser/module/function.

**Never look at H-tier or C-tier tasks as things to do.** Those are
human-only or primary-agent-only by design, not just by priority.

**Don't claim more than one task at a time**, even if several look appealing
— finish (submit) or explicitly stop on the current one before claiming the
next. A pile of simultaneously-claimed tasks from one agent is exactly the
kind of untracked-progress problem this system exists to prevent.

## 3.5. If you were told to pick up a specific claimed/stale/in-progress task

Sometimes you won't be starting from an open list — a human or the
primary agent may hand you a specific `<TASK_ID>` that's already
`claimed`, `in_progress`, or flagged `STALE` in `orchestrate status`, and
tell you to take it over directly. This is different from the autonomous
case in step 9 — you have explicit authorization already, so the bar is
lower, but you still owe the board an honest account of what you're
inheriting:

1. **Read before touching anything.** Pull the task's current notes,
   its report if one exists, and `git log --follow` on any files its
   scope names. Understand what the previous holder did or didn't do —
   don't start over blind.
2. **Check for real, uncommitted work in the shared tree.** If the
   previous agent left finished-but-uncommitted changes, prefer to
   *adopt* that work (verify it, finish it, commit it under your own
   name with credit in the report) over discarding and redoing it.
3. **State plainly in your eventual report** that this was a takeover,
   who/what you inherited from (agent name if known, or "unknown, board
   showed X"), and what evidence you used to confirm it was safe to
   proceed (e.g. "human explicitly authorized," "confirmed via commit
   timestamps the prior window has been silent N hours").
4. **Release, don't silently overwrite, if you find the task is actually
   still active** (e.g. a very recent commit, or notes suggesting the
   holder is mid-step). Report the contradiction and stop rather than
   racing the original owner.
5. Once inherited, release the previous owner's hold explicitly
   (`release --force` with a note citing who authorized the takeover —
   the ownership check requires the override since you are not the
   owner), then claim it yourself from step 4 through the rest of the
   normal loop. Do not skip the release: starting another owner's
   claimed task directly is rejected by the board itself.

## 4. Claim it — before reading further into the task, before writing any code

```
orchestrate claim <TASK_ID> --agent "<a name that identifies you, e.g. 'DeepSeek V4 Flash Free (window 3)'>"
```

If the claim says the task is already claimed/in_progress/review, do not work
on it; go back to step 3 and pick a different task. If it reports a remote
outage or another `git pull` failure, that is a synchronization blocker, not
evidence that the task is taken. Do not edit the board or work offline;
restore GitHub connectivity and retry. This is the whole point of claiming
first: two agents reading the same open list at the same moment must not both
start the same work.

```
orchestrate start <TASK_ID> --agent "<the exact same agent string you claimed with>"
```

**Ownership:** your claim records you as the task's owner. Every
worker-side move after that (`start`, `submit`, `block`, `release`) must
present the same `--agent` string or it is rejected by the board — this
is what stops one window from silently advancing another window's task.
The sanctioned exceptions are stale-takeover release (`release --force`,
step 9.5 only, audit-logged) and primary-agent/human overrides. Never
"borrow" another agent's string to get past the check; a mismatch caused
by your own typo is fixed by re-running with the claim string, not by
overriding.

## 5. Get the full task brief

```
orchestrate contract <TASK_ID>
```

This prints the DELEGATE/DO-NOT-DELEGATE verdict and the full task text
(embedded, not just a pointer) — scope, what "done" looks like, and stop
conditions. Read all of it before writing anything.

The contract also carries relevant durable knowledge — `orchestrate
contract` injects a budgeted `RELEVANT PROJECT KNOWLEDGE` block
(summary, key facts, evidence, why-relevant) when anything scores above
zero, and nothing at all when nothing is relevant. Treat that block as
input with the same weight as the task text itself. If a listed entry
contradicts what you're about to do, that's worth a line in your
eventual report, not something to silently override.

Separately, the contract may also carry a `RELEVANT PRIOR KNOWLEDGE`
block — one-line pointers into the Knowledge Board that the orchestrator
already judged relevant (id, status, title only). Read these before
starting, same weight as the task text itself — same rule as the
durable-knowledge block above: a contradiction is worth a line in your
report, not something to quietly work around. Run `orchestrate knowledge
show <id>` if you need the full entry, not just the title.

When you need knowledge the contract didn't include, search explicitly
instead of hand-grepping — see step 5.5 below for both search tools
and when to use each; the short version is `orchestrate knowledge
search` for durable knowledge, `orchestrate search` for everything
else (reports, docs, concerns).

After reading the contract, re-check that your planned implementation stays
inside its scope. If the contract conflicts with an old report, commit
message, or your assumption, the current contract wins; report the
contradiction rather than silently broadening scope.

## 5.5. Tools available while you work (use if useful, skip if not)

Beyond the knowledge already injected into your contract (step 5), a
few more resources exist. None of these are required for every task —
if you're fixing a typo or adding one obvious test, there's likely
nothing here worth reaching for. They're worth knowing about because
skipping them on a task where they *would* help usually means
re-deriving something someone already figured out:

- **Local search (this repo's own content, not the web):** two
  separate tools — `orchestrate knowledge search` for durable
  knowledge (decisions/architecture/blockers/learnings), `orchestrate
  search` for everything else Markdown (old reports, design docs,
  concerns). Full usage and a "which one do I use" table:
  [`../docs/LOCAL_SEARCH.md`](../docs/LOCAL_SEARCH.md).
- **Bundled skills (some swarm-runtime agents only — most sessions don't
  have this):** if your runtime is an OpenCode Swarm agent, it may have
  a skill library available. See
  [`delegation_agent_identity.md` §8](delegation_agent_identity.md) for
  what that is and isn't — don't assume it exists or invent equivalent
  tooling if it doesn't.

Don't front-load a search just to have done one — if step 5's injected
knowledge already covers what you need, or the task is simple enough
that there's nothing to look up, proceed straight to step 6.

## 6. Do the work

You're allowed to actually implement things, not just analyze — write
code, add tests, fix bugs — as long as you stay inside the task's stated
scope. Don't be timid about a task just because it involves real changes:
everything you do goes through review before it counts as done (step 8),
and it's all in git, so it can be reverted if it's wrong. That safety net
exists specifically so you don't have to under-scope your own work.

**If you hit something unfamiliar mid-task** — an odd pattern in the
code, a test failure that doesn't obviously relate to your change, a
design choice you don't understand — a quick local search (see 5.5
above) before guessing can save a wrong turn. Treat it like `grep`:
reach for it when it might actually help, not as a checklist item.

**Root-cause-first rule:** fix the underlying defect described by the
contract, not merely the first failing assertion. Before changing a test,
determine whether the implementation or the test is stale. A green test is
not sufficient if it no longer checks the current contract. Conversely, do
not change production behavior merely to satisfy an outdated test.

Classify observed problems as:
- genuine repository defect;
- stale/incorrect test;
- environment/dependency limitation;
- test-isolation/flakiness problem;
- documentation/state-data defect; or
- unresolved architectural/owner decision.

Do not convert an environment limitation into a code fix, and do not convert
an ambiguous architectural decision into an agent implementation without an
explicit contract decision.

If a task fixes a regression, add or update a regression test that would fail
under the old behavior where practical. If the behavior is difficult to test,
explain why in the report.

What you must NOT do:
- Touch anything outside the task's stated scope.
- Touch anything on the roadmap's "Delegation rejection list" (core
  memory/data schema, provider/router architecture, security model,
  secret handling, authentication) even if it seems related — stop and
  report back instead (see STOP CONDITIONS).
- Mark your own task "done" — that's not yours to decide (step 8). This is
  the only thing "don't push" ever meant here: pushing your actual code/test
  changes to `main` is fine (that's normal git use, and how your work becomes
  reviewable in the first place) — what's not fine is self-declaring the task
  finished without going through `submit` → `approve`.
- Fix unrelated failures just because they appear in the full test suite.
  Record them if relevant, but leave them for the appropriate task.

Run whatever tests are relevant before moving on. If you added code, add or
update tests for it. When practical, run the narrow regression test first,
then the relevant subsystem suite, then the full suite if the change warrants
it.

**Commit isolation — required:** Commit only your own task's files. Before
committing, run `git diff --name-status HEAD` (or `git status`) and verify
that every modified file belongs to this task. **If you're in an isolated
worktree** (see step 0.75), this is naturally satisfied — your worktree
can't contain another lane's files. **If you're in the shared/main
checkout** and it contains changes from another agent window (a
concurrent task's uncommitted files), do NOT include them in your commit —
commit only the files you changed. If you cannot separate them cleanly
(e.g. the same file was edited by two agents), stop and report the
conflict rather than committing a mixed change. A commit that accidentally
bundles another task's files causes misleading Git history and forces
reviewers to re-trace what actually changed.

**Pre-commit ownership check:** immediately before every `git commit` and
every `git pull --rebase`, re-run `git status --short` and `git log -1
--format=%H` and compare against what you expect. If either shows
activity you didn't make since your last check, stop and re-read the
actual current state of every file you're about to commit — don't assume
your in-memory understanding of the file is still accurate. Shared
working trees under concurrent agents are exactly the environment where
this matters most; this costs seconds and prevents board state from
being silently clobbered by a concurrent window.

**Don't commit yet if you're filing knowledge (step 7).** `orchestrate
knowledge durable-file` writes a file to disk but does not commit or
push it — if you commit your code/test changes now and file knowledge
after, the knowledge entry is left as uncommitted dirt in the shared
tree for the next agent to trip over. Do step 7's knowledge filing
*before* this commit, then commit code + tests + any new/updated
`KNOW-*.md` files together as one commit. If you have no durable
knowledge to file, commit as normal.

## 7. Write your report

Create `reports/<TASK_ID>_report.md` with:

0. **Environment** — one line: OS, internet access (yes/no), push access
   (yes/no), whether the repo is cloud-synced, and any local model runtimes
   actually reachable. From step 0.5.
1. **Summary** — what you did, in a few sentences.
2. **Files changed** — run `git diff --name-status <base>..HEAD` (where
   `<base>` is the commit before your work started) and paste the output.
   Also state the ACTUAL current git state: "pushed in commit `<hash>`",
   "committed locally, not pushed", or "working tree only, no commit" —
   whichever is true the moment you write this. Do not guess the git state;
   check it just before writing this section.
3. **Tests run** — command(s), relevant pass/fail counts, and whether each
   result was run before or after the change.
4. **Failures** — anything that didn't work, anything blocked by the
   environment, and anything intentionally left untouched because it was
   outside scope. Distinguish observed failures from expected/unrun cases.
5. **Verification** — state which important claims were directly verified
   against the current repository and which came from prior reports, task
   notes, or external research.
6. **Questions/blockers** — anything the primary agent or a human should
   weigh in on.
7. **Reusable discovery (optional)** — did this task turn up something
   worth remembering beyond this one report: an architecture fact, a
   decision, a blocker, or a general learning someone else would benefit
   from finding later? If so, file it with `orchestrate knowledge file
   ...` and reference the resulting `K-###` here. This is genuinely
   optional — most tasks don't produce anything durable-knowledge-worthy,
   and padding a report with a marginal entry just to fill this section
   is worse than leaving it out. Only file something you'd actually want
   surfaced to a future agent working on related code.

Two more rules that matter more than they sound like they should:

- **Describe the actual observed behavior, not an upgraded version of it.**
  If something silently returns wrong data, say "silently returns wrong data"
  — not "rejects," "fails," or "breaks," which read as a crash/exception to
  a reader skimming your summary. If you didn't actually run the failing case
  and watch what happened, say that too ("expected to X based on reading the
  code, not run").
- **Mark which claims you verified yourself vs. which you're reporting from
  memory or research.** For research/doc tasks especially: a version number
  or feature claim you read from a live source is different from one you're
  recalling — say which. A reviewer without live internet access (this
  happens) has to decide whether to spend time re-verifying your claims, and
  "I looked this up just now at <source>" vs. "this is what I recall" changes
  that decision.

Keep it honest and specific. This file is what gets reviewed — a vague or
overconfident report makes your work harder to approve, not easier.

### Knowledge extraction

**Do this before your step-6 commit, not after** (see the note added to
step 6) — `orchestrate knowledge durable-file` only writes to disk, it
doesn't commit. File first, then commit code + tests + the new
`KNOW-*.md` file together.

After writing the report, extract durable knowledge for future agents.
The goal: preserve understanding without preserving history. File through
the production writer — never hand-create `KNOW-*.md` files (the writer
allocates the globally-unique ID and validates the format; hand-written
files have caused real duplicate-ID collisions).

```bash
# Create a learning entry (ID + filename assigned automatically)
orchestrate knowledge durable-file --type learning \
  --title "One-line finding" --body "What we discovered, with evidence." \
  --tags "subsystem,technique" --task D-XXX --file path/to/module.py \
  --agent "<your agent name>"

# Types: project | architecture | decision | blocker | learning | task
# If your finding replaces an older entry, retire the old one instead of
# leaving two contradictory active entries:
orchestrate knowledge durable-supersede KNOW-OLD --with KNOW-NEW
# If you used an entry and it helped, say so (feeds future ranking):
orchestrate knowledge durable-useful KNOW-YYYYMMDD-NNN
```

### What to preserve

- [ ] **Architectural facts** — Component relationships, data flow, boundaries
- [ ] **Decisions** — Design choices and reasoning
- [ ] **Blockers** — Problems encountered that others might face
- [ ] **Failed approaches** — What didn't work and why
- [ ] **Verification results** — How to test this work
- [ ] **Dependencies** — Files, tasks, or systems this depends on
- [ ] **Affected files** — Files created/modified by this task

### What to discard

- [ ] Repetitive tool output
- [ ] Irrelevant command output
- [ ] Conversational filler
- [ ] Duplicated explanations
- [ ] Temporary reasoning
- [ ] Raw logs (unless evidence)

### Compression target

```
10,000 tokens of execution history
             ↓
500–1,500 tokens of durable knowledge
```

### Where to file

File with `orchestrate knowledge durable-file --type <category> ...` — the
writer places the entry in the right subdirectory and allocates the ID.
Do not invent filenames or IDs by hand.

| Category | `--type` |
|----------|----------|
| Architecture | `architecture` |
| Decisions | `decision` |
| Blockers | `blocker` |
| Learnings | `learning` |
| Task archive | `task` |

### Example: simple learning extraction

If you discovered something reusable (e.g., a gotcha, a working approach),
file it through the production writer:

```bash
orchestrate knowledge durable-file --type learning \
  --title "PyTorch must be lazily imported in tests" \
  --body "Importing torch at module top breaks the sandbox suite; lazy-import inside the function. Evidence: tests/test_vector_store.py." \
  --tags "pytorch,testing" --task D-XXX --file path/to/vector_store.py \
  --agent "<your agent name>"
```

### Simpler alternative

If a full knowledge entry is too much overhead, at minimum add a brief
section to your report that can be extracted later:

```markdown
## Knowledge Worth Preserving

**Finding:** [One sentence]
**Why it matters:** [One sentence]
**Evidence:** [File or test reference]
```

This makes knowledge extraction easier for whoever reviews and archives the task.

## 8. Submit for review — do not mark complete

```
orchestrate submit <TASK_ID> --notes "<one-line summary>" --report reports/<TASK_ID>_report.md --agent "<the exact same agent string you claimed with>"
```

This moves the task to **review**, not done, and keeps it locked to you.
The primary agent (or a human) will read your report and either:
- `approve` it → task becomes done.
- `reject` it with feedback → task goes back to open with the feedback in
  its notes, for you or someone else to pick up next round (see step 3).

You will not see the approve/reject happen in this session unless you're still
running when it does — that's fine. Move on to step 9.

**Post a short chat message right now**, even though the full report is
already on disk — this is the human's only cheap way to glance at progress
without opening files:

```
### ✅ <TASK_ID> submitted
<one-line summary — same as your --notes above>
```

## 9. Loop or stop

If you were told to keep going: go back to step 1 and find the next task.
If you were told to do N tasks: stop after N submissions.

If nothing is open and everything delegate-tier is claimed/in review, do
NOT drift into H/C-tier tasks and do NOT invent work. Instead, work
through the autonomous takeover check below before concluding there's
genuinely nothing to do.

### 9.5. Autonomous stale-task takeover (only when the open queue is empty)

This only applies when step 3 finds zero open delegate-tier tasks. If
open work exists, do that first — an idle claimed task is never higher
priority than real open work.

**Two time tiers — do not conflate them:**

- **12+ hours since last update, no open work available:** you may
  *investigate* a claimed/in_progress task as a takeover candidate.
  Investigating means gathering evidence (below) — it does not mean
  editing files or claiming yet.
- **48+ hours since last update:** this is the board's own `STALE`
  threshold (`orchestrate status` will show it, backed by
  `opencode_crack/orchestrator/task_board.py`'s `STALE_AFTER_HOURS`). At
  this point, if your investigation confirms abandonment, you may
  release and re-claim it yourself — check its notes/commit history for
  why it stalled first, and only proceed if you're confident it's
  genuinely abandoned.

Between 12 and 48 hours, investigate and document your findings in a
note on the task (or in your eventual report if you end up idle), but do
not release or re-claim — that gap exists so a slow-but-real session
isn't mistaken for an abandoned one.

**Evidence checklist — all of these, not just elapsed time, before
concluding a task is abandoned:**

1. `orchestrate status` — confirm the task's `Since` timestamp and
   whether it shows `STALE`.
2. `git log --all --since="<the task's claim/update time>"` — check
   whether *any* commits reference the task ID or touch files its scope
   would plausibly cover. Silence in git is stronger evidence than
   silence on the board alone (an agent could be mid-work with nothing
   pushed yet — that's normal, not abandonment).
3. Read the task's notes for anything explaining a legitimate pause
   (e.g. "blocked on X, will resume") — a documented wait is not
   abandonment.
4. Check whether the working tree (if you can see the same shared
   clone — not a different isolated worktree, which wouldn't show it
   at all) has uncommitted changes plausibly belonging to that task. If
   so, don't discard them — see step 3.5.2: verify the WIP, and if it's
   real and green, adopt and finish it rather than starting over.
5. If anything in 1–4 is ambiguous or contradicts abandonment, do not
   take over. Report what you found and stop — this is a STOP CONDITION,
   not a judgment call to push through.

**If all four checks support abandonment and the 48-hour threshold is
met:** release the task with a specific, evidence-citing note (state the
silence duration, what you checked, and that abandonment is verified
empirically rather than assumed) via `release --force` (you are not the
owner, so the ownership check needs the explicit override — it is
audit-logged), then re-claim it yourself and proceed via step 3.5.

**Never take over a task that's merely slow.** A task with recent
commits, recent notes, or under 12 hours of silence is not a candidate —
put it back on the shelf and either stop the session or, if told to keep
going, report that the queue is genuinely empty of actionable work right
now rather than forcing a takeover.

If nothing is open and no claimed task passes the takeover bar:
report that clearly instead of inventing work or drifting into H/C-tier
tasks.

**Before you actually stop** (end of session, out of eligible tasks, or hit a
STOP CONDITION), post one final chat message summarizing the whole session —
this is in addition to, not instead of, the per-task messages from step 8:

```
### Session summary — <N> task(s) submitted for review
- <TASK_ID> — <one-line outcome>
- <TASK_ID> — <one-line outcome>
...
Skipped: <TASK_ID> (<why, if any>)
Stopped because: <ran out of eligible work | hit a STOP CONDITION | told to stop>
```

Keep both of these short — a header and a few lines. The detailed version is
already in `reports/`; this is a scan, not a re-post of the same content.

---

## STOP CONDITIONS — report back instead of proceeding

- The task requires touching the Delegation rejection list.
- The task's scope is genuinely ambiguous and picking wrong would mean real
  rework (not just "you'd have to make a judgment call" — judgment calls are
  fine and expected).
- You hit something that looks like a security issue beyond what the task
  already describes (e.g. you're fixing logging and notice a hardcoded
  credential along the way) — note it in your report's Questions/blockers,
  don't fix it under a different task's scope.
- `claim` keeps failing on every open task you try (something's wrong with the
  board, not with task availability).
- Git reports conflicts, missing/corrupt objects, an unexpected branch state,
  or (in the shared/main checkout only — not inside your own isolated
  worktree, see step 0.75) uncommitted changes from another agent that
  cannot be cleanly separated.
- Repeated shell/tool/parser failures prevent reliable execution of the
  required protocol or verification. Do not bypass the failure by creating
  unrelated repository artifacts; report the blocker and the exact operation
  that could not be performed.
- A takeover candidate (step 9.5) has any ambiguous or contradicting
  evidence — recent commits, an explanation for the pause, or uncertain
  ownership of in-flight uncommitted work. Report findings and don't take
  over; this applies even under 48+ hours of silence if the evidence is
  mixed.
