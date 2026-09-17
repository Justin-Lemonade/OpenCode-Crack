# Review Work Protocol

This is the primary agent's side of the loop — what to do when tasks pile
up in `review`. Written so a future session (or a different agent
instance) can run this without having lived through how it was learned.
If you're picking this up cold: read this whole file before touching
anything.

**Concerns:** this pass is also the right time to clear the concerns
board (`orchestrate concern list --status open`, and `--status
acknowledged` for ones already in progress) — same "don't let it
silently grow" principle as the review queue itself. See
[`raise_concern.md`](raise_concern.md) for the lifecycle
(acknowledge/resolve/dismiss/reopen).

This has two halves that happen in the same pass: **(A) clear the review
queue** — verify and approve/reject each submitted task — and **(B) mine
that same reading for prompt problems** and fix `find_work.md`. Don't
treat these as separate sessions; the review IS the raw material for the
prompt fix, and doing them together is what keeps this loop actually
self-improving instead of two disconnected chores.

---

## Part A — Clearing the review queue

### A1. Sync and survey

```
git pull --rebase
orchestrate list --status review
```

Also check for anything that resolved itself outside the normal flow
(e.g. `blocked` with a report attached) — read those reports too even
though they're not in `review`, since they're still real work product
worth learning from.

### A2. Establish a baseline

```
pytest tests/ -q
```

If your project has known-heavy or environment-dependent test modules
(e.g. something needing a GPU, a live external service, or a large
model download), adjust with `--ignore` as appropriate and record why in
your notes — don't silently skip tests without saying so.

Zero failures here is your strongest single signal that the batch is
broadly sound before you've read a single report — it means every task
that touched code left the suite green. It is NOT sufficient on its own:
a task can be low-quality, imprecise, or scope-creeping while still
passing tests. Treat it as a floor, not a verdict.

### A3. For each task in review, verify — don't just read

Read the report first, then check its claims against reality. How much
verification effort a task deserves scales with **risk × confidence you
can actually check it**, not with priority label alone:

- **Read-only recon/analysis tasks** (no files changed): spot-check the
  1–2 most consequential or most surprising claims against the actual
  repo (`orchestrate search "<term>"` to find where else it's discussed
  faster than hand-grepping, read the actual file, run the actual code
  path if you can). You don't need to re-verify every line — you need to
  verify the load-bearing claim, the one other tasks or decisions will
  build on.
- **Tasks that added tests**: confirm the test file exists, confirm the
  test count matches what the report claims, confirm it's part of a
  passing suite run (A2 already gave you this for anything already
  merged/pushed).
- **Tasks that changed production code**: read the actual diff, not just
  the report's description of it. Check it against the task's stated
  scope — did it stay inside, or drift? Run the specific affected tests
  directly, not just trust the full-suite pass, when you can isolate them.
- **Claims you can empirically test** (a compatibility break, a config
  behavior, an API signature): actually run it. Don't accept "X breaks
  Y" without either reproducing the break or reading the relevant source
  closely enough to confirm the mechanism. This is where you catch
  imprecise-but-not-wrong findings — a report that says a dependency
  "rejects" a call when the real behavior is that it silently ignores
  unrecognized keyword arguments is a materially different (and often
  worse) bug than the word "rejects" implies.
- **Claims you cannot verify** (external version numbers, live doc
  research, anything needing tools/access you don't have): say so
  explicitly in your approval notes rather than silently treating them
  as confirmed. "Not independently fact-checked" is a legitimate
  approval note — it's honest about what review actually covered.

### A4. Decide: approve or reject

- **Approve** when the work is real, roughly matches its report, and
  either passes verification or is low-enough-risk that the verification
  effort in A3 was proportionate and clean. Write approval notes that
  state HOW you verified it, not just "looks good" — that note is what
  makes the review meaningful to anyone reading the activity log or
  status dashboard later, including a future you.
- **Reject** (with concrete, actionable notes — required by the tool)
  when: the work doesn't match the report, tests fail, scope crept
  somewhere it shouldn't have, or a claim you checked turned out to be
  wrong in a way that matters. A reject that just says "not good enough"
  is as useless as a report with no content — say specifically what
  needs to change.
- **Correct-and-approve** is also legitimate: if the work is real but
  imprecisely described, approve it but put the correction in your
  notes. Don't reject good work over wording — but don't let the
  imprecision go unrecorded either, since that's exactly the signal
  that feeds Part B.

```
orchestrate approve <TASK_ID> --notes "<what you verified and how>"
orchestrate reject <TASK_ID> --notes "<specifically what's wrong>"
```

### A4b. Durable knowledge check (Layer 2: `KNOW-*` entries)

When the report references a durable entry it created (via `knowledge
durable-file`) or claims to have used:

- Read it: `orchestrate knowledge durable-show <KNOW-ID>`. Verify its
  claims the same way you verify any other report claim — a knowledge
  entry is not verified just because it exists.
- If the entry's finding checked out and it supersedes an older one, make
  sure the old one was actually retired (`durable-supersede OLD --with
  NEW`); two contradictory active entries are a defect, flag it.
- If an entry is wrong, retract it (`durable-retract <ID> --notes "why"`)
  rather than leaving it searchable.
- If an entry genuinely helped the work, mark it (`durable-useful
  <ID>`) — that vote feeds future ranking.

Scope note: the check above covers Layer 2 (`KNOW-*`) durable entries
only. See A4c below for Layer 1 (`K-###`) Knowledge Board entries —
don't confuse the two.

### A4c. Knowledge Board check (Layer 1: `K-###` entries)

When a submitted report references a `K-###` it filed via the optional
"Reusable discovery" report step:

- Verify the claim the same way you verify any other report claim —
  read the entry (`orchestrate knowledge show <K-ID>`), don't
  just take the report's word for it.
- If the filer already self-verified it (`agent-verified`), and it
  checks out, run `orchestrate knowledge verify-reviewer <K-ID>`.
- If it's still `unverified` and you don't have time to fully vet it
  this pass, leave it `unverified` rather than rubber-stamping it — a
  later review can pick it up.
- Only `reviewer-verified`/`confirmed` entries are eligible for
  automatic contract injection (`RELEVANT PRIOR KNOWLEDGE`), so an
  entry stuck at `unverified` is invisible to future agents until
  someone verifies it — that's by design, not a bug to route around.

### A5. Confirm the queue is actually clear

```
orchestrate list --status review
pytest tests/ -q
```
Re-run the full suite once more at the end — approvals themselves don't
change code, but if you rejected anything or made corrections along the
way, confirm nothing regressed.

---

## Part B — Mining the review for prompt problems

Do this using what you just read in Part A — don't re-read everything a
second time. As you verify each task, keep a running scratch list of
anything that made verification harder than it should have been, or
anything a report got wrong, overstated, or left ambiguous. At the end,
sort that list into patterns:

- **Did agents pick the right work?** Look at the actual order/spread of
  what got claimed — is there a visible reason, or does it look like
  "everything that was open"? If the latter, `find_work.md`'s selection
  criteria (or the agents' adherence to them) needs work.
- **Did reports let you verify quickly, or did you have to dig?** A
  report that states exact file paths, exact test counts, and exact git
  state (pushed/committed/uncommitted) lets you verify in under a
  minute. A report that's vague on any of those costs you real time —
  that's a concrete, fixable prompt gap.
- **Was anything described more dramatically (or more mildly) than what
  you found when you checked?** Look for this specifically — it's common
  and easy to miss if you're skimming reports rather than verifying
  them. A verb that implies a hard failure ("rejects", "crashes") when
  the real behavior is a silent, softer one ("ignores", "falls back")
  is a materially different bug and worth correcting even when the
  underlying finding is otherwise sound.
- **Did any report mix verified and unverified claims without saying
  which was which?** Common in research/recon tasks. If you found
  yourself unsure whether to trust a specific number or fact, that's the
  gap.
- **Did agents respect concurrency and scope correctly?** Good signs:
  explicit mentions of avoiding another agent's in-flight files, explicit
  "this is out of my scope, flagging for review" notes. These are worth
  reinforcing in the prompt (say what's working, not just what isn't)
  so they don't erode over future iterations.
- **Did any task's report contradict the actual repo state?** (E.g.
  "no push" when it was, in fact, pushed — possibly because the report
  was written before a later push happened, or a concurrent agent pushed
  the shared working tree. Not necessarily dishonest, but still a defect
  worth catching and a reason to add a "state your ACTUAL git state,
  checked at write-time" rule if it recurs.) Check a sample against
  `git log`, not just the report's own words about itself.

### Turn patterns into prompt changes

For each real pattern (not a one-off — one weird report isn't a pattern,
three similar ones are):

1. State the problem in one line.
2. State the smallest concrete addition to `find_work.md` that would
   have caught or prevented it — a new report field, a new rule, a
   clarified instruction. Prefer editing an existing step over adding a
   new one; only add a genuinely new step when the fix doesn't belong
   anywhere that already exists.
3. Add it to `find_work.md`'s own Changelog with the date and the reason
   — not just "added X" but "added X after Y happened." Future-you (or a
   future different-model agent) needs the reasoning, not just the rule,
   to apply it well in cases the rule doesn't literally cover.
4. Commit and push `find_work.md` (and `AGENTS.md` if the change is
   general enough to belong there too) before ending the session — the
   next agent that runs `find_work.md` should see the improved version
   immediately, not next time someone remembers to push it.

Don't over-edit. A prompt that grows a new paragraph after every single
task is unreadable within a few iterations. Bar for a change: it fixes
something that actually happened and is likely to happen again, not
something that merely could theoretically go wrong.
