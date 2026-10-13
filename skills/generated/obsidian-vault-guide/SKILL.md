---
name: obsidian-vault-guide
description: >
  Operate Justin's Obsidian vault (Fabian Phenix): where everything lives, how to open and use each tool (Graph view, Canvas, Bases, Copilot Agent Mode, KarpathyWiki ingest, Git sync), and the vault's organization rules. Use when the user asks about the vault, its plugins, its map/graph, where a note lives, or how to do anything in Obsidian.
allowed-tools: Read, Glob, Grep, Bash
---

# Obsidian Vault Guide (Fabian Phenix)

> Vault: `C:\Users\User\OneDrive\Documents\Fabian Phenix`
> Repo: `https://github.com/Justin-Lemonade/Obsidian-Vault---Fabian-Phenix` (private, branch `main`)
> Code project (separate, NOT a vault): `C:\Users\User\OneDrive\Documents\Projects\My AI Brain`

This is a general/main vault. The AI Brain is a section inside it
(`MOCs/AI Brain Home.md` + `01 Projects/My AI Brain.md` + `AI Brain Map.canvas`),
not its own vault. Never convert the code project folder into a vault;
link to it from `01 Projects/My AI Brain.md` instead.

## The three aspects (details in references/)

1. **Tools and how to use them** — `references/tools.md`.
   Graph view (global + local, groups, forces, time-lapse), Canvas,
   Bases (`.base` files), Backlinks, Tags, Calendar/Daily notes,
   Tasks + Day Planner, Copilot Chat + Agent Mode (the vault writer),
   KarpathyWiki ingest, Smart Connections, Omnisearch, Terminal,
   Git plugin, 3D Graph, Mind Map, Importer, and the small utilities.
2. **How to access things** — `references/access.md`.
   Ribbon icons, command palette (`Ctrl+P`), Quick switcher (`Ctrl+O`),
   exact Settings paths, disk paths for vault / `.obsidian` / plugins,
   git remote + branch, and key URLs.
3. **What is where and how it's organized** — `references/structure.md`.
   Full folder tree, what each folder is for, the MOC + link-back rules
   that make the graph cluster, inbox processing, and the git workflow.

## Golden rules (learned in this vault, don't relearn)

- **A plugin that "failed to load" almost always means its folder is missing
  `main.js`.** Every plugin dir needs `manifest.json` + `main.js`
  (`styles.css` optional). Skeleton folders (`manifest` only) come from
  interrupted BRAT/manual installs — uninstall, reinstall from Browse
  (stable), verify `main.js` landed, then enable. Never enable first.
- **One writer only.** Copilot Agent Mode is the approved vault writer
  (approve-each-change default). KarpathyWiki only ever *creates* under
  `wiki/` and never rewrites sources. Nothing else writes notes.
- **Every new note links back to its hub.** A note with no `[[link]]`
  is an orphan: invisible in the graph. Hubs: `[[AI Brain Home]]` for
  AI-Brain notes, area MOCs (e.g. `[[Islamic Banking Overview]]`) for
  area notes, section `[[... .base]]` notes for Notion-imported items.
- **Capture in `[[Quick Capture]]`, file weekly** into Projects / Areas /
  Resources. Inbox is a conveyor belt, not storage.
- **Git is source of truth, OneDrive is just disk.** The vault lives in
  OneDrive, which can fight `.git`. If conflicted copies appear, move the
  vault out of OneDrive. Commit + push via shell (`git add -A`,
  `git commit -m`, `git push`) or Obsidian Git commands; author email
  must stay `Justin-Lemonade@users.noreply.github.com` or GitHub
  rejects the push (GH007).
- **Vault writes go through `vault-transfer/transfer.ps1`.** The direct
  edit tool is blocked outside the code project, but the transfer script
  (FromVault/ToVault/Verify, multi-file per call, append-only
  `manifest.jsonl` log, 1:1 mirrored paths under `vault-transfer/vault/`)
  runs fine under this shell for both humans (direct copy) and agents
  (automatic git-plumbing fallback) — proven end-to-end 2026-10-09.
  Pull with FromVault, edit in the mirror, push back with ToVault,
  Verify MATCH, then commit + push. Never hand-place files in the mirror;
  the manifest is truth. Protected roots (`.obsidian` `.git` `.smart-env`
  `.copilot`) need `-Force`.

## Routine: verify vault health

1. `git status --short` in the vault — expect clean or only intentional edits.
2. `Glob .obsidian/plugins/*/main.js` — 16 folders should hit
   (all except `karpathywiki/`, which is pending reinstall).
3. Read `.obsidian/community-plugins.json` — expect the 17 enabled IDs.
4. `git log --oneline -3` — sanity-check history before pushing.
5. After any transfer: `transfer.ps1 -Mode Verify` reports MATCH and
   `manifest.jsonl` names what moved.
