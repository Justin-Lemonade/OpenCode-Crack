"""
Argument parsing for `python -m src.main orchestrate ...`.

Kept in its own module (rather than bloating main.py) since it has enough
subcommands/flags to warrant argparse, while main.py's top-level commands
stay simple if/elif dispatch by design.
"""
import argparse
import sys

from opencode_crack.orchestrator import task_board, concerns_board, knowledge_board, repo_search
from opencode_crack.orchestrator.delegator import decide, build_contract, build_compact_contract, build_compact_contract_json
from opencode_crack.orchestrator.dispatcher import dispatch


def _ensure_unicode_safe_console() -> None:
    """Reconfigure stdout/stderr so printing task titles (which may contain
    characters like U+2192) can never crash the orchestrator CLI with a
    UnicodeEncodeError on consoles whose codepage can't encode them (e.g.
    cp1251). Same pattern as src/main.py's _make_console_output_unicode_safe();
    duplicated here because run_orchestrate is also invoked directly (e.g.
    via `python -c`), bypassing main(). Best-effort: no-op when the stream
    does not support reconfigure (e.g. test capture)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _print_task(t) -> None:
    assignee = f" ({t.state.assignee})" if t.state.assignee else ""
    print(f"{t.id:8} [{t.tier:14}] {t.state.status:12}{assignee}  {t.priority:12}  {t.title}")


def _print_knowledge(k) -> None:
    print(f"{k.id:8} {k.category:12} {k.state.status:16} {k.submitted_by:16} {k.title}")


def run_orchestrate(argv: list[str]) -> None:

    _ensure_unicode_safe_console()
    parser = argparse.ArgumentParser(prog="orchestrate", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List tasks from the task board")
    p_list.add_argument("--tier", choices=["human", "delegate", "primary_claude"])
    p_list.add_argument("--status", choices=sorted(task_board.VALID_STATUSES))

    sub.add_parser("status", help="Print the STATUS.md dashboard (what's active, stale, blocked, left)")

    p_briefing = sub.add_parser(
        "briefing", help="Print a compact startup briefing for delegated agents"
    )
    p_briefing.add_argument("--json", action="store_true", help="Print the briefing as JSON")

    p_claim = sub.add_parser("claim", help="Claim a task for an agent")
    p_claim.add_argument("task_id")
    p_claim.add_argument("--agent", required=True, help="Name identifying the claiming agent")

    p_start = sub.add_parser("start", help="Mark a claimed task in_progress (owner only)")
    p_start.add_argument("task_id")
    p_start.add_argument("--agent", required=True,
                         help="Must be the exact agent string from claim (D-363 ownership)")

    p_submit = sub.add_parser("submit", help="Submit finished work for review (does NOT mark done)")
    p_submit.add_argument("task_id")
    p_submit.add_argument("--notes", required=True, help="Summary of what was done")
    p_submit.add_argument("--report", default=None, help="Path to a written report, e.g. reports/D-007_report.md")
    p_submit.add_argument("--agent", required=True,
                          help="Must be the exact agent string from claim (D-363 ownership)")

    p_approve = sub.add_parser("approve", help="Confirm reviewed work and mark done (Primary Claude/human only)")
    p_approve.add_argument("task_id")
    p_approve.add_argument("--notes", default="")

    p_reject = sub.add_parser("reject", help="Send submitted work back to open with feedback")
    p_reject.add_argument("task_id")
    p_reject.add_argument("--notes", required=True, help="What needs to change before resubmitting")

    p_complete = sub.add_parser("complete", help="Mark a task done directly (Primary Claude's own work only — delegated agents should use 'submit' instead)")
    p_complete.add_argument("task_id")
    p_complete.add_argument("--notes", default="")

    p_release = sub.add_parser("release", help="Give up a claim without completing it")
    p_release.add_argument("task_id")
    p_release.add_argument("--notes", default="")
    p_release.add_argument("--agent", default=None,
                           help="Claiming agent string; required unless --force")
    p_release.add_argument("--force", action="store_true",
                           help="Override: stale takeover / Primary Claude / human (audit-logged)")

    p_block = sub.add_parser("block", help="Mark a task blocked, with a reason")
    p_block.add_argument("task_id")
    p_block.add_argument("--notes", required=True)
    p_block.add_argument("--agent", default=None,
                         help="Claiming agent string; required when the task is active")

    p_contract = sub.add_parser("contract", help="Print the delegation contract for a task")
    p_contract.add_argument("task_id")
    p_contract.add_argument("--compact", action="store_true",
                            help="Print the compact contract form (execution-critical fields only)")
    p_contract.add_argument("--json", action="store_true",
                            help="Print the compact contract form as JSON")

    p_dispatch = sub.add_parser("dispatch", help="Send a task's contract to a backend")
    p_dispatch.add_argument("task_id")
    p_dispatch.add_argument("--backend", default="manual", choices=["manual", "ollama", "swarm", "opencode"])
    p_dispatch.add_argument("--model", default="llama3.1")
    p_dispatch.add_argument("--manager", help="Registered manager agent ID (required for swarm/opencode backend)")
    p_dispatch.add_argument("--worker", help="Registered worker agent ID (required for swarm/opencode backend)")
    p_dispatch.add_argument("--tester", help="Registered tester agent ID (required for swarm/opencode backend)")

    p_report_json = sub.add_parser(
        "report-json", help="Emit or validate the machine-readable agent report (D-153)"
    )
    p_report_json.add_argument("task_id")
    p_report_json.add_argument("--check", action="store_true",
                               help="Validate an existing reports/<TASK_ID>_report.json instead of writing")
    p_report_json.add_argument("--outcome", default="submitted",
                               choices=["submitted", "blocked", "released"])
    p_report_json.add_argument("--files", default="", help="Comma-separated list of files changed")
    p_report_json.add_argument("--tests", default="", help="Comma-separated list of tests run")
    p_report_json.add_argument("--failures", default="", help="Comma-separated list of failures")
    p_report_json.add_argument("--blockers", default="", help="Comma-separated list of blockers")
    p_report_json.add_argument("--confidence", default="medium",
                               choices=["high", "medium", "low"])
    p_report_json.add_argument("--source", default="inspection",
                               choices=["tests", "inspection", "manual"])
    p_report_json.add_argument("--git-state", default="committed locally, not pushed",
                               choices=["pushed in commit", "committed locally, not pushed",
                                        "working tree only, no commit"])

    p_swarm = sub.add_parser("swarm", help="Run one task through the manager -> worker -> tester OpenCode Swarm loop")
    p_swarm.add_argument("task_id")
    p_swarm.add_argument("--manager", required=True, help="Registered manager agent ID")
    p_swarm.add_argument("--worker", required=True, help="Registered worker agent ID")
    p_swarm.add_argument("--tester", required=True, help="Registered tester agent ID")
    p_swarm.add_argument("--timeout", type=int, default=3600)
    p_swarm.add_argument("--max-concurrent", type=int, default=3)
    p_swarm.add_argument("--budget-usd", type=float, default=None)

    p_search = sub.add_parser(
        "search",
        help="Repo-wide search over Markdown docs (tasks, reports, concerns, "
             "docs/, .agent_prompts/) -- NOT the knowledge base; use "
             "'orchestrate knowledge search' for KNOW-*.md entries",
    )
    p_search.add_argument("query", help="Search terms")
    p_search.add_argument("--path", default=None,
                          help="Only search files under this path prefix, e.g. delegated_tasks/")
    p_search.add_argument("--limit", type=int, default=10, help="Max results")
    p_search.add_argument("--json", action="store_true", help="Output as JSON")

    p_concern = sub.add_parser(
        "concern", help="File or manage concerns on the concerns board (see concerns/README.md)"
    )
    concern_sub = p_concern.add_subparsers(dest="concern_action", required=True)

    c_file = concern_sub.add_parser("file", help="File a new concern")
    c_file.add_argument("--title", required=True)
    c_file.add_argument("--body", required=True, help="Full description of the concern")
    c_file.add_argument("--severity", required=True, choices=["blocker", "high", "medium", "low"])
    c_file.add_argument("--raised-by", required=True, help="Name identifying the filing agent")
    c_file.add_argument("--category", default="other",
                        choices=["process", "board-integrity", "technical", "environment", "quality", "other"])
    c_file.add_argument("--related-task", default=None, help="e.g. D-259, if this concern is about a specific task")

    c_list = concern_sub.add_parser("list", help="List concerns")
    c_list.add_argument("--status", choices=sorted(concerns_board.VALID_STATUSES))
    c_list.add_argument("--severity", choices=["blocker", "high", "medium", "low"])

    c_show = concern_sub.add_parser("show", help="Print the full write-up for one concern")
    c_show.add_argument("concern_id")

    c_board = concern_sub.add_parser("board", help="Print the CONCERNS_BOARD.md dashboard")

    c_ack = concern_sub.add_parser("acknowledge", help="Confirm a concern has been seen (open -> acknowledged)")
    c_ack.add_argument("concern_id")
    c_ack.add_argument("--notes", default="")

    c_resolve = concern_sub.add_parser("resolve", help="Mark a concern addressed")
    c_resolve.add_argument("concern_id")
    c_resolve.add_argument("--notes", required=True, help="What was done about it")

    c_dismiss = concern_sub.add_parser("dismiss", help="Acknowledge but decide not to act on a concern")
    c_dismiss.add_argument("concern_id")
    c_dismiss.add_argument("--notes", required=True, help="Why it won't be actioned")

    c_reopen = concern_sub.add_parser("reopen", help="Send a resolved/wontfix concern back to open")
    c_reopen.add_argument("concern_id")
    c_reopen.add_argument("--notes", default="")

    concern_sub.add_parser("sync", help="Re-scan concerns/*.md and regenerate the board")

    # --- Knowledge board (mirrors concern block, D-315) ---
    from opencode_crack.orchestrator.knowledge_parser import VALID_CATEGORIES as _KB_VALID_CATEGORIES
    p_knowledge = sub.add_parser(
        "knowledge", help="File or manage knowledge entries on the knowledge board"
    )
    knowledge_sub = p_knowledge.add_subparsers(dest="knowledge_action", required=True)

    k_file = knowledge_sub.add_parser("file", help="File a new knowledge entry")
    k_file.add_argument("--title", required=True)
    k_file.add_argument("--body", required=True, help="Full description of the knowledge")
    k_file.add_argument("--category", required=True, choices=sorted(_KB_VALID_CATEGORIES))
    k_file.add_argument("--submitted-by", required=True, help="Name identifying the filing agent")
    k_file.add_argument("--tags", default="", help="Comma-separated tags, e.g. a,b,c")
    k_file.add_argument("--related-task", default=None, help="e.g. D-259, if this knowledge is about a specific task")
    k_file.add_argument("--supersedes", default=None, help="e.g. K-001, if this supersedes another entry")

    k_list = knowledge_sub.add_parser("list", help="List knowledge entries")
    k_list.add_argument("--status", choices=sorted(knowledge_board.VALID_STATUSES))
    k_list.add_argument("--category", choices=sorted(_KB_VALID_CATEGORIES))
    k_list.add_argument("--tags", default="", help="Comma-separated tags filter")

    k_show = knowledge_sub.add_parser("show", help="Print the full write-up for one knowledge entry")
    k_show.add_argument("knowledge_id")

    k_verify_agent = knowledge_sub.add_parser("verify-agent", help="Agent self-verification: unverified -> agent-verified")
    k_verify_agent.add_argument("knowledge_id")
    k_verify_agent.add_argument("--verified-by", required=True, help="Name identifying the verifying agent")
    k_verify_agent.add_argument("--verified-how", required=True, help="What was actually checked")

    k_verify_reviewer = knowledge_sub.add_parser("verify-reviewer", help="Reviewer verification: agent-verified -> reviewer-verified")
    k_verify_reviewer.add_argument("knowledge_id")
    k_verify_reviewer.add_argument("--verified-by", required=True, help="Name identifying the reviewer")
    k_verify_reviewer.add_argument("--notes", default="", help="Optional notes")

    k_confirm = knowledge_sub.add_parser("confirm", help="Promote a reviewer-verified entry to confirmed")
    k_confirm.add_argument("knowledge_id")
    k_confirm.add_argument("--confirmed-by", required=True, help="Name identifying the confirmer")
    k_confirm.add_argument("--notes", default="", help="Optional notes")

    k_useful = knowledge_sub.add_parser("mark-useful", help="Increment useful_count for a knowledge entry")
    k_useful.add_argument("knowledge_id")

    k_supersede = knowledge_sub.add_parser("supersede", help="Mark an entry as superseded by another")
    k_supersede.add_argument("knowledge_id")
    k_supersede.add_argument("--with", dest="with_id", required=True, help="New entry K-### that supersedes the old one")
    k_supersede.add_argument("--notes", default="", help="Optional notes")

    k_retract = knowledge_sub.add_parser("retract", help="Retract an entry")
    k_retract.add_argument("knowledge_id")
    k_retract.add_argument("--notes", required=True, help="Why it was wrong")

    knowledge_sub.add_parser("sync", help="Re-scan knowledge/*.md and regenerate the board")

    # --- Durable knowledge search (KNOW-*.md entries) ---
    k_search = knowledge_sub.add_parser("search", help="Search durable knowledge entries (KNOW-*.md)")
    k_search.add_argument("--query", default="", help="Search terms (keywords)")
    k_search.add_argument("--tags", default="", help="Comma-separated tags filter")
    k_search.add_argument("--type", choices=["project", "architecture", "decision", "blocker", "learning", "task"], help="Filter by knowledge type")
    k_search.add_argument("--task", default=None, help="Filter by task ID")
    k_search.add_argument("--file", default=None, help="Filter by file path")
    k_search.add_argument("--after", default=None, help="ISO date: only return entries created after this date")
    k_search.add_argument("--limit", type=int, default=10, help="Max results")
    k_search.add_argument("--status", default=None,
                          choices=["active", "superseded", "resolved", "retracted"],
                          help="Filter by status (default: exclude superseded/retracted)")
    k_search.add_argument("--include-superseded", action="store_true",
                          help="Include superseded entries in results")
    k_search.add_argument("--include-retracted", action="store_true",
                          help="Include retracted entries in results")
    k_search.add_argument("--json", action="store_true", help="Output as JSON")

    k_show_durable = knowledge_sub.add_parser("durable-show", help="Show a durable knowledge entry by ID")
    k_show_durable.add_argument("knowledge_id", help="The KNOW-YYYYMMDD-NNN ID")

    k_durable_file = knowledge_sub.add_parser(
        "durable-file", help="Create a durable knowledge entry (KNOW-YYYYMMDD-NNN)")
    k_durable_file.add_argument("--type", required=True,
                                choices=["project", "architecture", "decision", "blocker", "learning", "task"])
    k_durable_file.add_argument("--title", required=True)
    k_durable_file.add_argument("--body", required=True, help="Summary/finding body (Markdown ok)")
    k_durable_file.add_argument("--tags", default="", help="Comma-separated tags")
    k_durable_file.add_argument("--keywords", default="", help="Comma-separated keywords")
    k_durable_file.add_argument("--task", default="", help="Comma-separated related task IDs")
    k_durable_file.add_argument("--file", default="", help="Comma-separated related file paths")
    k_durable_file.add_argument("--related", default="", help="Comma-separated related KNOW IDs")
    k_durable_file.add_argument("--priority", default="medium",
                                choices=["critical", "high", "medium", "low"])
    k_durable_file.add_argument("--confidence", default="medium",
                                choices=["high", "medium", "low"])
    k_durable_file.add_argument("--agent", default="", help="Filing agent name")

    k_durable_list = knowledge_sub.add_parser("durable-list", help="List durable knowledge entries")
    k_durable_list.add_argument("--type", default=None,
                                choices=["project", "architecture", "decision", "blocker", "learning", "task"])
    k_durable_list.add_argument("--tag", default=None)
    k_durable_list.add_argument("--task", default=None)
    k_durable_list.add_argument("--status", default=None,
                                choices=["active", "superseded", "resolved", "retracted"])
    k_durable_list.add_argument("--limit", type=int, default=100)

    k_durable_supersede = knowledge_sub.add_parser(
        "durable-supersede", help="Mark a durable entry superseded by another")
    k_durable_supersede.add_argument("knowledge_id")
    k_durable_supersede.add_argument("--with", dest="with_id", required=True)
    k_durable_supersede.add_argument("--notes", default="")

    k_durable_retract = knowledge_sub.add_parser("durable-retract", help="Retract a durable entry")
    k_durable_retract.add_argument("knowledge_id")
    k_durable_retract.add_argument("--notes", required=True)

    k_durable_resolve = knowledge_sub.add_parser(
        "durable-resolve", help="Mark a durable entry (typically a blocker) resolved")
    k_durable_resolve.add_argument("knowledge_id")
    k_durable_resolve.add_argument("--notes", default="")
    k_durable_useful = knowledge_sub.add_parser(
        "durable-useful", help="Mark a durable entry useful (it helped real work)")
    k_durable_useful.add_argument("knowledge_id")

    args = parser.parse_args(argv)

    if args.action == "list":
        tasks = task_board.list_tasks(tier=args.tier, status=args.status)
        for t in tasks:
            _print_task(t)
        print(f"\n{len(tasks)} task(s)")

    elif args.action == "status":
        from opencode_crack.orchestrator.status_report import render_markdown
        tasks = task_board.list_tasks()
        print(render_markdown(tasks))

    elif args.action == "briefing":
        from opencode_crack.orchestrator.briefing import build_briefing, build_briefing_json
        tasks = task_board.list_tasks()
        print(build_briefing_json(tasks) if args.json else build_briefing(tasks))

    elif args.action == "claim":
        try:
            t = task_board.claim_task(args.task_id, args.agent)
            print(f"Claimed {t.id} for {args.agent}.")
        except (ValueError, RuntimeError) as e:
            print(f"Could not claim {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "start":
        try:
            t = task_board.start_task(args.task_id, args.agent)
            print(f"{t.id} -> in_progress")
        except (ValueError, RuntimeError) as e:
            print(f"Could not start {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "submit":
        try:
            t = task_board.submit_for_review(args.task_id, notes=args.notes, report_path=args.report, agent=args.agent)
            print(f"{t.id} -> review (awaiting Primary Claude/human approval)")
        except (ValueError, RuntimeError) as e:
            print(f"Could not submit {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "approve":
        try:
            t = task_board.approve_task(args.task_id, notes=args.notes)
            print(f"{t.id} -> done (approved)")
        except (ValueError, RuntimeError) as e:
            print(f"Could not approve {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "reject":
        try:
            t = task_board.reject_task(args.task_id, notes=args.notes)
            print(f"{t.id} -> open (rejected, ready to be re-claimed with feedback in notes)")
        except (ValueError, RuntimeError) as e:
            print(f"Could not reject {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "complete":
        try:
            t = task_board.complete_task(args.task_id, notes=args.notes)
            print(f"{t.id} -> done")
        except (ValueError, RuntimeError) as e:
            print(f"Could not complete {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "release":
        try:
            t = task_board.release_task(args.task_id, notes=args.notes, agent=args.agent, override=args.force)
            print(f"{t.id} -> open (released)")
        except (ValueError, RuntimeError) as e:
            print(f"Could not release {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "block":
        try:
            t = task_board.block_task(args.task_id, notes=args.notes, agent=args.agent)
            print(f"{t.id} -> blocked: {args.notes}")
        except (ValueError, RuntimeError) as e:
            print(f"Could not block {args.task_id}: {e}")
            sys.exit(1)

    elif args.action == "contract":
        t = task_board.get_task(args.task_id)
        if t is None:
            print(f"No such task: {args.task_id}")
            sys.exit(1)
        if args.json:
            print(build_compact_contract_json(t))
        else:
            decision = decide(t)
            print(f"# Delegation decision: {'DELEGATE' if decision.should_delegate else 'DO NOT DELEGATE'}")
            print(f"# Reason: {decision.reason}\n")
            if args.compact:
                print(build_compact_contract(t))
            else:
                print(build_contract(t))
        # USE step: the contract was handed to an agent — record retrieval
        # for the injected entries (sidecar only, no git).
        try:
            from opencode_crack.orchestrator.delegator import _get_relevant_knowledge_for_task
            from opencode_crack.orchestrator import durable_knowledge as dk
            for scored in _get_relevant_knowledge_for_task(t):
                dk.record_entry_retrieval(scored.entry.id)
        except Exception:
            pass

    elif args.action == "dispatch":
        t = task_board.get_task(args.task_id)
        if t is None:
            print(f"No such task: {args.task_id}")
            sys.exit(1)
        decision = decide(t)
        if not decision.should_delegate:
            print(f"Refusing to dispatch {t.id}: {decision.reason}")
            sys.exit(1)
        # Pass swarm/opencode backend arguments if provided
        kwargs = {}
        if args.backend in {"swarm", "opencode"}:
            for arg in ("manager", "worker", "tester"):
                if getattr(args, arg):
                    kwargs[arg + "_id"] = getattr(args, arg)
        result = dispatch(t, backend=args.backend, model=args.model, **kwargs)
        print(result)

    elif args.action == "report-json":
        from opencode_crack.orchestrator.report_schema import (
            build_report,
            report_json_path,
            validate_report_file,
            write_report_json,
        )

        t = task_board.get_task(args.task_id)
        if t is None:
            print(f"No such task: {args.task_id}")
            sys.exit(1)
        if args.check:
            problems = validate_report_file(report_json_path(t.id))
            if problems:
                print(f"{t.id}: invalid report JSON:\n- " + "\n- ".join(problems))
                sys.exit(1)
            print(f"{t.id}: report JSON is schema-valid.")
            return
        report = build_report(
            task_id=t.id,
            outcome=args.outcome,
            files_changed=[s.strip() for s in args.files.split(",") if s.strip()],
            tests=[s.strip() for s in args.tests.split(",") if s.strip()],
            failures=[s.strip() for s in args.failures.split(",") if s.strip()],
            blockers=[s.strip() for s in args.blockers.split(",") if s.strip()],
            confidence=args.confidence,
            source=args.source,
            git_state=args.git_state,
        )
        path = write_report_json(t.id, report)
        print(f"Wrote {path}")

    elif args.action == "swarm":
        from opencode_crack.runtime import control_db
        from opencode_crack.runtime.agent_profile import AgentProfile
        from opencode_crack.orchestrator.manager_loop import ManagerLoop

        profiles = []
        for agent_id in (args.manager, args.worker, args.tester):
            profile = control_db.get_agent(agent_id)
            if profile is None:
                print(f"No registered runtime agent: {agent_id}")
                sys.exit(1)
            profiles.append(profile)

        result = ManagerLoop().run_once(
            args.task_id, profiles[0], profiles[1], profiles[2],
            timeout_seconds=args.timeout,
            max_concurrent=args.max_concurrent,
            budget_usd=args.budget_usd,
        )
        if result.error:
            print(f"Swarm failed for {args.task_id}: {result.error}")
            sys.exit(1)
        swarm_id = result.swarm.swarm_id if result.swarm else None
        print(f"Swarm completed for {args.task_id}: {swarm_id} ({result.event_count} events ingested)")

    elif args.action == "search":
        results = repo_search.search(args.query, path_prefix=args.path, limit=args.limit)
        if args.json:
            import json
            print(json.dumps(results, indent=2))
        elif not results:
            print("No matching documents found.")
        else:
            for r in results:
                print(f"{r['path']}  (score={r['score']:.1f})  {r['title']}")
                print(f"    {r['snippet']}")
            print(f"\n{len(results)} result(s)")

    elif args.action == "concern":
        _run_concern(args)

    elif args.action == "knowledge":
        _run_knowledge(args)


def _print_concern(c) -> None:
    icon = concerns_report_icon(c.severity)
    related = f" [{c.related_task}]" if c.related_task else ""
    print(f"{c.id:8} {icon} {c.severity:8} {c.state.status:12} {c.category:16} "
          f"{c.raised_by}{related}  {c.title}")


def concerns_report_icon(severity: str) -> str:
    from opencode_crack.orchestrator.concerns_report import SEVERITY_ICON
    return SEVERITY_ICON.get(severity, "")


def _run_concern(args) -> None:
    action = args.concern_action

    if action == "file":
        try:
            c = concerns_board.file_concern(
                title=args.title, body=args.body, severity=args.severity,
                raised_by=args.raised_by, category=args.category,
                related_task=args.related_task,
            )
            print(f"Filed {c.id} ({c.severity}): {c.title}")
        except (ValueError, RuntimeError) as e:
            print(f"Could not file concern: {e}")
            sys.exit(1)

    elif action == "list":
        concerns = concerns_board.list_concerns(status=args.status, severity=args.severity)
        for c in concerns:
            _print_concern(c)
        print(f"\n{len(concerns)} concern(s)")

    elif action == "show":
        from opencode_crack.orchestrator.concern_parser import get_concern_section_text
        try:
            print(get_concern_section_text(args.concern_id))
        except ValueError as e:
            print(str(e))
            sys.exit(1)

    elif action == "board":
        from opencode_crack.orchestrator.concerns_report import BOARD_MD_PATH
        if not BOARD_MD_PATH.exists():
            print("No CONCERNS_BOARD.md yet — run 'orchestrate concern sync' first.")
            sys.exit(1)
        print(BOARD_MD_PATH.read_text(encoding="utf-8"))

    elif action == "acknowledge":
        try:
            c = concerns_board.acknowledge_concern(args.concern_id, notes=args.notes)
            print(f"{c.id} -> acknowledged")
        except (ValueError, RuntimeError) as e:
            print(f"Could not acknowledge {args.concern_id}: {e}")
            sys.exit(1)

    elif action == "resolve":
        try:
            c = concerns_board.resolve_concern(args.concern_id, notes=args.notes)
            print(f"{c.id} -> resolved")
        except (ValueError, RuntimeError) as e:
            print(f"Could not resolve {args.concern_id}: {e}")
            sys.exit(1)

    elif action == "dismiss":
        try:
            c = concerns_board.dismiss_concern(args.concern_id, notes=args.notes)
            print(f"{c.id} -> wontfix")
        except (ValueError, RuntimeError) as e:
            print(f"Could not dismiss {args.concern_id}: {e}")
            sys.exit(1)

    elif action == "reopen":
        try:
            c = concerns_board.reopen_concern(args.concern_id, notes=args.notes)
            print(f"{c.id} -> open")
        except (ValueError, RuntimeError) as e:
            print(f"Could not reopen {args.concern_id}: {e}")
            sys.exit(1)

    elif action == "sync":
        concerns = concerns_board.sync()
        print(f"Synced {len(concerns)} concern(s). See CONCERNS_BOARD.md.")


def _run_knowledge(args) -> None:
    action = args.knowledge_action

    if action == "file":
        try:
            tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
            k = knowledge_board.file_knowledge(
                title=args.title, body=args.body, category=args.category,
                submitted_by=args.submitted_by, tags=tags,
                related_task=args.related_task, supersedes=args.supersedes,
            )
            print(f"Filed {k.id} ({k.category}): {k.title}")
        except (ValueError, RuntimeError) as e:
            print(f"Could not file knowledge: {e}")
            sys.exit(1)

    elif action == "list":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        entries = knowledge_board.list_knowledge(status=args.status, category=args.category, tags=tags)
        for k in entries:
            _print_knowledge(k)
        print(f"\n{len(entries)} knowledge entry(ies)")

    elif action == "show":
        from opencode_crack.orchestrator.knowledge_parser import get_knowledge_section_text
        try:
            print(get_knowledge_section_text(args.knowledge_id))
            k = knowledge_board.get_knowledge(args.knowledge_id)
            if k:
                print(f"\nStatus: {k.state.status}  retrieval_count={k.state.retrieval_count}  useful_count={k.state.useful_count}")
        except ValueError as e:
            print(str(e))
            sys.exit(1)

    elif action == "verify-agent":
        try:
            k = knowledge_board.mark_agent_verified(args.knowledge_id, verified_by=args.verified_by, verified_how=args.verified_how)
            print(f"{k.id} -> agent-verified")
        except (ValueError, RuntimeError) as e:
            print(f"Could not verify-agent {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "verify-reviewer":
        try:
            k = knowledge_board.mark_reviewer_verified(args.knowledge_id, verified_by=args.verified_by, notes=args.notes)
            print(f"{k.id} -> reviewer-verified")
        except (ValueError, RuntimeError) as e:
            print(f"Could not verify-reviewer {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "confirm":
        try:
            k = knowledge_board.confirm_knowledge(args.knowledge_id, confirmed_by=args.confirmed_by, notes=args.notes)
            print(f"{k.id} -> confirmed")
        except (ValueError, RuntimeError) as e:
            print(f"Could not confirm {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "mark-useful":
        try:
            k = knowledge_board.mark_useful(args.knowledge_id)
            print(f"{k.id} marked useful")
        except (ValueError, RuntimeError) as e:
            print(f"Could not mark-useful {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "supersede":
        try:
            k = knowledge_board.supersede_knowledge(args.knowledge_id, args.with_id, notes=args.notes)
            print(f"{k.id} -> superseded by {args.with_id}")
        except (ValueError, RuntimeError) as e:
            print(f"Could not supersede {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "retract":
        try:
            k = knowledge_board.retract_knowledge(args.knowledge_id, notes=args.notes)
            print(f"{k.id} -> retracted")
        except (ValueError, RuntimeError) as e:
            print(f"Could not retract {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "sync":
        entries = knowledge_board.sync()
        print(f"Synced {len(entries)} knowledge entry(ies). See KNOWLEDGE_BOARD.md.")

    # --- Durable knowledge (Layer 2: KNOW-*.md entries) ---
    elif action == "search":
        keywords = [k.strip() for k in args.query.split() if k.strip()] if args.query else []
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        task_ids = [args.task] if args.task else None
        knowledge_types = [args.type] if args.type else None
        files = [args.file] if args.file else None
        results = knowledge_board.search_durable_knowledge(
            keywords=keywords,
            tags=tags,
            task_ids=task_ids,
            knowledge_types=knowledge_types,
            files=files,
            after=args.after,
            limit=args.limit,
            include_superseded=args.include_superseded,
            include_retracted=args.include_retracted,
        )
        # Status visibility is explicit-search only; apply the status flag here.
        if args.status:
            results = [e for e in results if e.get("status") == args.status]
        if args.json:
            import json
            print(json.dumps(results, indent=2))
        elif not results:
            print("No matching durable knowledge entries found.")
        else:
            for entry in results:
                print(f"{entry['id']}  [{entry['type']:12}]  {entry.get('status', '?'):10} "
                      f"conf={entry.get('confidence', '?'):6} score={entry['relevance_score']}  {entry['title']}")
                print(f"       path: {entry['path']}")
            print(f"\n{len(results)} result(s)")

    elif action == "durable-show":
        entry = knowledge_board.get_durable_knowledge(args.knowledge_id)
        if entry:
            fm = entry["front_matter"]
            print(f"# {entry['title']}\n")
            print(f"ID: {entry['id']}")
            print(f"Type: {entry['type']}")
            print(f"Status: {entry.get('status', fm.get('status'))}  "
                  f"Confidence: {entry.get('confidence', fm.get('confidence'))}  "
                  f"Priority: {fm.get('priority')}")
            print(f"Path: {entry['path']}")
            print(f"Tags: {', '.join(fm.get('tags', []))}")
            print(f"Keywords: {', '.join(fm.get('keywords', []))}")
            print(f"Task IDs: {', '.join(fm.get('task_ids', []))}")
            print(f"Files: {', '.join(fm.get('files', []))}")
            print(f"Related: {', '.join(fm.get('related', []))}")
            if fm.get("superseded_by"):
                print(f"Superseded by: {fm.get('superseded_by')}")
            print(f"Useful: {entry.get('useful_count', 0)}  "
                  f"Retrieved: {entry.get('retrieval_count', 0)}")
            print("\n--- Content ---\n")
            import re
            body_match = re.match(r"^---\n.*?\n---\n(.*)", entry["content"], re.DOTALL)
            body = body_match.group(1) if body_match else entry["content"]
            print(body.strip())
            from opencode_crack.orchestrator import durable_knowledge as dk
            dupes = dk.duplicates().get(args.knowledge_id, [])
            if len(dupes) > 1:
                print(f"\nWARNING: duplicate ID claimed by {len(dupes)} files; showing first.")
                for d in dupes:
                    print(f"  - {d}")
        else:
            print(f"No such durable knowledge entry: {args.knowledge_id}")
            sys.exit(1)

    elif action == "durable-file":
        from opencode_crack.orchestrator import durable_knowledge as dk
        try:
            entry = dk.file_entry(
                title=args.title, body=args.body, knowledge_type=args.type,
                tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
                task_ids=[t.strip() for t in args.task.split(",") if t.strip()],
                files=[f.strip() for f in args.file.split(",") if f.strip()],
                related=[r.strip() for r in args.related.split(",") if r.strip()],
                priority=args.priority, confidence=args.confidence,
                agents=[args.agent] if args.agent else [],
            )
            print(f"Filed {entry.id} ({entry.type}): {entry.title}")
            print(f"  path: {entry.path}")
            print("NOTE: no git commit made — the filing task's own commit publishes this file.")
        except (ValueError, RuntimeError) as e:
            print(f"Could not file durable knowledge: {e}")
            sys.exit(1)

    elif action == "durable-list":
        from opencode_crack.orchestrator import durable_knowledge as dk
        entries = dk.list_entries(
            knowledge_type=args.type, tag=args.tag, task_id=args.task,
            status=args.status, limit=args.limit,
        )
        for e in entries:
            print(f"{e['id']}  [{e['type']:12}]  {e.get('status', '?'):10} "
                  f"conf={e.get('confidence', '?'):6} {e['title']}")
        print(f"\n{len(entries)} entr(ies)")

    elif action == "durable-supersede":
        from opencode_crack.orchestrator import durable_knowledge as dk
        try:
            dk.supersede_entry(args.knowledge_id, args.with_id, notes=args.notes)
            print(f"{args.knowledge_id} -> superseded by {args.with_id}")
        except (ValueError, RuntimeError) as e:
            print(f"Could not supersede {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "durable-retract":
        from opencode_crack.orchestrator import durable_knowledge as dk
        try:
            dk.retract_entry(args.knowledge_id, reason=args.notes)
            print(f"{args.knowledge_id} -> retracted")
        except (ValueError, RuntimeError) as e:
            print(f"Could not retract {args.knowledge_id}: {e}")
            sys.exit(1)
    elif action == "durable-resolve":
        from opencode_crack.orchestrator import durable_knowledge as dk
        try:
            dk.resolve_entry(args.knowledge_id, notes=args.notes)
            print(f"{args.knowledge_id} -> resolved")
        except (ValueError, RuntimeError) as e:
            print(f"Could not resolve {args.knowledge_id}: {e}")
            sys.exit(1)

    elif action == "durable-useful":
        from opencode_crack.orchestrator import durable_knowledge as dk
        try:
            counts = dk.mark_entry_useful(args.knowledge_id)
            print(f"{args.knowledge_id} marked useful "
                  f"(useful={counts['useful_count']}, retrieved={counts['retrieval_count']})")
        except (ValueError, RuntimeError) as e:
            print(f"Could not mark-useful {args.knowledge_id}: {e}")
            sys.exit(1)


def main() -> None:
    """Zero-argument entry point for the `orchestrate` console script
    (see pyproject.toml). AI-Brain's own CLI calls run_orchestrate()
    directly with an explicit argv slice; this thin wrapper exists only
    because setuptools console-script entry points must be callables
    that take no arguments."""
    run_orchestrate(sys.argv[1:])


if __name__ == "__main__":
    main()
