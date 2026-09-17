# Durable Knowledge Audit Protocol

A reusable prompt for periodically reviewing every entry in the
knowledge base (`knowledge/K-*.md` legacy format and
`knowledge/**/KNOW-*.md` current format) for accuracy, hygiene, and
whether it should exist at all.

---

## Prompt (give this to the auditing agent)

```
You are auditing the durable knowledge base at knowledge/ against
current `main`. Goal: approve, correct, merge, or retire every entry.
This is a judgment task, not a mechanical one — read each entry's
actual claim and check it, don't skim.

### 0. Inventory first

    find knowledge -name "K-*.md" -o -name "KNOW-*.md" | sort

Two formats coexist:
- Legacy `K-###_slug.md` — flat, `**Status:** unverified` field, no
  frontmatter, no supersede mechanism.
- Current `KNOW-YYYYMMDD-NNN-slug.md` — YAML frontmatter (id, type,
  status, confidence, files, related, supersedes, task_ids, etc.),
  filed via `orchestrate knowledge durable-file`, retired via
  `orchestrate knowledge durable-supersede`.

Note the total count before starting. You will account for every
entry at the end — none silently skipped.

### 1. Cheap hygiene pass first (before deep-reading each one)

Catches the highest-value findings for the least effort:

- **Exact/near-duplicates**: same title or near-identical body text
  filed under different IDs (grep title lines, diff suspicious pairs
  directly). Legacy K-* entries are especially prone to this — they
  have no supersede mechanism, so re-filing was the only way to
  "update" one.
- **Same claim, different status**: e.g. two entries describing the
  same bug, one still says unresolved, one implies fixed — which is
  current?
- **Orphaned status**: every legacy K-* entry likely still says
  `unverified` since filing — that field existing at all means nobody
  has closed the loop. Your job this pass is to close it.

### 2. Per-entry review (the actual audit)

For EVERY remaining entry, do all of the following before deciding:

a. **Read the actual claim.** What specific, falsifiable fact or
   decision does this entry assert? (Not the title — the body.)

b. **Verify against current `main`, not memory.** If it names a file,
   function, config value, or behavior — grep/view it and confirm the
   claim still holds. If it's a KNOW-* entry with a `files:` field,
   check every listed file. If it references a task ID, check that
   task's current status/report. Do not approve on the strength of
   the entry reading plausibly — approve because you checked.

c. **Check for staleness relative to OTHER entries or docs**, not just
   internal self-consistency. An entry can be 100% accurate the day it
   was filed and still be incomplete today because something later
   changed the picture (e.g. a workflow rule the entry describes was
   later relaxed or extended elsewhere). Cross-reference `related:`
   fields, and grep the entry's key terms across `knowledge/`,
   `.agent_prompts/`, and `docs/` for anything that supersedes it in
   substance without saying so formally.

d. **Judge whether it should exist at all**, independent of accuracy:
   - Is it durable knowledge (survives past this task) or should it
     have just stayed in a task report?
   - Is it too narrow/low-value to be worth a future agent's read time
     (padding, not signal)?
   - Is it in the right category/type (architecture vs decision vs
     learning vs blocker vs project vs task)?

e. **Classify the entry** as exactly one of:
   - **APPROVE** — accurate, current, correctly scoped and typed. No
     action.
   - **CORRECT** — the core finding is valid but some detail is wrong
     or outdated. File a corrected replacement, supersede the old one
     (KNOW-*) or edit-and-note (K-* has no supersede mechanism — see
     step 3).
   - **MERGE** — a duplicate or near-duplicate of another entry. Keep
     the better-written/more complete one, retire the other.
   - **RETIRE** — was true, no longer is, and isn't corrected by a
     newer entry yet — mark obsolete rather than leaving it live and
     misleading.
   - **REJECT** — should not have been durable knowledge in the first
     place (too narrow, belongs in a task report, wrong type). Retire
     it; don't "fix" something that shouldn't exist.

f. **Record your verification method per entry** — "confirmed via
   grep on <file>", "confirmed task <ID> report", "read code directly
   at <path>:<line>", or "could not verify — flagging for human/live
   confirmation" if you genuinely can't check it (e.g. it depends on
   an external system state you can't see from here). Never mark
   something APPROVE on an assumption.

### 3. Taking action

- **KNOW-* entries**: never hand-edit the file. Use
  `orchestrate knowledge durable-supersede KNOW-OLD --with KNOW-NEW`
  for CORRECT/MERGE/RETIRE — file the replacement (or a short retiring
  note) via `orchestrate knowledge durable-file` first, then supersede.
  This preserves history instead of destroying it.
- **K-* legacy entries**: no supersede mechanism exists for this
  format. Do not silently edit them either — instead:
  1. If genuinely still useful, re-file as a proper KNOW-* entry via
     `orchestrate knowledge durable-file` (this also fixes the
     "unverified" status problem — set status to what you actually
     verified) and note in the new entry that it replaces the K-*
     original.
  2. If a duplicate, keep one and note the retirement of the other
     directly in this audit's report (don't touch the file), since
     there's no formal retirement flow for this format.
  3. If no longer relevant, note it as rejected in this audit's report
     and leave the file as-is — don't delete durable records.
  Flag the missing K-* retirement mechanism itself as a process gap in
  your final report rather than working around it silently.

### 4. Deliverable

Produce a table, one row per entry, every entry accounted for:

| ID | Type/Format | Claim (one line) | Verification method | Verdict | Action taken |

Then a short summary:
- Counts by verdict.
- Any process gaps hit along the way (e.g. "K-* has no supersede
  mechanism" is a known one — note if you hit others).
- Any entries you could NOT verify and why — hand these back rather
  than guessing.

Do not silently skip an entry because it looked fine at a glance —
every row needs an actual verification method, not "looks right."
```

---

## Notes for whoever runs this

- This is a full-repo read task, not a quick check — budget for it
  accordingly. Scale the session or split into batches once the
  knowledge base grows large enough that reading every entry no longer
  fits comfortably in one pass.
- Known process gap: legacy `K-*.md` has no supersede/retirement
  mechanism the way `KNOW-*.md` does via `durable-supersede`. The
  prompt above works around this by having the auditor note retirements
  in its own report rather than editing K-* files directly. Migrating
  any still-useful K-* entries to the KNOW-* format as part of running
  this audit removes the need for the workaround on future runs.
