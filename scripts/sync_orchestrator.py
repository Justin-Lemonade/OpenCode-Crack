#!/usr/bin/env python3
"""Two-way orchestrator sync between AI-Brain- and OpenCode-Crack.

Keeps opencode_crack/{orchestrator,runtime,tools,prompts,storage/migrations.py}
(this repo) in sync with the same code's original home in AI-Brain-
(src/{orchestrator,runtime,tools,prompts,storage/migrations.py}), in both
directions, without ever auto-merging anything.

Every sync -- clean or conflicting -- lands as a pull request on the
target repo for a human to review and merge. This is deliberate, not
just a safety default: the two trees have already diverged on purpose
(OpenCode-Crack's tool registry had ~26 AI-Brain-specific tools removed,
its strings were rebranded, and some real bugs were fixed only on this
side) -- a sync in the OpenCode-Crack -> AI-Brain direction could easily
try to reintroduce that divergence into AI-Brain if it were ever
auto-applied. A human has to look at every one.

State (which commit on each side has already been proposed for sync)
lives in .sync/state.json, committed to this repo (OpenCode-Crack) only
-- it is the single source of truth for both directions, so there is
never a question of which side's state file is authoritative.

Usage (run from a checkout of THIS repo, with AI_BRAIN_PATH pointing at
a sibling checkout of AI-Brain-):
    python scripts/sync_orchestrator.py sync --direction ai-brain-to-occ
    python scripts/sync_orchestrator.py sync --direction occ-to-ai-brain
    python scripts/sync_orchestrator.py check-merged

Required environment:
    GH_TOKEN            -- GitHub token with push + PR access to both repos
    AI_BRAIN_PATH        -- path to a checkout of Justin-Lemonade/AI-Brain-
    OCC_PATH              -- path to a checkout of Justin-Lemonade/OpenCode-Crack
                             (defaults to the repo this script lives in)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

AI_BRAIN_REPO = "Justin-Lemonade/AI-Brain-"
OCC_REPO = "Justin-Lemonade/OpenCode-Crack"

# Path mapping is a clean 1:1 rename in both directions -- verified no
# filename divergence exists between the two trees at time of writing.
PATH_MAP_AB_TO_OCC = {
    "src/orchestrator/": "opencode_crack/orchestrator/",
    "src/runtime/": "opencode_crack/runtime/",
    "src/tools/": "opencode_crack/tools/",
    "src/prompts/": "opencode_crack/prompts/",
    "src/storage/migrations.py": "opencode_crack/storage/migrations.py",
    # Matching test files, kept in sync the same way as their sources.
    "tests/test_orchestrator": "tests/test_orchestrator",  # test dir names vary; handled by content-only import remap below
    # Skills and agent prompts -- Markdown only, no import remapping needed.
    # Any new files/subdirectories added inside these directories are picked
    # up automatically by the diff; no change to this map is required.
    ".opencode/skills/": "skills/",
    ".agent_prompts/": "agent_prompts/",
}

# Import-statement remapping. Order matters: longer/more-specific
# patterns first so "src.storage.migrations" doesn't get partially
# matched by a broader "src.storage" rule that doesn't exist here, etc.
IMPORT_MAP_AB_TO_OCC = [
    (r"\bsrc\.orchestrator\b", "opencode_crack.orchestrator"),
    (r"\bsrc\.runtime\b", "opencode_crack.runtime"),
    (r"\bsrc\.tools\b", "opencode_crack.tools"),
    (r"\bsrc\.prompts\b", "opencode_crack.prompts"),
    (r"\bsrc\.storage\.migrations\b", "opencode_crack.storage.migrations"),
    (r"\bfrom src import\b", "from opencode_crack import"),
    (r"\bsrc/orchestrator/", "opencode_crack/orchestrator/"),
    (r"\bsrc/runtime/", "opencode_crack/runtime/"),
    (r"\bsrc/tools/", "opencode_crack/tools/"),
    (r"\bsrc/prompts/", "opencode_crack/prompts/"),
    (r"\bsrc/storage/migrations\.py\b", "opencode_crack/storage/migrations.py"),
    (r"\bpython -m src\.main orchestrate\b", "orchestrate"),
]

# The reverse direction is the exact inverse of the above.
PATH_MAP_OCC_TO_AB = {v: k for k, v in PATH_MAP_AB_TO_OCC.items() if "test_orchestrator" not in v}
IMPORT_MAP_OCC_TO_AB = [
    (r"\bopencode_crack\.orchestrator\b", "src.orchestrator"),
    (r"\bopencode_crack\.runtime\b", "src.runtime"),
    (r"\bopencode_crack\.tools\b", "src.tools"),
    (r"\bopencode_crack\.prompts\b", "src.prompts"),
    (r"\bopencode_crack\.storage\.migrations\b", "src.storage.migrations"),
    (r"\bfrom opencode_crack import\b", "from src import"),
    (r"\bopencode_crack/orchestrator/", "src/orchestrator/"),
    (r"\bopencode_crack/runtime/", "src/runtime/"),
    (r"\bopencode_crack/tools/", "src/tools/"),
    (r"\bopencode_crack/prompts/", "src/prompts/"),
    (r"\bopencode_crack/storage/migrations\.py\b", "src/storage/migrations.py"),
    (r"\borchestrate\b(?!-)", "python -m src.main orchestrate"),
]

STATE_PATH = Path(__file__).resolve().parent.parent / ".sync" / "state.json"

DEFAULT_STATE = {
    "ai_brain_synced_sha": None,
    "occ_synced_sha": None,
    "pending": {
        "ai-brain-to-occ": None,  # {"pr_number": int, "branch": str, "head_sha": str}
        "occ-to-ai-brain": None,
    },
}


def run(cmd: list[str], cwd: str | Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return json.loads(json.dumps(DEFAULT_STATE))  # deep copy


def save_state(state: dict, occ_path: Path, commit_message: str) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    run(["git", "add", str(STATE_PATH.relative_to(occ_path))], cwd=occ_path)
    diff = run(["git", "diff", "--cached", "--quiet"], cwd=occ_path, check=False)
    if diff.returncode == 0:
        return  # nothing actually changed
    run(["git", "commit", "-m", commit_message], cwd=occ_path)
    run(["git", "push", "origin", "HEAD:main"], cwd=occ_path)


def gh_api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "opencode-crack-sync-script")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {method} {path} failed: {e.code} {e.read().decode()}")


def check_pr_merged(repo: str, pr_number: int, token: str) -> tuple[bool, str | None]:
    pr = gh_api("GET", f"/repos/{repo}/pulls/{pr_number}", token)
    if pr.get("merged"):
        return True, pr.get("merge_commit_sha")
    if pr.get("state") == "closed" and not pr.get("merged"):
        # Closed without merging -- treat as resolved-without-sync; caller
        # decides what that means (we do NOT auto-advance state here).
        return False, "closed_unmerged"
    return False, None


def remap_patch_text(patch_text: str, path_map: dict, import_map: list[tuple[str, str]]) -> str:
    text = patch_text
    for old, new in path_map.items():
        text = text.replace(old, new)
    for pattern, repl in import_map:
        text = re.sub(pattern, repl, text)
    return text


def get_diff_paths(direction: str) -> list[str]:
    if direction == "ai-brain-to-occ":
        return [
            "src/orchestrator/",
            "src/runtime/",
            "src/tools/",
            "src/prompts/",
            "src/storage/migrations.py",
            ".opencode/skills/",
            ".agent_prompts/",
        ]
    return [
        "opencode_crack/orchestrator/",
        "opencode_crack/runtime/",
        "opencode_crack/tools/",
        "opencode_crack/prompts/",
        "opencode_crack/storage/migrations.py",
        "skills/",
        "agent_prompts/",
    ]


def do_sync(direction: str, ai_brain_path: Path, occ_path: Path, token: str) -> None:
    state = load_state()
    source_repo = AI_BRAIN_REPO if direction == "ai-brain-to-occ" else OCC_REPO
    target_repo = OCC_REPO if direction == "ai-brain-to-occ" else AI_BRAIN_REPO
    source_path = ai_brain_path if direction == "ai-brain-to-occ" else occ_path
    target_path = occ_path if direction == "ai-brain-to-occ" else ai_brain_path
    state_key = "ai_brain_synced_sha" if direction == "ai-brain-to-occ" else "occ_synced_sha"
    path_map = PATH_MAP_AB_TO_OCC if direction == "ai-brain-to-occ" else PATH_MAP_OCC_TO_AB
    import_map = IMPORT_MAP_AB_TO_OCC if direction == "ai-brain-to-occ" else IMPORT_MAP_OCC_TO_AB

    pending = state["pending"].get(direction)
    if pending:
        merged, merge_sha = check_pr_merged(target_repo, pending["pr_number"], token)
        if merged:
            print(f"[{direction}] PR #{pending['pr_number']} merged -- advancing synced SHA.")
            state[state_key] = pending["head_sha"]
            state["pending"][direction] = None
            save_state(state, occ_path, f"sync: advance {state_key} after PR #{pending['pr_number']} merged")
        elif merge_sha == "closed_unmerged":
            print(f"[{direction}] PR #{pending['pr_number']} was closed without merging. "
                  f"NOT advancing synced SHA -- those commits will be re-proposed next run "
                  f"unless a human resolves this. Clearing pending marker.")
            state["pending"][direction] = None
            save_state(state, occ_path, f"sync: clear stale pending marker for {direction} (PR closed unmerged)")
        else:
            print(f"[{direction}] PR #{pending['pr_number']} still open -- skipping, no new PR this run.")
            return

    run(["git", "fetch", "origin"], cwd=source_path)
    run(["git", "checkout", "main"], cwd=source_path, check=False)
    run(["git", "reset", "--hard", "origin/main"], cwd=source_path)
    source_head = run(["git", "rev-parse", "HEAD"], cwd=source_path).stdout.strip()

    synced_sha = state.get(state_key)
    if synced_sha is None:
        print(f"[{direction}] No baseline synced SHA recorded -- initializing to current HEAD "
              f"without generating a sync (first run should be seeded explicitly, not diffed "
              f"from repo creation).")
        state[state_key] = source_head
        save_state(state, occ_path, f"sync: initialize {state_key} baseline")
        return

    if synced_sha == source_head:
        print(f"[{direction}] Already up to date ({source_head[:10]}).")
        return

    diff_paths = get_diff_paths(direction)
    log = run(["git", "log", f"{synced_sha}..{source_head}", "--oneline", "--", *diff_paths], cwd=source_path)
    if not log.stdout.strip():
        print(f"[{direction}] No relevant changes in {diff_paths} between "
              f"{synced_sha[:10]} and {source_head[:10]}.")
        state[state_key] = source_head
        save_state(state, occ_path, f"sync: advance {state_key} (no relevant changes)")
        return

    print(f"[{direction}] Relevant commits since last sync:\n{log.stdout}")

    diff = run(["git", "diff", f"{synced_sha}..{source_head}", "--", *diff_paths], cwd=source_path)
    if not diff.stdout.strip():
        state[state_key] = source_head
        save_state(state, occ_path, f"sync: advance {state_key} (empty diff)")
        return

    remapped_patch = remap_patch_text(diff.stdout, path_map, import_map)

    branch = f"sync/{direction}-{source_head[:10]}"
    run(["git", "fetch", "origin"], cwd=target_path)
    run(["git", "checkout", "main"], cwd=target_path, check=False)
    run(["git", "reset", "--hard", "origin/main"], cwd=target_path)
    run(["git", "checkout", "-b", branch], cwd=target_path)

    patch_file = target_path / ".sync-incoming.patch"
    patch_file.write_text(remapped_patch)

    apply_result = run(["git", "apply", "--reject", "--whitespace=fix", str(patch_file.name)],
                        cwd=target_path, check=False)
    patch_file.unlink()

    conflict = apply_result.returncode != 0
    rej_files = run(["git", "status", "--porcelain"], cwd=target_path).stdout
    has_rejects = ".rej" in rej_files

    run(["git", "add", "-A"], cwd=target_path)
    commit_status = run(["git", "diff", "--cached", "--quiet"], cwd=target_path, check=False)
    if commit_status.returncode == 0:
        print(f"[{direction}] Patch produced no changes to commit (already applied?). Skipping PR.")
        run(["git", "checkout", "main"], cwd=target_path)
        run(["git", "branch", "-D", branch], cwd=target_path, check=False)
        state[state_key] = source_head
        save_state(state, occ_path, f"sync: advance {state_key} (patch was a no-op on target)")
        return

    commit_msg = f"sync({direction}): {source_head[:10]} -- {log.stdout.strip().splitlines()[0]}"
    if conflict or has_rejects:
        commit_msg = "[CONFLICT] " + commit_msg
    run(["git", "commit", "-m", commit_msg], cwd=target_path)
    run(["git", "push", "-u", "origin", branch, "--force"], cwd=target_path)

    pr_title = f"{'[CONFLICT] ' if conflict or has_rejects else ''}Sync from {source_repo} @ {source_head[:10]}"
    pr_body = (
        f"Automated two-way orchestrator sync ({direction}).\n\n"
        f"Source commits ({synced_sha[:10]}..{source_head[:10]}):\n\n```\n{log.stdout.strip()}\n```\n\n"
        + (
            "**This patch did not apply cleanly.** Check for `.rej` files in this branch "
            "(if `git apply --reject` produced them) and resolve manually before merging.\n\n"
            if (conflict or has_rejects) else
            "Patch applied cleanly. Review the diff -- this repo's copy may have diverged "
            "on purpose (removed AI-Brain-specific behavior, renamed strings, independent "
            "bug fixes) and this PR could be proposing to undo that. **Never auto-merge.**\n\n"
        )
        + "Merging this PR is what advances the sync state -- nothing here is applied "
        "automatically regardless of whether it looks clean."
    )
    pr = gh_api("POST", f"/repos/{target_repo}/pulls", token, {
        "title": pr_title,
        "head": branch,
        "base": "main",
        "body": pr_body,
    })
    print(f"[{direction}] Opened PR #{pr['number']}: {pr['html_url']}")

    state["pending"][direction] = {
        "pr_number": pr["number"],
        "branch": branch,
        "head_sha": source_head,
    }
    save_state(state, occ_path, f"sync: record pending PR #{pr['number']} for {direction}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sync_p = sub.add_parser("sync")
    sync_p.add_argument("--direction", choices=["ai-brain-to-occ", "occ-to-ai-brain"], required=True)

    sub.add_parser("check-merged")
    sub.add_parser("init-baseline")

    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN")
    if not token:
        print("GH_TOKEN environment variable is required.", file=sys.stderr)
        sys.exit(1)
    ai_brain_path = Path(os.environ.get("AI_BRAIN_PATH", "../AI-Brain-")).resolve()
    occ_path = Path(os.environ.get("OCC_PATH", str(Path(__file__).resolve().parent.parent))).resolve()

    if not ai_brain_path.exists():
        print(f"AI_BRAIN_PATH does not exist: {ai_brain_path}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "sync":
        do_sync(args.direction, ai_brain_path, occ_path, token)
    elif args.cmd == "check-merged":
        do_sync("ai-brain-to-occ", ai_brain_path, occ_path, token)
        do_sync("occ-to-ai-brain", ai_brain_path, occ_path, token)
    elif args.cmd == "init-baseline":
        state = load_state()
        ab_head = run(["git", "rev-parse", "HEAD"], cwd=ai_brain_path).stdout.strip()
        occ_head = run(["git", "rev-parse", "HEAD"], cwd=occ_path).stdout.strip()
        state["ai_brain_synced_sha"] = ab_head
        state["occ_synced_sha"] = occ_head
        save_state(state, occ_path, f"sync: seed baseline (AI-Brain {ab_head[:10]}, OCC {occ_head[:10]})")
        print(f"Seeded baseline: AI-Brain {ab_head[:10]}, OCC {occ_head[:10]}")


if __name__ == "__main__":
    main()
