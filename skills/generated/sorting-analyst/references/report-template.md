# Sorting Report Template

Use these exact sections, in this order. Keep the report concise — compress aggressively, link instead of repeating.

## Project understanding

What the project appears to be and how its current structure works. One short paragraph. If inferred from files rather than docs, say so.

## Current organization

What is already organized appropriately. Name the conventions that exist and are working, so they are preserved rather than accidentally "fixed."

## Recommended organization

The proposed structure as a tree or list. If no changes are warranted, state exactly that: **no organizational changes are recommended** — and skip to Safety confirmation.

## Proposed changes

One entry per recommended move or rename:

- **Item:** current path
- **Proposal:** move/rename to proposed path
- **Reason:** one line
- **Confidence:** high / medium / low
- **Risks:** dependencies, references, or unknowns that moving would affect

Group entries by confidence (high first). Do not list purely aesthetic moves.

## Items requiring investigation

Anything ambiguous, potentially important, or risky: what it is, why it is unclear, and what would need checking before any recommendation could be made. Potential duplicates and potentially obsolete/generated material go here unless evidence is strong.

## Safety confirmation

Close with this statement, verbatim in meaning: **no files or folders were created, deleted, moved, renamed, or edited during this analysis.** All changes above are recommendations only.
