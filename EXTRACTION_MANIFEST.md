# C-076 Extraction Manifest

Staged locally at `/home/claude/extraction-staging/` (not yet a git repo,
not yet pushed anywhere). This is the compiled output of steps 1 and
part of step 3 of the C-076 plan (full spec: `delegated_tasks/C-076.md`
in AI-Brain, commit `8ecfbde`). Everything below is real, audited
against the actual AI-Brain codebase — not assumed.

## Files staged (72 .py files, copied not moved — AI-Brain's own copy
is untouched)

```
opencode_crack/
├── config.py                  (NEW — minimal, see below)
├── cli_format.py              (from src/cli_format.py, unmodified)
├── logging_config.py          (from src/logging_config.py, unmodified)
├── orchestrator/              (from src/orchestrator/, entire dir, unmodified)
├── runtime/                   (from src/runtime/, entire dir, unmodified)
├── tools/                     (from src/tools/, entire dir, unmodified)
├── prompts/                   (from src/prompts/, entire dir, unmodified)
└── storage/
    ├── __init__.py
    └── migrations.py          (from src/storage/migrations.py ONLY —
                                 facts_db.py, vector_store.py,
                                 contradiction_detector.py, and
                                 result_prioritizer.py are domain and
                                 stay in AI-Brain)
```

Also staged at the repo root: `requirements.txt`, `.gitignore`, `README.md`
(this document's siblings in this folder).

## Dependency audit — confirmed findings

**Third-party dependencies: PyYAML only.** Grepped every top-level
import across all 72 files. Everything else is standard library.
`requirements.txt` reflects this plus `python-dotenv` (for `config.py`)
and `pytest` (dev/test).

**`config.py` — rebuilt minimal, not copied.** AI-Brain's `src/config.py`
mixes `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, and five different model
provider keys into the same file as the six constants orchestration code
actually uses (`_PROJECT_ROOT`, `CONTROL_DB_PATH`, `OPENCODE_BASE_URL`,
`SWARM_DB_PATH`, `SWARM_SMOKE_MODEL`, `SWARM_SMOKE_TIMEOUT`). Copying it
verbatim would silently drag Telegram/provider-key concerns into a repo
that has no business knowing about them. The staged `config.py` has only
those six.

**Reverse dependencies: clean.** Only `src/main.py` and `src/brain.py`
(both AI-Brain-side) import from `orchestrator`/`runtime`/`tools`/
`prompts`. No domain module (`memory/`, `storage/facts_db.py`, `bot/`,
`ingestion/`) depends on orchestration internals.

## ⚠️ One coupling point found during staging — needs your call

`src/orchestrator/change_summary.py`'s `_decisions_since()` function
imports `src.memory.decisions` and `src.storage.facts_db.FACTS_DB_PATH`
directly (function-local import, which is why the first audit pass
missed it — that grep only checked module-top imports). This is real,
intentional functionality: `build_change_summary()` composes git
commits + task changes + control_db agent events (all generic) together
with AI-Brain's domain-specific recorded "decisions" (backed by
facts_db) into one report.

Three ways to handle it — I haven't picked one, since it changes
behavior rather than just moving files:

1. **Leave `change_summary.py` behind in AI-Brain entirely.** Simplest.
   The new repo loses this one report-composition feature; AI-Brain
   keeps a local copy if it still wants it.
2. **Extract it, make the decisions source optional.** Wrap the
   `src.memory`/`facts_db` import in try/except ImportError, defaulting
   to an empty decisions list when unavailable. The new repo runs
   standalone (decisions always empty); AI-Brain's environment (which
   has `src.memory` installed) gets decisions populated automatically.
   Most flexible, small code change.
3. **Extract it as-is, dependency and all.** Only works if AI-Barin's
   `src.memory`/`src.storage.facts_db` also end up importable from
   wherever the new repo runs — defeats the "no domain coupling"
   premise, not recommended, listed for completeness.

I'd lean toward option 2, but this is exactly the kind of call C-076's
spec flagged as needing you rather than being resolved silently.

## Import rewrite scope (step 4 of the plan — not yet done)

Quantified, not estimated: **158 lines across 41 files** still say
`from src.orchestrator...` / `from src.runtime...` / `from src.config...`
etc. and need rewriting to `from opencode_crack...` (or whatever the
final package name is) once this becomes its own repo. Breakdown by
module is in the audit output; largest concentrations are
`src.orchestrator` (25), `src.runtime` (19), `src.runtime.agent_profile`
(14), `src.orchestrator.task_board` (10), `src.config` (10).

Verified this is real work, not theoretical: I test-imported the staged
package in isolation and it fails immediately with
`ModuleNotFoundError: No module named 'src'`, exactly as expected.

## What's still needed from you before this becomes a real repo

1. **A new, empty GitHub repo.** My GitHub App installation is scoped
   to `AI-Brain-` only — it has no permission to create or push to a
   new repo. You'll need to create it (any name/visibility) and either
   add this installation to it or provide separate credentials.
2. **Confirm the package name.** `opencode_crack` is a placeholder
   used throughout this staging area and the manifest.
3. **A decision on `change_summary.py`** (see above).

Once those three are settled, the remaining plan steps (git subtree
split with history, push, rebuild `pyproject.toml`, wire AI-Brain back
as a consumer, full test suite) are mechanical from here.

## Update — post-push status

All three items above are resolved: repo created
(`Justin-Lemonade/OpenCode-Crack`), package name confirmed as
`opencode_crack`, and `change_summary.py` shipped with option 2 (the
decisions source is optional, defaulting to empty — see that file's own
docstring for the full rationale). The subtree split with history didn't
pan out (see the project's own overview notes); the package was pushed
fresh instead, with AI-Brain's original commit history untouched.

**Cosmetic rebrand pass — done.** Every AI-Brain-specific string with
real user/agent-facing surface has been fixed: the context-telemetry env
var, HTTP User-Agent identifiers, OpenCode session titles, the dashboard
(title, header, print statement, message-source label), agent identity
prompts, generated report headers, and a real (not just cosmetic) bug in
`token_audit.py` where it excluded a hardcoded `"AI-Brain-"` directory
name from its own scan — a nested-checkout exclusion that only made
sense in the origin repo, now fixed to exclude `"OpenCode-Crack"`
instead. Four files still mention AI-Brain deliberately, not by
omission — `change_summary.py`, `config.py`, `prompts/registry.py`,
`tools/registry.py` — because those mentions are accurate provenance
documentation (what was deliberately left behind during extraction and
why), not stray branding, and erasing them would lose real information
a future maintainer needs.

**General hardening pass — done.** Ran `pyflakes` across the package
(clean; wasn't run before). Found and fixed: a genuine bug in
`roadmap_check.py` where two full implementations of `check_roadmap`
existed — the second silently shadowed the first at import time, making
~40 lines of the first dead and unreachable (removed, since the second
was a strict superset); several unused imports (`datetime`/`timezone` in
`dashboard.py`, `AgentProfile` in `cli.py`, `normalize_block` in
`token_audit.py`, `shutil`/`time`/`Path` in `request_queue.py`/
`first_party_swarm.py`); an unused local variable in `cli.py`'s argument
parser setup; a stray `f"..."` prefix with no interpolation in
`report_schema.py`; and a loop variable in `task_board.py` shadowing the
module-level `dataclasses.field` import. Full test suite re-verified
green after every change (914 passed, 11 skipped, 0 failed — same
baseline as the original push).
