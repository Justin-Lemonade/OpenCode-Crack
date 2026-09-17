# Raise a Concern Protocol

You noticed something wrong with the *project* — not just with the task
you're on — and it's worth someone's attention beyond your own task
report. This file is how you file it without stopping to ask what to do.

If you're not sure whether what you found is a "concern" or just
something to mention in your task report, ask: would this still matter
if the specific task you're on were deleted? If yes, it's a concern.

## Steps

1. **Check it isn't already filed.** Run
   `orchestrate concern list` and skim titles/related tasks for anything
   covering the same issue. If one exists and is still
   `open`/`acknowledged`, don't file a duplicate — if you have new
   evidence, that's worth adding, but there's no "comment" action by
   design (keeps the record append-only and simple); mention it in your
   task report instead and let whoever resolves the existing concern pick
   it up.

2. **Pick a severity honestly.** Don't inflate to get attention faster —
   an inbox where everything is `blocker` is as useless as one where
   nothing is. Don't downplay either — if it's actively producing wrong
   output or wasted work right now, that's `blocker`, not `medium`.

3. **Write a concern that stands alone.** Someone reading only this file,
   with no other context, should understand: what you saw, why it
   matters, and (if you checked) what you already ruled out. Reference
   specific files/commits/task IDs — "the board is wrong" is not
   actionable; "task D-259 shows `done` but the artifact it was supposed
   to produce doesn't exist in the repo at HEAD" is.

4. **File it:**
   ```
   orchestrate concern file \
     --title "Short, specific summary" \
     --body "Full write-up per step 3." \
     --severity <blocker|high|medium|low> \
     --category <process|board-integrity|technical|environment|quality|other> \
     --raised-by "<your agent name>" \
     --related-task <ID>   # omit if not about one specific task
   ```
   In a git-backed deployment this may pull, write, commit, and push
   immediately — same as `orchestrate claim`. If it fails with a git
   error, resolve that first (don't lose the write-up — save it
   somewhere and retry) rather than abandoning the concern.

5. **Say so in chat, briefly.** One line: "Filed CN-### (severity) —
   short title." Don't paste the full body back into chat; it's on disk
   and (if wired up) on a dashboard.

6. **Go back to your actual task.** Filing a concern doesn't mean stop
   and wait for a response — keep working per whatever protocol brought
   you here (`find_work.md`, a direct assignment, etc.), unless the
   concern itself makes continuing unsafe or pointless (e.g. you found
   the task's spec is built on a false premise — then `block` the task
   too, with a note pointing at the concern ID).

## If you're the primary agent or a human clearing the board

Run `orchestrate concern list --status open` (and `--status acknowledged`
for ones already in progress) periodically — treat this the same way
`review_work.md` treats the review queue: don't let it silently grow.
For each:

- Read it, verify against actual code/board state (don't just trust the
  narrative — same "verify, don't just trust the report" principle
  `review_work.md` uses for task submissions).
- `acknowledge` if you're on it but not done yet, or go straight to
  `resolve --notes "..."` / `dismiss --notes "..."` once you've actually
  acted or decided.
- If resolving reveals a concrete follow-up task, create it the normal
  way per your project's task-definition convention (see `AGENTS.md`)
  and reference the concern ID in the task's origin/context field — the
  concern closes once understood/actioned; the task tracks the actual
  fix.
