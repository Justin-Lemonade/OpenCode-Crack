# Summary Levels, Types, and Techniques

## Levels (compression contracts — binding)

| Level | Size | Use when |
|---|---|---|
| `headline` | 1 sentence, ≤25 words | Triage: is this worth my time |
| `tldr` | 2-4 sentences or ≤5 bullets | 30-second catch-up |
| `brief` | One screen (~150-250 words), structured | Meeting prep, handoff |
| `full` | Multi-section, length fits the source (typically 5-15% of source) | Study, reference, decision record |

Always deliver exactly one level per summary. If the user needs two (e.g. TL;DR + full), stack them headline-first so each deeper level is optional.

## Types

- **executive** — conclusions, decisions needed, risks; background minimized. For someone who acts on it.
- **technical** — methods, mechanisms, specs, edge cases preserved; jargon kept. For someone who builds on it.
- **plain-language** — same content as technical but jargon translated on first use. For a smart non-specialist (pairs with learning-assistant's assessed level).
- **structured** — fixed schema: purpose / key points / decisions / open questions / next actions. For working documents and handoffs.
- **extractive** — direct quotes only, each cited, minimal connective tissue. For commitments, requirements, anything quotable.
- **comparative** — per-source positions first, then agreements vs disagreements. For multi-source sets with tension.
- **narrative** — what happened, in order, cause and effect intact. For histories, incidents, evolutions.
- **changelog-style** — what changed between versions/states, grouped by impact. For diffs and updates.

## Techniques

- **Extractive vs abstractive:** quote when the exact wording carries weight (commitments, definitions, criteria); rewrite when the meaning carries weight (explanations, narratives). Mixing is normal — label quotes as quotes.
- **Hierarchical (map-reduce) for long sources:** notes per section → compress notes → final. Never summarize from a partial read without saying so.
- **Lead-bias guard:** introductions overstate and conclusions hide — check the middle of every source, not just the opening.
- **Fidelity list:** numbers+units, names, dates, commitments, hedges, and explicit unknowns survive every level, including `headline`.
