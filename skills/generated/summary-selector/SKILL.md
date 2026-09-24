---
name: summary-selector
description: Decides which summary type and level fit a given need. Use before summarizing whenever the right shape is not obvious — when the user just says "summarize this" without saying for whom or for what. Hands the decision to the summarizer skill.
---

# Summary Selector

Pick the summary shape deliberately instead of defaulting to a generic paragraph.

## Instructions

### Step 1: Collect the four inputs

Ask (via the question tool, or infer when stated) only what is missing:

1. **Audience** — who reads it: decision-maker / builder / learner / general?
2. **Purpose** — what it informs: act / build / learn / triage / quote / compare?
3. **Source shape** — single or multi-source; short or long; harmonious or conflicting?
4. **Budget** — seconds, minutes, or study time?

### Step 2: Decide by matrix

- **Type** from audience × purpose: act→executive, build→technical, learn→plain-language, triage→headline-level of any type, quote→extractive, compare/harmonize→comparative (conflict) or structured (agreement), handoff→structured, history→narrative, versions→changelog-style.
- **Level** from budget × source length: seconds→headline/tldr, minutes→brief, study→full. Long sources never go below `brief` without a warning that detail was sacrificed.
- **Conflicts adjust:** multi-source with tension forces comparative regardless of other inputs; quotable commitments force an extractive pass even inside other types.

### Step 3: Hand off

Output exactly: chosen type + level, one-line why, the four inputs as recorded, and one fallback ("if too long, drop to X"). The summarizer skill takes it from there. If the user rejects the choice, re-run Step 2 with their correction as a constraint — do not argue for the matrix over the user.
