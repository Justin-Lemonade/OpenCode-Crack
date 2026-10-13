# What Is Where and How It's Organized

## System: PARA-lite + MOCs + Bases

- **Folders = where a note lives** (exactly one home, by actionability).
  **Links = what it relates to** (ignore folder boundaries).
  **Tags = status/type** (`#ai-brain`, `#project`, `#inbox`, `#area`, `#moc`).
- **MOCs (Maps of Content)** are hand-curated hub pages — the entry points
  humans (and agents) start from. The graph is automatic; MOCs are intentional.

## Current tree (vault root → purpose)

- `MOCs/AI Brain Home.md` — THE hub. Core links (`My AI Brain`,
  `Islamic Banking Overview`, `Quick Capture`), Project log, map-layer
  explainer. Pin this tab.
- `AI Brain Map.canvas` — the visual map: 5 cards
  (home / project / banking / inbox / settings), 4 labeled edges.
- `00 Inbox/Quick Capture.md` — 5-second capture dump. Conveyor belt:
  file weekly into Projects/Areas/Resources, never storage.
- `01 Projects/My AI Brain.md` — hub for the code project. Points at the
  real folder, `data/intake/`, `tools/get-harper.py`; Progress section
  links work summaries (e.g. `[[Work Summary 7-10-2026]]`).
- `02 Areas/` — reserved (ongoing responsibilities; none yet — Islamic
  Banking predates the numbering and stays where it is).
- `Islamic Banking/Overview.md` — area MOC. New banking notes
  (`[[Sukuk]]`, `[[Murabaha]]`, …) must link back here so they cluster.
- `03 Resources/Graph Settings.md` — the cool-graph recipe (Reddit
  forces + Groups scheme) and plugin watchlist.
- `04 Archive/` — reserved (done projects, dormant areas).
- `Notion/` — one-shot Importer output, kept as-is: `Tasks/`, `Notes/`,
  `Meetings/`, `Docs/`, `Daily Furuz Product Brainstorm.md`,
  `Furuz Learning/` (incl. `Justin's Learning Plan & Execution/`),
  `Freiker Project/`. Each note carries `base: "[[<Section>.base]]"`;
  each section has a `.base` view (`Tasks.base`, …). New notes in these
  sections copy the same `base:` line.
- `Work Summary 7-10-2026.md` (root) — session log pattern: backlink
  header (`Part of [[AI Brain Home]] and [[My AI Brain]]`) + `#work-summary`
  tag. Future summaries follow this shape.
- Root attachments (unfiled, fine for now): `Justin's Learning Plan &
  Execution.jpeg`, `Learning Plan for Justin - V2.2 Revised.docx`,
  `SWOT Analysis guide.webp`.
- `wiki/` — reserved for KarpathyWiki output (`entities/` + `concepts/`
  appear after first ingest). Never hand-edit generated pages unless
  marking `reviewed: true`.
- `.smart-env/` — Smart Connections embeddings (large, noisy diffs normal).
- `.obsidian/` — config + 17 plugins (see `community-plugins.json`).

## Rules that keep the map beautiful

1. One home per note; link freely across folders.
2. New note → link back to its hub in the same edit (orphans are invisible).
3. Captures start in Quick Capture; weekly review files or deletes them.
4. Sources are never rewritten by AI tools — only appended/linked
   (KarpathyWiki creates under `wiki/`, Copilot edits need approval).
5. Big binaries stay out of notes when avoidable (repo bloat);
   root attachments are grandfathered.

## Agent transfer workflow (`vault-transfer/` in the code project)

1. `transfer.ps1 -Mode FromVault -Files "<rel>"…` — vault → mirror (source kept).
2. Edit freely under `vault-transfer/vault/` (mirrored tree = vault tree).
3. `transfer.ps1 -Mode ToVault -Files "<rel>"… [-CommitMessage "…"]` — mirror → vault.
4. `transfer.ps1 -Mode Verify` — both sides re-hashed, expect MATCH.
5. Commit + push the vault repo (transfer commits nothing unless asked).
Every run appends to `manifest.jsonl` (time, direction, path, sha256, engine),
so nothing loses its place. Multi-file per call; protected roots need `-Force`.

## Git workflow for the vault

- `git add -A` → `git commit -m "<scope>: <what>"` → `git push`
  (tracking set; author email must be the noreply address).
- History was squashed to single commits twice (email-privacy GH007,
  clean re-push) — squashing local-only history is acceptable;
  never rewrite pushed history.
- **Still missing: `.gitignore`** in the vault root. Create it
  (`notepad .gitignore` from vault root) with:
  `.obsidian/workspace.json`, `.obsidian/cache/`, `.obsidian/hotkeys.json`,
  `.DS_Store` — then commit + push. Without it, UI churn
  (`workspace.json` etc.) pollutes every commit.
- After any plugin install/update/enable spree: verify
  (`*/main.js` present, enabled list sane), then commit + push so the
  repo matches the working vault.
