---
name: summarizer
description: Produces summaries at multiple levels (headline to full brief) and types (executive, technical, plain-language, structured, extractive, comparative). Use when the user asks to summarize, condense, shorten, brief, or TL;DR any text, document set, or topic. Pairs with summary-selector, which picks the right type and level.
---

# Summarizer

Summarize at the level and in the shape the need calls for — never a one-size-fits-all paragraph.

## Instructions

### Step 1: Fix the target

- Accept the output of the summary-selector skill when it exists (it specifies type + level + why). Otherwise ask at most two questions: who is it for, and what decision will it inform. Never start summarizing an unclear target.
- Confirm the source boundary: exactly which text or files are covered. Name them in the delivered summary's header.

### Step 2: Read fully, then compress

- Read the entire source before writing a word. For sources longer than comfortably holdable, work hierarchically (section-by-section notes first, then compress the notes — the map-reduce pattern from long-document practice), and say that you did.
- Decide extractive vs abstractive per the type (see `references/summary-types.md`): quote when wording matters (decisions, commitments, definitions), rewrite when meaning matters (narrative, explanation).

### Step 3: Draft to the level

- Hit the level's size contract from the type catalog — a TL;DR that runs to a page is a failed TL;DR. Cut by removing detail first, then background, never the conclusion or the caveats.
- Preserve: numbers with units, names, dates, commitments, and anything the source hedges. A summary that drops the hedge ("might" → "will") is misinformation.
- Multi-source: summarize each source separately first, then synthesize — and keep per-source attribution so disagreements stay visible instead of averaged away.

### Step 4: Fidelity check before delivering

- Re-read the draft against the source asking: is every sentence supported? Is anything material missing for the stated purpose? Are hedges intact?
- Fix failures by re-reading, not by softening language. If the source is ambiguous, carry the ambiguity forward explicitly.

## Rules

- Source boundary in the header; no claims from outside it without labeling.
- Size contract of the chosen level is binding.
- Numbers, names, dates, commitments, hedges survive compression.
- Disagreements across sources are reported, never averaged.
- For the level/type catalog and technique notes, see `references/summary-types.md`. For choosing, see the summary-selector skill.
