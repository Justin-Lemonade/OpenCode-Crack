# Repository Sorting Protocol

Purpose: tidy the AI-Brain repository (misfiled reports, duplicate folders, a flat and mixed `docs/`, prompts and scripts in odd places) **without breaking the code, the task board, or any link**. Task reports live in `reports/tasks/` (decided by the human on 2026-10-08).

This prompt is written so that a smaller model can run it. Follow it literally. Do not improvise paths, do not shorten steps, and do not "improve" anything that is not listed. When a result differs from the "Expected" line, stop and report instead of guessing.

Version 2 (2026-10-08). Measured against commit `61f1ec6` (2026-10-07). Counts below may drift slightly if the repository has changed; the rules still apply.

---

## 0. How to start this job

Open the repository, then give the agent this one line:

> Read and follow `agent_prompts/sort_repo.md`. Start at Phase 0 and stop at the approval gate in Phase 1.

Default behavior is **plan first, then wait for the human's approval**. Only skip the wait if the human wrote "execute without asking".

---

## 1. Hard rules (read twice)

1. Work on a new branch named `sort/<YYYY-MM-DD>`. Never commit to `main`. Never push to `main`. Never force-push.
2. Never edit, move, or delete anything under: `src/`, `tests/`, `orchestration/`, `delegated_tasks/`, `concerns/`, `knowledge/`, `.github/`, `.opencode/`, `.kilo/`. These hold code, task-board state and data stores that other agents write to constantly.
3. Never add any new file inside `delegated_tasks/`. The task parser reads every `.md` file there.
4. Never hand-edit `orchestration/tasks.yaml`. Board changes go through the `orchestrate` CLI only. The one exception is the exact-string path rewrite done by `python scripts/sortcheck.py repath`, which the human approved on 2026-10-08.
5. Never use `rm`, `git rm`, or `git mv` yourself. Every move, delete and untrack goes through `python scripts/sortcheck.py apply <manifest>`, which validates first and records what it did.
6. Never move a file into the top-level `archive/` folder. It is listed in `.gitignore`, so files placed there would silently not be committed. Use `docs/archive/` or a `reports/` subfolder.
7. Never touch `opencode_crack/prompts/`. That is Python code (the prompt registry), not a prompt folder. The prompt folder is `agent_prompts/`.
8. Never rename files in this job (spaces, capitals and em dashes in names are left as they are). Renames are a human decision.
9. Never delete a file unless `apply` does it as a `DEDUPE` line. `apply` refuses unless the two files are byte-identical.
10. Do not run the baseline command a second time. Do not edit `scripts/sortcheck.py` or `scripts/sortcheck_manifests.sh`.
11. If a file was changed in the last 3 days, leave it alone. Another agent may be using it. The tool skips these automatically and lists them; that is correct behavior, not an error.
12. If anything fails and you are unsure, stop and write what you saw. A stopped job costs nothing; a damaged board costs a lot.

---

## 2. What you need to understand first (context)

AI-Brain is a local-first personal knowledge system plus a multi-agent orchestration layer. Many AI agents work in this repository at the same time through a task board and a CLI (`orchestrate ...`). That is why locations matter: code and the board look files up **by exact path**.

Facts that were verified in the repository (do not re-derive them, rely on them):

| Fact | Consequence for sorting |
|---|---|
| Task reports live at `reports/tasks/<TASK-ID>_report.md`. The one place that knows this is `opencode_crack/orchestrator/report_paths.py` (`TASK_REPORTS_DIR`); it still finds old flat copies in `reports/`. `orchestration/tasks.yaml` holds about 360 such paths. | Any file named like `D-123_report.md`, `C-004_report.md` or `D-231_audit.json` belongs in `reports/tasks/`, never anywhere else. |
| On 2026-09-26 (commit `9c0a346`) 232 task reports were moved into `reports/delegated_tasks/` while the board still pointed at `reports/`, leaving 232 dead pointers. On 2026-10-08 the human chose `reports/tasks/` as the single home; report files, board paths, code and prompts were moved or updated together. | Phase 2 is normally already done. If `inventory` shows `0` dead board pointers and `reports/tasks/` exists, skip Phase 2. If task-ID files show up outside `reports/tasks/` again (an agent used an old prompt), run Phase 2 again. |
| `delegated_tasks/*.md` is parsed for headers of the form `## D-NNN — Title`. | Leave that folder completely alone. |
| `docs/iteration improvement ideas.md` is the roadmap source of truth (`ROADMAP_PATH` in `task_parser.py`). | Never move it. |
| `STATUS.md`, `CONCERNS_BOARD.md`, `KNOWLEDGE_BOARD.md`, `reports/activity.log` are regenerated automatically. | Never edit them. If a command modified them, run `git checkout -- <file>` before committing. |
| Many `src/` files mention doc paths in comments or strings (for example `src/factdev/similarity.py` mentions `docs/FACT_DEVELOPMENT_SYSTEM.md`). | A doc mentioned by code is "pinned": it stays where it is. The tool detects this and skips it. |
| `tests/test_doc_links.py` checks Markdown links in `README.md`, `USER_GUIDE.md` and `docs/*.md` (top level of `docs/` only). | After moving docs, links must still resolve. The tool rewrites links; `check` verifies all of them, including inside subfolders. |
| `orchestrate search` indexes every `.md` file recursively (including dot-folders). | Moving files into subfolders does not hide them from search. |
| `scripts/opencode_parent_child_experiment.py` is imported by more than 20 tests as a module. | It stays in `scripts/`. (The tool catches module imports, not only file names.) |
| `.gitignore` lists `/archive/`, yet 8 files under `archive/technoblade/` are tracked. | Do not touch them. This goes on the human's decision list. |

### Folder purposes (current, before sorting)

- `src/`, `tests/` — code and its test suite. Not part of this job.
- `orchestration/` — board state (`tasks.yaml` is canonical) and the request queue. Not part of this job.
- `delegated_tasks/` — task definitions. Not part of this job.
- `reports/` — per-task reports (flat, required) plus loose project-level material. **Main target.**
- `docs/` — documentation, flat and mixed with reports. **Main target.**
- `agent_prompts/` — prompts that agents read. This file lives here. `prompts/` holds one stray prompt.
- `scripts/`, `evidence/`, `tools/`, `evals/`, `benchmarks/`, `config/`, `learning/` — helpers and records. Minor changes only (see manifests).
- `concerns/`, `knowledge/` — their own boards with their own CLI and audit protocol. Not part of this job.

---

## 3. Your two tools

Both are in `scripts/`. They need only Python 3 and git.

**`python scripts/sortcheck.py <command>`**

| Command | What it does |
|---|---|
| `inventory` | Read-only survey: folder sizes, root clutter, odd names, duplicates, dead board pointers. |
| `repath [--dry-run]` | Rewrites mentions of task reports in code, tests, board YAML and living docs to the `reports/tasks/` path (exact strings only, only for files that exist there). |
| `refs <path>...` | Shows who references a file. Verdict `FREE`, `REWRITE` (only docs link to it; the tool fixes the links) or `BLOCKED` (code or the board mentions it). |
| `recent --days 3` | Lists files changed lately (these are skipped). |
| `baseline` | Saves the starting state. Run once, before any change. |
| `apply <manifest.tsv> [--dry-run]` | Validates the whole manifest first, then performs moves, dedupes and untracks, repairs Markdown links and path mentions, and logs everything. Unsafe entries are skipped and listed. |
| `note-edit <path>...` | Declares a hand edit that you made on purpose. |
| `check` | Compares the current tree to the baseline. Must print `RESULT: PASS`. |

**`bash scripts/sortcheck_manifests.sh`** writes four manifest files into `$SORT_SCRATCH`:

| Manifest | Content |
|---|---|
| `m1_repair.tsv` | Gather every task-ID-named file (flat `reports/`, `reports/delegated_tasks/`, stragglers) into `reports/tasks/`. |
| `m2_dedupe.tsv` | Remove exact duplicate copies; stop tracking `.coverage`. |
| `m3_reports.tsv` | Sort loose reports into topic folders. |
| `m4_docs_prompts_scripts.tsv` | Sort `docs/`, gather prompts into `agent_prompts/`, tidy stray scripts. |

Manifest format: one entry per line, columns separated by a TAB. `SOURCE<TAB>DESTINATION`, or `DEDUPE<TAB>REMOVED<TAB>KEPT`, or `UNTRACK<TAB>.coverage`. Lines starting with `#` are comments.

---

## 4. Procedure

Use a task list. Mark each phase done only when its "Expected" line is true.

### Phase 0 — Setup and comprehension (read-only)

```bash
export SORT_SCRATCH="$HOME/.sort_scratch/ai-brain"
mkdir -p "$SORT_SCRATCH"
git fetch origin
git switch main && git pull --rebase
git switch -c "sort/$(date +%Y-%m-%d)"
git status --short                      # Expected: no output (clean tree)
python scripts/sortcheck.py baseline    # Expected: "Baseline saved ..." (0 missing board paths once Phase 2 is done)
python scripts/sortcheck.py inventory   # read the whole output
python scripts/sortcheck.py recent --days 3
```

Record the test baseline **before** changing anything, so that pre-existing failures are not blamed on the sort:

```bash
cleanup_tests() { rm -rf .swarm-worktrees-local; git worktree prune
  git for-each-ref --format='%(refname)' refs/heads/swarm/ | xargs -r -n1 git update-ref -d
  git checkout -- STATUS.md CONCERNS_BOARD.md KNOWLEDGE_BOARD.md reports/activity.log 2>/dev/null || true; }
python -m pytest tests --ignore=tests/live -q -p no:cacheprovider --continue-on-collection-errors -rfE 2>&1 | tee "$SORT_SCRATCH/tests_before.txt" | tail -3
grep -E '^(FAILED|ERROR)' "$SORT_SCRATCH/tests_before.txt" | sed 's/ - .*//' | sort > "$SORT_SCRATCH/fail_before.txt"
cleanup_tests; git status --short       # Expected: no output
```

Some tests may already fail because optional packages are missing (for example `telegram`). That is fine. The aim is "no new failures", not "all green". (On the machine where this protocol was written: 72 failed, 3666 passed, 18 collection errors, all before any change.)

Then read these four files completely so you understand the conventions: `docs/AGENTS.md`, `reports/README.md`, `docs/PROJECT_CHARTER.md` (first 60 lines), `agent_prompts/review_work.md` (first 40 lines).

Generate the manifests and preview all of them:

```bash
bash scripts/sortcheck_manifests.sh
for m in m1_repair m2_dedupe m3_reports m4_docs_prompts_scripts; do
  echo "=== $m"; python scripts/sortcheck.py apply "$SORT_SCRATCH/$m.tsv" --dry-run
done
```

Expected (approximate): `m1` about 235 moves, 0 skipped; `m2` 28 dedupes and 1 untrack; `m3` about 67 moves with a handful skipped (recent files, one pinned critique); `m4` about 26 moves with a handful skipped (recent overnight and report files). **Any line starting with `MANIFEST REJECTED` means a manifest line is wrong or the repository changed: stop and report it.**

### Phase 1 — Approval gate (STOP HERE)

Write a short plan for the human, in plain prose, covering:

1. What you found (dead board pointers, duplicate folders, mixed `docs/`, stray prompts and scripts), with the numbers from `inventory`.
2. The target layout (section 5 below), and which files will be skipped and why (copy the `SKIPPED` lines from the dry runs).
3. The decisions that only the human can make (section 7).

Then wait for the answer. Do not continue until the human approves. If the human changed something, adjust the generated `.tsv` files by deleting the lines concerned (never invent new lines).

### Phase 2 — Task reports into `reports/tasks/` (skip if already done)

```bash
python scripts/sortcheck.py apply "$SORT_SCRATCH/m1_repair.tsv"
python scripts/sortcheck.py repath          # points board, code, tests and docs at reports/tasks/
python scripts/sortcheck.py check
```

Expected: `RESULT: PASS` and `Board report pointers missing: ... -> 0 now`. Commit **before** running any test (the board code refuses to run with staged changes):

```bash
git add -A && git commit -m "sort: task reports into reports/tasks/ and repoint board, code, docs"
```

### Phase 3 — Remove exact duplicates

```bash
python scripts/sortcheck.py apply "$SORT_SCRATCH/m2_dedupe.tsv"
printf '\n# Coverage data is generated, never committed\n.coverage\n' >> .gitignore
python scripts/sortcheck.py note-edit .gitignore
python scripts/sortcheck.py check          # Expected: RESULT: PASS
git add -A && git commit -m "sort: drop byte-identical duplicate report copies; untrack .coverage"
```

### Phase 4 — Sort `reports/`

```bash
python scripts/sortcheck.py apply "$SORT_SCRATCH/m3_reports.tsv"
python scripts/sortcheck.py check          # Expected: RESULT: PASS
git add -A && git commit -m "sort: group loose reports into critiques/, research/, project/, incidents/, handoffs/, overnight/"
```

### Phase 5 — Sort `docs/`, prompts and scripts

```bash
python scripts/sortcheck.py apply "$SORT_SCRATCH/m4_docs_prompts_scripts.tsv"
python scripts/sortcheck.py check          # Expected: RESULT: PASS
python -m pytest tests/test_doc_links.py -q -p no:cacheprovider    # Expected: all pass
git add -A && git commit -m "sort: docs into guides/design/plans/protocols/handoffs; gather prompts; tidy scripts"
```

If `apply` prints `HINT` lines, open the named document, find the mentioned file name, and fix the mention by hand only if it is a live reference (a normal sentence such as "see `SEARCH_PROTOCOL.md`"). Ignore hints inside generated audit listings. After any hand edit run `python scripts/sortcheck.py note-edit <file>`.

### Phase 6 — Orientation documents (so future agents find things)

Write exactly these three changes, then run `note-edit` on the two existing files:

1. Create `docs/REPO_MAP.md` from the template in section 6. Before saving, compare the folder table with `ls` and `git ls-files | cut -d/ -f1,2 | sort -u`, and correct anything that no longer matches.
2. Append to `reports/README.md` a section titled `## Subfolders (not task reports)` that lists `critiques/`, `research/`, `project/`, `incidents/`, `handoffs/`, `overnight/`, `audits/` with one line each, and states that every `<TASK-ID>_*` file lives in `reports/tasks/` because the code (`report_paths.py`) and the board address them by that path.
3. Add one line near the top of `docs/AGENTS.md`: a sentence pointing to `REPO_MAP.md` in the same folder, written as a normal relative Markdown link (it must resolve from `docs/`).

```bash
python scripts/sortcheck.py note-edit reports/README.md docs/AGENTS.md
python scripts/sortcheck.py check
python -m pytest tests/test_doc_links.py -q -p no:cacheprovider
git add -A && git commit -m "sort: add docs/REPO_MAP.md and document the new reports/ layout"
```

### Phase 7 — Verify everything

```bash
cleanup_tests
python scripts/sortcheck.py check                         # Expected: RESULT: PASS
orchestrate status | head -12          # Expected: normal dashboard, no traceback
git status --short                                        # Expected: no output (if STATUS.md changed: git checkout -- STATUS.md)
python -m pytest tests --ignore=tests/live -q -p no:cacheprovider --continue-on-collection-errors -rfE 2>&1 | tee "$SORT_SCRATCH/tests_after.txt" | tail -3
grep -E '^(FAILED|ERROR)' "$SORT_SCRATCH/tests_after.txt" | sed 's/ - .*//' | sort > "$SORT_SCRATCH/fail_after.txt"
echo "NEW failures:"; comm -13 "$SORT_SCRATCH/fail_before.txt" "$SORT_SCRATCH/fail_after.txt"
cleanup_tests
```

Expected: `NEW failures:` followed by nothing. If a test fails that passed before, find which moved file it needs (`python scripts/sortcheck.py refs <old path>`), report it, and ask the human; never edit the test. (Tests that create git worktrees can fail because of leftovers from an earlier run; `cleanup_tests` removes them. Re-run once after cleanup before concluding anything.)

### Phase 8 — Deliver

```bash
git fetch origin main && git rebase origin/main      # on conflict: STOP and report, do not resolve blindly
python scripts/sortcheck.py check --base "$(git merge-base HEAD origin/main)"
git push -u origin "$(git branch --show-current)"
test "$(git ls-remote origin "$(git branch --show-current)" | cut -f1)" = "$(git rev-parse HEAD)" && echo "push verified"
cp "$SORT_SCRATCH/log.tsv" "docs/archive/repo_sort_$(date +%F).tsv"   # permanent record of every move
```

Commit that log file on the same branch and push again. Do **not** merge into `main`: the human merges after reading the report.

If other agents added files to `reports/` or `docs/` while you worked, the rebase brings them in untouched; that is expected. If `check` then reports a problem only for files you did not touch, list them in the report instead of fixing them.

---

## 5. Target layout

```
reports/
  README.md, activity.log                 (unchanged, code-owned)
  tasks/        every <TASK-ID>_report.md|.json and <TASK-ID>_* file (required by code and board)
  critiques/    agent critique batches (critique_*.md)
  research/     ingestion_types/ (format research), agent_architecture/ (swarm research and probes)
  project/      status reports, final reports, verification records (not tied to one task)
  incidents/    error and board-repair records
  handoffs/     AI handoff documents
  overnight/    overnight-memory run reports and logs
  audits/       token audits and agent-quality assessments
docs/
  AGENTS.md, PROJECT_CHARTER.md, NEW_USER_GUIDE.md, roadmap pair, REPO_MAP.md   (entry points, stay at top)
  guides/       how to run or set something up
  design/       architecture and design documents
  plans/        plans, roadmaps, deferred ideas
  protocols/    rules agents follow
  handoffs/     briefs written for another agent
  known_issues/, archive/                (already existed)
agent_prompts/   all prompts, including the knowledge handoff protocol, audit prompt and the PR #3 prompt
scripts/          repro/ (one-off crash reproductions), probes/ (disposable probes), the rest unchanged
```

Documents that code or the board mention by path stay in `docs/` (pinned). That is intended.

### Rules for files that are not in any manifest (later runs, new files)

Decide in this order and stop at the first match:

1. Path starts with a protected area (section 1, rule 2) → leave it.
2. Name starts with `C-`, `D-` or `H-` plus digits → it belongs in `reports/tasks/` (use the manifest rule; never anywhere else).
3. `python scripts/sortcheck.py refs <path>` says `BLOCKED` → leave it, add to the human's list.
4. Changed in the last 3 days → leave it, mention it as "revisit later".
5. Otherwise classify by what the document **is**:
   - how to run or configure something → `docs/guides/`
   - architecture or design → `docs/design/`
   - plan, roadmap, deferred ideas → `docs/plans/`
   - rules agents must follow → `docs/protocols/`
   - brief written for another agent → `docs/handoffs/`
   - text an agent is told to execute (a prompt) → `agent_prompts/`
   - a dated report, audit, critique, incident or investigation → the matching `reports/` subfolder
   - one-off script → `scripts/repro/` or `scripts/probes/`
6. Still unsure → leave it and add it to the human's list. Doubt means do nothing.

Put the new line in a small manifest, run `apply --dry-run`, then `apply`, then `check`.

---

## 6. Template for `docs/REPO_MAP.md`

Copy, then correct against the real tree:

```markdown
# Repository Map

Where things live in AI-Brain, and where new files belong. Written by the sorting job (see `agent_prompts/sort_repo.md`). Moves are recorded in `docs/archive/repo_sort_<date>.tsv`.

## Top-level folders

| Folder | What it holds | Who may change it |
|---|---|---|
| `src/` | Application code (brain core, orchestrator, runtime, ingestion, overnight, factdev, ...) | Task owners, through tasks |
| `tests/` | pytest suite | Task owners |
| `orchestration/` | Board state: `tasks.yaml` (canonical), `concerns.yaml`, `knowledge.yaml`, request queue | `orchestrate` CLI only |
| `delegated_tasks/` | Task definitions parsed from `## D-NNN — Title` headers. Only task files belong here | Task authors |
| `reports/` | Task reports (flat, `<ID>_report.md`) plus topic subfolders | Agents writing reports |
| `concerns/`, `knowledge/` | Concerns board and knowledge board entries | Their own CLIs |
| `docs/` | Documentation (see below) | Anyone |
| `agent_prompts/` | Prompts that agents read and follow | Prompt owners |
| `scripts/`, `tools/`, `evals/`, `benchmarks/`, `evidence/`, `config/`, `learning/` | Helpers, search tools, evaluations, records | Task owners |
| `.opencode/`, `.kilo/`, `.github/` | Agent runtime config, skills, CI | Task owners |

## Where a new file goes

(Insert the classification list from section 5 of the sorting protocol, one line per kind of file.)

## Pinned documents

Documents that code, tests or the board mention by path. They stay in `docs/` until those mentions are updated.
(List the `SKIPPED ... code/board mentions it` entries printed by the dry runs.)

## Rules that must not be broken

1. Task reports live at `reports/tasks/<ID>_report.md`. 2. Nothing but task files goes in `delegated_tasks/`. 3. Never use the top-level `archive/` folder (it is git-ignored). 4. Board changes go through the `orchestrate` CLI.
```

---

## 7. Decisions that belong to the human (list these in the final report; do not act on them)

1. `archive/technoblade/` (8 scraped website files) is tracked although `.gitignore` excludes `/archive/`. Suggested action if unwanted: `git rm -r --cached archive/` (keeps local copies).
2. Pinned documents (every `SKIPPED ... code/board mentions it` line). Moving them needs a small edit of code comments or strings, which this job must not do.
3. `evals/retrieval.py` says a contract is "pinned by `reports/critique_2026-08-18.md`", but that file is in `reports/system_reports/Agent Critiques/`, not at that path, so the tool skips it. This was already inconsistent before the sort. Either update the comment or keep a copy at the old path.
4. Files skipped because they were changed in the last 3 days (overnight reports, `docs/OVERNIGHT_*`, `docs/FACT_DEVELOPMENT_SYSTEM.md`, `reports/fact_development_*`, and so on). Re-run this protocol after a quiet week.
5. Awkward names (spaces, em dash, camelCase): `agent_prompts/AI Final Knowledge Handoff Protocol — Preserve Everything Future Agents Need.md`, `docs/iteration improvement ideas.md`, `docs/agentQualityValuation.md`. Renaming is not done by this job.
6. `docs/iteration improvement ideas.md` has about 5,500 lines. Splitting it would change what the task parser reads, so it is out of scope.
7. `delegated_tasks/` has 35 "range" files (for example `D-065-069.md`) next to single-task files. Out of scope (parser).
8. `knowledge/` contains near-duplicate entries (for example K-006 and K-012). They belong to the knowledge-board audit, not to this job.
9. Open task specs in `delegated_tasks/*.md` and the generated token audit still mention the old `reports/<ID>_report.md` paths in their text. They are history and were left unchanged; the code finds both locations.

---

## 8. If something goes wrong

| Symptom | Action |
|---|---|
| `MANIFEST REJECTED` | Nothing was changed. Read the listed lines. If the repository changed since this prompt was written, delete the offending lines from the `.tsv`, re-run `--dry-run`, and mention it in the report. |
| `check` prints `LOST: <path>` or `CONTENT CHANGED` | Stop. Do not "fix" by hand. Run `git status` and `git diff --stat`, report both outputs. |
| `check` prints `NEW BROKEN LINK` | A link was not repaired. Open the named file, correct that one link, run `note-edit` on the file, run `check` again. |
| New failing tests | Section 4, Phase 7. Never edit tests or code. |
| `apply` says a destination is git-ignored | You picked a forbidden folder (probably `archive/`). Choose a folder from section 5. |
| `git rebase` conflicts | `git rebase --abort`, report which files conflicted, wait for the human. |
| Any command not in this document seems necessary | Do not run it. Ask. |

---

## 9. Final report (send to the human; keep it short and plain)

1. Result in one sentence (for example: "232 dead board pointers repaired; 28 duplicate files removed; N files sorted; tests: no new failures").
2. Numbers: files moved, files skipped (with the reason counts), links rewritten, `check` result, test comparison.
3. The branch name and the verified push.
4. The decision list from section 7, filled in with the real file names.
5. What you did **not** do, and why.
