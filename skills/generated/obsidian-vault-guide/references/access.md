# How to Access Things

## Inside Obsidian (UI)

- **Ribbon (left bar):** Graph view (forked icon), Canvas (new canvas),
  Copilot Agent (agent icon), BRAT (after enable), Quick capture flows.
- **Command palette `Ctrl+P`:** every command in `tools.md` lives here —
  type `Git:`, `Copilot`, `Ingest`, `BRAT:`, `Terminal:`, `Mind Map:`.
- **Quick switcher `Ctrl+O`:** jump to any note by name (faster than folders).
- **Search `Ctrl+Shift+F`:** vault-wide text search (Omnisearch improves ranking).
- **Right-click a tab:** Pin (pin `MOCs/AI Brain Home.md` — it's the entry point).
- **Hotkeys:** no default hotkey for Graph/Canvas — open via ribbon or
  palette; custom bindings under Settings → Hotkeys.
- **Settings paths you will actually open:**
  - Settings → Community plugins → Browse / Check for updates / per-plugin toggles
  - Settings → Git → pull-on-startup, push-after-commit, backup interval
  - Settings → Copilot → Models (keys), Basic → Agents, Agent Mode
  - Settings → KarpathyWiki → LLM Configuration → Advanced (Force PDF Support)
  - Settings → Terminal → Recheck Python
  - Settings → Core plugins → enable Bases, Canvas, Graph, Backlinks, Daily notes
  - Graph view → cog icon → Filters / Groups / Display / Forces

## On disk (Windows paths)

- **Vault root:** `C:\Users\User\OneDrive\Documents\Fabian Phenix`
- **Config:** `<vault>\.obsidian\` — `community-plugins.json` (enabled IDs),
  `core-plugins.json`, `graph.json`, `workspace.json`, `app.json`
- **Plugins:** `<vault>\.obsidian\plugins\<id>\` — each needs
  `manifest.json` + `main.js` (`styles.css`, `data.json` optional)
- **Smart Connections index:** `<vault>\.smart-env\`
- **Code project (NOT a vault):**
  `C:\Users\User\OneDrive\Documents\Projects\My AI Brain`
  (`data/intake/`, `tools/`, `skills/generated/` ← this skill lives here)
- **Full checkout:** `C:\Projects\My-AI-Brain-clean` — skill installed at
  `skills/generated/obsidian-vault-guide/` (commit `14aff651`);
  re-transfer the folder after editing the skill source.
- **Transfer airlock:** `Projects\My AI Brain\vault-transfer\`
  (`transfer.ps1`, `vault/` 1:1 mirror, `manifest.jsonl`). All vault
  writes flow through it — see SKILL.md golden rules and
  `vault-transfer/README.md` for exact commands.

## Git + web

- **Remote:** `https://github.com/Justin-Lemonade/Obsidian-Vault---Fabian-Phenix`
  (private), branch `main`, tracking set — plain `git push`/`git pull` works.
- **Shell:** `git add -A` → `git commit -m "<msg>"` → `git push`,
  run with workdir = vault root.
- **Author identity (mandatory):** name `Justin Cassidy`,
  email `Justin-Lemonade@users.noreply.github.com` (local vault config).
  Any other email → push rejected (GH007).
- **Key URLs:** repo (above) · Copilot docs `https://www.obsidiancopilot.com/en/docs` ·
  KarpathyWiki repo `https://github.com/green-dalii/obsidian-llm-wiki`
  (now `GD4AI/obsidian-llm-wiki`) · plugin lists
  `https://community.obsidian.md/plugins/<id>`.
