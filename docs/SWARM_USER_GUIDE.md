# OpenCode-Crack Swarm User Guide

This guide has two parts: **Part A** is a complete beginner walkthrough —
if you've never run this before, start there and follow it top to
bottom. **Part B** is the reference material (architecture, dashboard,
recovery, permissions) for once you're past your first run.

If you'd rather have an agent walk you through Part A interactively —
asking you before every install/spend/launch step and reporting back
after each command — point it at
[`.agent_prompts/guided_swarm_run.md`](../.agent_prompts/guided_swarm_run.md).

---

## Before you start — known gaps (read this first)

This system genuinely works for a bounded manager → worker → tester run,
but it has real, documented limitations. Knowing them now saves you from
debugging a "mystery" that's actually a known issue:

- **Permissions in §B10's table are advisory, not enforced.** Swarm agents
  execute tools through OpenCode's own runtime, which does **not**
  consult this package's permission system at all. A worker profile that
  only lists `search_memory` could still theoretically run `write_file`
  if OpenCode's own tool system allows it. Don't treat the role/permission
  table as a security boundary — it's an instruction to a cooperative
  agent, not a sandbox.
- **Worktree isolation is enforced only when you launch through the CLI**
  (`orchestrate swarm`). `opencode_crack/runtime/swarm_coordinator.py`'s
  `launch()` function is a separate, lower-level path that does **not**
  go through this enforcement — don't call it directly expecting
  isolation; use the CLI command in Part A instead.
- **No CLI command currently registers agents or runs environment checks.**
  The underlying logic (`register_agent`, `VALID_ROLES` in
  `opencode_crack/runtime/agent_profile.py`, and `swarm_diagnostics.py`)
  shipped in this package, but the convenience CLI wrappers around them
  (an `agents add` command, a `doctor`/`swarm-status` command) did not —
  they lived in the consuming project's own entry point and weren't part
  of the extraction. Until a project wires up its own CLI for these, use
  `register_agent()` and the diagnostics module directly from Python (see
  A4).
- **Stale Swarm-side state still needs a manual `/swarm recover`.** This
  package's own task leases auto-reclaim with backoff
  (`control_db.reclaim_stale_leases`), but that's separate from Swarm's
  own internal worktree/settlement state. A stuck lane from a previous
  run is a known failure mode with no automatic fix yet.
- **A coder session can get permanently stuck at `DISPATCHED`** if the
  underlying model stream dies at exactly the wrong moment. This is an
  open upstream bug in the swarm plugin, not something this package's
  code can fix.
- **No automatic worktree cleanup.** Completed lanes accumulate under
  `.swarm-worktrees-local/` until someone removes them. There's a safe
  removal helper (`opencode_crack/orchestrator/worktree_guard.py::safe_cleanup_worktree`)
  but nothing calls it automatically yet.
- **Cosmetic branding debt:** a few log/print strings inherited from this
  package's origin project (e.g. the dashboard's startup message) still
  say "AI-Brain" instead of something generic. Harmless, but don't be
  surprised by it.

None of this means don't use it — it means: run one small, bounded task
first (Part A does exactly that), watch it on the dashboard, and don't
assume more automation exists than actually does.

---

# Part A — Beginner walkthrough

This walks you through: install → verify → register three agents → run
one real bounded task → watch it happen on the dashboard. Every command
below is copy-pasteable. Run them in order.

## A1. What you're actually installing

Two separate pieces of software, plus this Python package:

| Piece | What it is | Why you need it |
|---|---|---|
| **OpenCode** | A terminal AI coding agent with a headless server mode | The thing that actually reads/writes files and runs commands |
| **opencode-swarm** | A multi-agent layer on top of OpenCode (manager/worker/tester coordination, shared memory) | What actually runs your manager/worker/tester roles |
| **`opencode_crack`** | This package's control plane | Owns task ownership, agent identity, the dashboard, and worktree isolation that keeps concurrent agents from stepping on each other |

Use the `ZaxbyHub/opencode-swarm` plugin. If you find older references
to a different fork elsewhere, treat `ZaxbyHub/opencode-swarm` as
current.

## A2. Install, in order

1. **Node.js** (for OpenCode itself) — <https://nodejs.org>, any current LTS.
2. **OpenCode**:
   ```bash
   npm install -g opencode-ai
   opencode --version
   ```
3. **An authenticated provider.** Swarm agents run headless and need real
   credentials — pick one:
   ```bash
   opencode auth login
   ```
   Follow its prompts (OpenRouter, Anthropic, or another supported provider).
4. **Bun >= 1.1** (needed for opencode-swarm) — <https://bun.sh>.
5. **opencode-swarm**:
   ```bash
   bun add ZaxbyHub/opencode-swarm
   ```
   If that package name doesn't resolve on npm/bun's registry directly,
   install from source instead:
   ```bash
   git clone https://github.com/ZaxbyHub/opencode-swarm
   cd opencode-swarm
   bun install
   bun link
   ```
6. **This package** — `pip install -e .` from the repo root, or
   `pip install opencode-crack` once published. Nothing swarm-specific
   beyond what the package already needs.

## A3. Verify everything before touching a real task

```bash
opencode --version
swarm --version
python -c "from opencode_crack.runtime import swarm_diagnostics; print(swarm_diagnostics.check())"
```

If any fails, stop and fix it before continuing — don't skip ahead
hoping it'll work anyway. (See the "known gaps" box above: there's no
packaged `doctor`/`swarm-status` CLI command yet, so this is a direct
Python call rather than a one-liner — a good first CLI-wrapper
contribution if you want one.)

## A4. Register your three agents

A bounded run needs at minimum a manager, a worker, and a tester
(`opencode_crack/runtime/agent_profile.py::VALID_ROLES` — the only valid
role values are `manager`, `worker`, `tester`, `monitor`). Pick a model
each agent will use — any string your OpenCode provider recognizes:

```bash
python -c "
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.control_db import register_agent

for name, role in [('my-manager', 'manager'), ('my-worker', 'worker'), ('my-tester', 'tester')]:
    register_agent(AgentProfile(agent_id=name, role=role, model='openai/gpt-4o-mini'))
"
```

(Again, no packaged CLI wraps this yet — see the gaps box. Check your
package's `AgentProfile` signature before copy-pasting, since field
names may have changed since this guide was written.)

## A5. Pick a small, bounded task

Don't point your first run at something big. List what's actually
available and pick something narrow:

```bash
orchestrate list --tier D --status open
```

Pick one task ID from that list — call it `D-XXX` below.

## A6. Run it — the enforced, recommended path

```bash
orchestrate swarm D-XXX --manager my-manager --worker my-worker --tester my-tester
```

What this does, in order:

1. Resolves an isolated `git worktree` for this task/worker
   (`.swarm-worktrees-local/my-worker/D-XXX`, branch
   `swarm/lane/my-worker/D-XXX`) and validates it's clean and genuinely
   isolated. **If this fails for any reason, nothing runs** — no lease is
   taken, no swarm subprocess launches. You'll get an error message
   instead of silent fallback to your main checkout.
2. Records that task/lane/worktree/branch relationship to `control.db`.
3. Builds the swarm config for your three registered agents and launches
   `swarm run` with that isolated worktree as its actual working
   directory.
4. Mirrors Swarm's own events back into `control.db` as it runs.

Useful flags:

```bash
orchestrate swarm D-XXX \
  --manager my-manager --worker my-worker --tester my-tester \
  --timeout 3600 --max-concurrent 2 --budget-usd 1.00
```

`--timeout` is seconds. `--max-concurrent` and `--budget-usd` cap
resource usage — see Part B §B8.

## A7. Watch it happen

In a second terminal, while the swarm is running (or after):

```bash
python -m opencode_crack.runtime.dashboard
```

Open <http://127.0.0.1:8765>. It's read-only and refreshes every 5
seconds. See Part B §B5 for how to read it — the short version: find your
agent in the main table, check it shows your task ID as its **exact
currently leased task**, and watch messages flow manager → worker →
tester in **Agent communication**.

## A8. When it finishes

Check the result:

```bash
orchestrate list --tier D --status review
```

Your task should now be in `review` if the swarm completed and reported
back. Follow the normal review process from there (`AGENTS.md`) —
running the swarm doesn't auto-approve anything.

If it *didn't* reach `review` — go to Part B §B7 (Recovery) before
re-running. Don't just run it again; check what actually happened first.

---

# Part B — Reference

## B1. Architecture

```text
User / primary agent
        |
        v
opencode_crack control.db
  agents / tasks / leases / sessions / messages / events / worktree lanes
        |
        v
OpenCode Swarm (external, third-party -- ZaxbyHub/opencode-swarm)
  worker sessions / runtime messages / shared memory
        |
        v
.swarm/swarm.db
```

Do not use Git commits as live agent communication. Do not merge
`control.db` and `.swarm/swarm.db` — they're intentionally separate:
this package owns organizational identity and task ownership; Swarm owns
live execution state.

## B2. Agent roles

- **Manager:** assigns bounded work, collects evidence, decides pass/retry/escalation.
- **Worker:** implements one bounded task and reports files, tests, evidence and blockers.
- **Tester:** independently verifies acceptance criteria and reports PASS/FAIL with evidence.
- **Monitor:** observes health, leases, sessions and events; does not mutate active work.

Role context is defined in `opencode_crack/runtime/role_context.py` and
injected by `opencode_crack/runtime/swarm_coordinator.py`.

## B3. Communication

Agents use Swarm's own communication tools, not files or Git:

```text
swarm_send       send to another agent
swarm_inbox      inspect pending messages
swarm_agents     inspect active roster
swarm_memory_*   shared runtime knowledge
```

A useful delegation contains an exact task, scope, and required
evidence. A useful completion contains changed files, tests, result, and
blockers. A tester must independently verify the worker's claim, not
just trust the completion message.

## B4. The two ways to launch a swarm — use the CLI one

There are two code paths in this package that can launch a swarm:

| Path | Isolation enforced? | Use it? |
|---|---|---|
| `orchestrate swarm ...` (-> `ManagerLoop.run_once`) | **Yes** | **Yes — this is the one to use.** |
| `opencode_crack/runtime/swarm_coordinator.py::launch()` (called directly from Python) | **No** — calls `SwarmRuntime.run()` without a `cwd`, so it runs from whatever directory the Python process is in | Only if you've already established isolation yourself by some other means. Not recommended for normal use. |

If you're scripting something custom and reach for `swarm_coordinator.launch()`
directly, you are opting out of the worktree isolation guarantee. That's
sometimes legitimate (e.g. a single-agent, non-concurrent script) but
know that you're doing it.

## B5. Dashboard — primary operational view

Start it:

```bash
python -m opencode_crack.runtime.dashboard
```

Open <http://127.0.0.1:8765>. **Read-only**, refreshes every 5 seconds.
It never mutates runtime state — there's no "cancel" or "approve" button.

### What it shows

**Overview cards**: total agents, working agents, active sessions,
active leases, Swarms, messages.

**Every agent · role + exact current work** — the main table. Per
agent: ID, role, manager, status, **exact currently leased task
ID**, lease claim/expiry, heartbeat, OpenCode session, model, notes. If
there's no lease, it explicitly says **No active task lease** — don't
infer an idle agent is working.

**Swarm runtime agents** — what OpenCode Swarm itself reports: Swarm ID,
agent name, session, runtime status, cost, latest result/state, last update.

**Active sessions** — connects each OpenCode session to its registered
agent and task.

**Agent communication** — combined control-plane + Swarm messages. Use
it to confirm delegation, worker reports, and tester verdicts actually
happened.

**Recent lifecycle events** — agent/session/task transitions, lease
events, runtime observations, and `lane_provisioned` events showing
exactly which worktree/branch an agent got. Check this when something
looks stuck.

### Inspecting one agent

1. Find it in **Every agent · role + exact current work**.
2. Check its role and manager.
3. Read its **Exact task**.
4. Follow its session into **Active sessions** and **Swarm runtime agents**.
5. Read its messages for the latest instruction/report.
6. Check lifecycle events if anything's ambiguous.

### Verifying manager -> worker -> tester actually happened

```text
Manager owns task lease
        v
Worker has matching task/session
        v
Worker sends evidence
        v
Tester receives verification work
        v
Tester sends PASS/FAIL + evidence
        v
Manager records final decision
```

If a link is missing, investigate it before trusting the outcome. A
successful Swarm process exit is not proof the organizational workflow
actually succeeded — check the chain, not just the exit code.

### Dashboard safety boundary

The dashboard cannot edit tasks, send commands, approve operations,
restart Swarms, alter leases, or modify messages. It observes; it
doesn't control. (`ZaxbyHub/opencode-swarm` itself doesn't ship this web
dashboard at all — it's part of this package's operational layer on top.)

## B6. Where state actually lives

| Layer | Purpose |
|---|---|
| Task board (`orchestration/tasks.yaml`) | Work ownership and task lifecycle |
| `data/control.db` | Agent identities, leases, sessions, messages, events, worktree-lane records |
| `.swarm/swarm.db` | OpenCode Swarm's own runtime state |
| `.swarm-worktrees-local/` | Isolated git worktrees for active/completed lanes (no auto-cleanup yet — see the gaps box) |
| Dashboard | Human-readable combined view of the above |
| Git | Reviewed source/documentation history — not a live communication channel |

## B7. Recovery

If an agent looks stale, inspect leases, events, and Swarm state
together:

```bash
python -c "from opencode_crack.runtime import control_db; print(control_db.get_stale_leases())"
swarm status
swarm logs <swarm-id>
```

Then look at the dashboard's task lease, heartbeat, session, messages,
and events together before doing anything. **Don't immediately start
another worker if an existing lease might still be active** — you could
end up with two agents in the same lane.

If a task's lease really is stale and abandoned:

```bash
orchestrate release D-XXX --notes "stale lease, agent unresponsive since <time>"
```

For an interrupted Swarm (not a control-plane lease issue — a Swarm-side
resume), use its own resume mechanism:

```bash
swarm run swarm.json --resume <swarm-id>
```

For a **Swarm-side stuck lane** (not a control-plane lease at all — see
the gaps box above), there is currently no automatic fix. The documented
manual step is Swarm's own `/swarm recover`; the underlying bug is
inside the third-party plugin's internals, so this package's code can't
resolve it directly.

## B8. Resource limits

Keep any real run bounded:

```bash
orchestrate swarm D-XXX \
  --manager my-manager --worker my-worker --tester my-tester \
  --max-concurrent 2 --budget-usd 1.00
```

If calling the lower-level `swarm` CLI directly (not recommended — see
B4): `swarm run swarm.json --max-concurrent 2 --budget-usd 1.00`.

## B9. Authority boundaries

Agents decide bounded implementation details. Managers decide
assignment, evidence sufficiency, bounded retries, and escalation. The
primary agent/a human decides architecture, dependency replacement,
security boundaries, control-plane schema changes, and organizational
redesign.

## B10. Permissions — read the caveat above before trusting this table

Intended, advisory role permissions (this is **not enforced by the
runtime** — see "Before you start" at the top of this guide):

| Role | Read | Write | Bash |
|---|---:|---:|---:|
| Manager | yes | no | no |
| Worker | yes | yes | only when required |
| Tester | yes | no | tests/read-only commands |
| Monitor | yes | no | no |

Treat this as an instruction you're giving a cooperative agent, the same
way you'd brief a human contractor — not as a technical restriction that
stops a misbehaving or compromised agent from doing something else.

## B11. First-run checklist

```text
[ ] Node.js installed
[ ] OpenCode installed (opencode --version works)
[ ] Provider authenticated (opencode auth login)
[ ] Bun installed
[ ] opencode-swarm installed (swarm --version works)
[ ] swarm_diagnostics check passes (A3)
[ ] Three agents registered (manager, worker, tester)
[ ] A bounded task picked (orchestrate list --tier D --status open)
[ ] orchestrate swarm ran without a provisioning/validation error
[ ] Dashboard opens at localhost:8765
[ ] Your agent shows its exact task/session in the dashboard
[ ] Manager -> worker -> tester chain visible in Agent communication
[ ] Task reached `review` status after the run
```

## B12. Source and version notes

Runtime plugin: **`ZaxbyHub/opencode-swarm`** —
<https://github.com/ZaxbyHub/opencode-swarm>.

Version numbers drift — treat anything you see as a floor, not a pin.
Plugin version drift between releases is expected, not a sign of a
broken install.
