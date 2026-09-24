# Connecting a Real NotebookLM Notebook (Optional)

The skill mimics NotebookLM's study features natively and needs no connection. This path is only for when the user explicitly wants their actual Google NotebookLM notebooks (their sources, Audio Overviews, quizzes) reachable from the agent.

## Option A: Unofficial API + MCP (`notebooklm-py`)

Community project `teng-lin/notebooklm-py` exposes NotebookLM through Python, a CLI, and an MCP server (33 tools: notebooks, sources, source-grounded chat, studio artifacts, deep research). Related wrappers: `notebooklm-mcp-server`, `notebooklm-connector` (one-click Claude Desktop bundle).

Setup (run by the user, not the agent — it needs interactive login):

1. Install: `pip install "notebooklm-py[mcp]"` (needs Python 3.12+; or run install-free via `uvx --from "notebooklm-py[mcp]" notebooklm-mcp --help`).
2. Authenticate: `notebooklm login` — opens a browser for Google sign-in. The session is stored locally per profile; nothing is uploaded anywhere except to NotebookLM itself. Sessions last roughly 2-4 weeks, then re-login.
3. Attach the MCP server to the agent client with `notebooklm mcp install <client>` (knows claude-desktop, claude-code, cursor, windsurf) or add `notebooklm-mcp` to the client's MCP config manually following that client's docs. Restart the client afterwards.

## Caveats — read before recommending this

- **Unofficial and fragile.** It drives NotebookLM's undocumented internal APIs, which Google can change without notice. Breakage is a matter of when, not if.
- **Your Google account, your quota.** Free accounts get roughly 50 questions/day. Heavy use may be throttled.
- **Interactive auth only.** The agent cannot log in for the user; setup is a user-side task.
- **Context cost.** The MCP server registers ~30 tools, which eats context window. Recommend enabling it only during active NotebookLM sessions.
- **Credentials stay local.** Auth tokens live under `~/.notebooklm/` (or browser cookies read locally). Never paste tokens into chat or commit them.

## When to suggest which path

- Default: use the skill's built-in mimicry (Steps 1-7) — no setup, no breakage risk.
- Suggest Option A only if the user already keeps sources in NotebookLM and wants the agent to work against those exact notebooks.
