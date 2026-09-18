# Orchestrator sync — setup

This repo and `Justin-Lemonade/AI-Brain-` two-way sync the orchestrator/
runtime/tools/prompts/storage.migrations code via
`.github/workflows/sync-with-ai-brain.yml`, which runs daily (13:00 UTC)
and on manual trigger. Full design/rationale is documented at the top of
`scripts/sync_orchestrator.py`.

**Nothing merges automatically, in either direction, ever.** Every sync
— whether the patch applied cleanly or hit a conflict — lands as a pull
request on the target repo for you to review and merge yourself. A
merged PR is what advances the sync state; an unmerged one just sits
there and blocks new PRs for that direction until you deal with it.

## One-time setup

The workflow needs two repo secrets on **OpenCode-Crack**
(Settings → Secrets and variables → Actions → New repository secret):

| Secret name | Value |
|---|---|
| `SYNC_APP_ID` | `4591705` (the `claude-s-plan` GitHub App's ID) |
| `SYNC_APP_PRIVATE_KEY` | Full contents of the App's `.pem` private key file |

The App's installation already has access to both `OpenCode-Crack` and
`AI-Brain-` ("All repositories" on your account), so no separate
installation-ID secret is needed — the workflow requests a token scoped
to just those two repos at run time.

## First run

Once the secrets are set, trigger it manually once (Actions tab →
"Sync orchestrator with AI-Brain-" → "Run workflow") rather than waiting
for the next scheduled run, to confirm the plumbing works.

**Expected result of that first run: a clean no-op.** `.sync/state.json`
is already seeded to today's HEAD commit on both repos, so the first run
should just print `Already up to date` for both directions and exit —
that's the successful outcome, not a sign nothing happened. It's testing
authentication and checkout, not a real sync, since there's nothing new
to sync yet.

Only commits made **after** the seed point will ever be proposed for
sync. The pre-existing divergence between the two trees (AI-Brain's
hardwired tool registry vs. OpenCode-Crack's cleaned-up one, rebranded
strings, a few bug fixes made only on the OpenCode-Crack side) was a
deliberate, already-reviewed decision — it is not retroactively
proposed as a "sync" in either direction.

## What a sync PR looks like

- Title: `Sync from <repo> @ <short-sha>`, or `[CONFLICT] Sync from
  <repo> @ <short-sha>` if `git apply --reject` couldn't apply the
  whole patch cleanly (look for `.rej` files in the PR's branch).
- Body: the exact source commit range and one-line log, plus a reminder
  that a clean-looking patch can still be proposing to undo a
  deliberate divergence — read the diff, don't just check for merge
  conflicts.
- Merging it is the only thing that advances `.sync/state.json`'s
  tracked SHA for that direction. Closing it without merging leaves
  those commits queued for the next run to re-propose (the workflow
  logs this explicitly rather than silently dropping them).

## If something looks stuck

Check `.sync/state.json` in this repo — `pending.ai-brain-to-occ` or
`pending.occ-to-ai-brain` holds the open PR number blocking new syncs
for that direction. Merge or close that PR and the next run un-blocks
itself.
