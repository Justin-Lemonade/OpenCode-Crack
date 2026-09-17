# OpenCode-Crack

A local-first, git-based multi-agent task orchestration framework:
task board, knowledge board, concerns board, agent runtime (leases,
handoffs, swarm coordination), and a delegator/dispatcher that routes
work to different backends (Claude, OpenCode, or any CLI-invocable
model).

Extracted from [AI-Brain](https://github.com/Justin-Lemonade/AI-Brain-)
(C-076), where this started as the orchestration layer for a personal
knowledge system before outgrowing that one use case. AI-Brain now
depends on this repo as a package rather than owning the code directly,
so improvements here flow back into AI-Brain and any other project that
adopts it — including OpenCode sessions run outside of AI-Brain entirely.

## Design

- **Nearly dependency-free on purpose.** The only third-party package
  this framework needs is PyYAML. Everything else — argparse, sqlite3,
  subprocess, dataclasses — is Python standard library. Model backends
  are invoked by shelling out via `subprocess`, not SDK calls, so this
  framework has no opinion on which LLM provider you use.
- **State lives in git-tracked files, not a server.** `tasks.yaml`
  holds only state (status/assignee/timestamps); task definitions
  themselves come from parsing Markdown headers in a roadmap doc plus
  a `delegated_tasks/*.md` extension directory. Knowledge and concerns
  follow the same pattern. This is what makes the board reviewable in
  a normal PR diff instead of hidden inside a database.
- **Tiered task ownership.** Tasks are tagged `human` / `delegate` /
  `primary_claude` by ID prefix (`H-`/`D-`/`C-`) so routing which agent
  should pick up which work is mechanical, not a judgment call made
  fresh each time.

## Install

```bash
pip install -e .
```

This exposes an `orchestrate` command usable from any directory, not
just from inside this repo.

## Usage

```bash
orchestrate list                    # see the task board
orchestrate briefing                # what's active, stale, blocked
orchestrate claim <TASK-ID>         # claim a task for an agent
orchestrate contract <TASK-ID>      # print the delegation contract
orchestrate dispatch <TASK-ID>      # send a contract to a backend
orchestrate concern file ...        # file a concern
orchestrate knowledge file ...      # file a durable knowledge entry
```

Run `orchestrate --help` for the full command tree.

## Configuration

Copy `.env.example` to `.env` and set what you need — every value has a
sane local-filesystem default (`CONTROL_DB_PATH`, `SWARM_DB_PATH`, etc.
default to paths under this repo, not anywhere shared).

## What's deliberately NOT in this repo

This framework only knows about tasks, knowledge entries, concerns, and
agent coordination state. It has no opinion on and no dependency on:
- Where your actual project's domain data lives (facts, embeddings,
  documents) — that belongs in the project that adopts this framework.
- Which LLM provider or providers you use — dispatch shells out to
  whatever CLI you configure.

If you're looking for the personal-knowledge-ingestion system this was
extracted from, see
[AI-Brain](https://github.com/Justin-Lemonade/AI-Brain-).

## Status

Extracted 2026-09 (C-076). Task/knowledge/concerns boards below start
empty — this repo's own board tracks the framework's development, not
the projects that consume it.
