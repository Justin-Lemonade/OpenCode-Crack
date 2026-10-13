# Tools and How to Use Them

How to *open* anything below: command palette `Ctrl+P` → type the
quoted command, or see `access.md` for ribbon/Settings paths.

## Graph view (the important one)

- **What:** auto-generated map of the vault. Circles = notes (bigger =
  more backlinks), lines = `[[wikilinks]]`. Read-only: it shows the
  territory you already crossed; it never creates structure.
- **Open:** ribbon graph icon, or palette → `Open graph view`.
- **Use it for:** spotting clusters, gaps, and orphans; finding two
  topics that unexpectedly connect (e.g. Islamic Banking ↔ product
  ideas); maintenance checks after filing inbox notes.
- **Settings (cog, top-right):** Filters — Tags ON, Attachments ON,
  Orphans ON, Existing-files-only OFF. Display — Arrows OFF,
  Text fade −1.5, Node size ~0.5, Link thickness ~0.7. Forces —
  Center ~0.65, Repel ~12.4, Link force ~0.9, Link distance ~30
  (the "make my graph look better" recipe; full copy in
  `03 Resources/Graph Settings.md`).
- **Groups:** New group per area, e.g. `path:01 Projects` green,
  `path:Islamic Banking` blue, `path:MOCs` yellow — this is what makes
  the map *cool* instead of a hairball.
- **Bookmark** a good configuration so you can toggle back to it.
- **Local graph:** palette → `Open local graph` on the active note,
  depth 2. Use *while writing* for discoverability — it surfaces
  forgotten connections. Global graph is for review, local is for work.

## Canvas (`AI Brain Map.canvas`, vault root)

- **What:** intentional thinking space — you decide what goes where.
  The opposite of Graph view: Canvas is where you decide where to walk.
- **Use for:** mind maps, brainstorming, project planning, knowledge
  synthesis (pull key notes + quotes onto one board).
- **How:** drag notes/cards onto the board; hover a card edge until the
  dot appears, drag dot → another card to draw a labeled edge.
  Current board: 5 cards (home / project / banking / inbox / settings),
  4 labeled edges. Keep it prunable — a canvas is a workspace, not storage.
- **Rule:** durable record lives in notes; the canvas holds the *arrangement*.

## Bases (`.base` files — Tables of the Notion import)

- **What:** database views over notes (core plugin `bases`,
  `Notion/*/Tasks.base`, `Meetings.base`, `Notes.base`, `Docs.base`,
  `Furuz Learning.base`, `Freiker Project.base`).
- **Use:** open the `.base` for the section you work in (e.g.
  `Notion/Tasks/Tasks.base`); every imported note carries
  `base: "[[...base]]"` in frontmatter to belong to its view.
- New notes in an imported section must carry the same `base:` line.

## Backlinks / Outgoing links / Tags

- **Backlinks panel:** everything pointing *at* the current note —
  check it before creating a "new" note on a topic (the note may exist).
- **Outgoing links:** what the current note points to.
- **Tags:** status/type only (`#ai-brain`, `#project`, `#inbox`,
  `#area`), never duplicates of folders. Tags cut *across* folders;
  folders say where a note lives, `[[links]]` say what it relates to.

## Calendar + Daily notes

- Calendar gives a visual date navigator; daily notes are the
  chronological layer, separate from topical organization. Daily note =
  front door for captures that later get filed.

## Tasks + Day Planner

- **Tasks:** `Track tasks across your vault` — due/recurring/done dates,
  checklist filters. Query tasks anywhere with code blocks.
- **Day Planner:** turns Tasks + daily-note + calendar items into time
  blocks on an editable timeline with time tracker.
- Flow: capture task in daily note → Tasks tracks it → Day Planner
  schedules it.

## Copilot — the vault writer (v4.0.13)

- **Two modes.** Quick Chat (Chat / Vault QA / Projects): conversation,
  vault-grounded Q&A, "write or update your notes for me".
  **Agent Mode** (ribbon Agent icon or `Open Copilot Agent Chat Window`):
  runs OpenCode, Claude Code, or Codex *scoped to this vault* for
  multi-step work — research, write, organize. Every create/rename/
  modify/delete waits for your approve/reject by default.
- **Setup:** Settings → Copilot → Models tab → add one provider key
  (OpenRouter default; Ollama/LM Studio for fully local) → pick default
  model. Agent Mode → choose OpenCode (Copilot can download it), or
  Claude/Codex with their CLIs. Single-agent sessions need no Copilot
  license, just the agent's own account/key.
- **Rule:** this is the *only* plugin allowed to write notes
  autonomously, and only with per-change approval.

## KarpathyWiki — bulk knowledge builder (v1.28.0, PENDING reinstall)

- **What:** turns notes + PDFs into a linked LLM wiki: entity pages
  (people, orgs, products) + concept pages (theories, methods) under
  `wiki/`, with aliases, merges, contradiction flags, Dataview-ready
  frontmatter. Graph view renders it with zero extra setup.
- **Flow:** install from Browse → Settings → LLM Configuration →
  pick provider (Anthropic/OpenAI/Bedrock read PDFs natively; other
  endpoints need Force PDF Support) → Ingest ONE throwaway note →
  then `Ingest from folder` on real captures.
- **Depth/cost dial:** granularity Minimal → Fine (+ Custom).
  `reviewed: true` pages are protected from overwrite.
- **Constraint:** never rewrites sources, only creates under `wiki/`.
- Headless twin (optional): `npm i -g karpathywiki-cli`.

## Smart Connections

- Zero-setup local embeddings; related notes surface as you write;
  semantic lookup across the vault. First run downloads models
  (needs network + GBs free) into `.smart-env/` — committed to git,
  expect large diffs after re-indexes.

## Omnisearch

- The search engine. Full-text with ranking — fastest retrieval across
  the 30+ note vault. Use before creating any note.

## Terminal (v3.28.0)

- Consoles/shells inside Obsidian. Windows needs Python 3.9+ plus
  `python -m pip install --upgrade "psutil>=5.9.5" "pywinctl>=0.0.50" "typing_extensions>=4.7.1"`,
  then Settings → Terminal → Recheck Python, or terminals fall back
  from ConPTY to ConHost.

## Git (v2.41.1)

- Palette: `Git: Commit`, `Git: Push`, `Git: Pull`. Settings: Pull on
  startup ON, Push after commit ON, auto-backup ~30 min. Auth via
  Windows credential manager / PAT. Never commit with a private email.

## 3D Graph / Mind Map / Image Converter / Enhancing Export / Importer / Recent Files / Agent Client / BRAT

- **3D Graph:** alternate spatial graph view for wow-factor walkthroughs.
- **Mind Map:** previews any note as a Markmap mind map.
- **Image Converter:** convert/compress/resize/annotate WEBP-JPG-PNG-HEIC-TIF,
  drag-resize, batch.
- **Enhancing Export:** note → HTML/DOCX/ePub/PDF/Hugo Markdown.
- **Importer:** one-shot Notion/Evernote/OneNote/etc. imports (already used).
- **Recent Files:** most-recently-opened list.
- **Agent Client (ACP):** chat with external agents; not a vault writer.
- **BRAT:** installs/updates beta plugins from GitHub (`Add a beta plugin
  for testing`). After any BRAT install, verify `main.js` landed before
  enabling.
