"""CLI entry point for the local autonomous swarm scheduler.

Examples:
  python -m src.orchestrator.autonomy_cli cycle --manager M --worker W --tester T
  python -m src.orchestrator.autonomy_cli run --manager M --worker W --tester T --cycles 10
  python -m src.orchestrator.autonomy_cli recover
"""
from __future__ import annotations

import argparse
import json

from src.orchestrator.autonomy import recover_stale_leases, run_cycle, scheduler
from src.runtime import control_db


def _profiles(args):
    profiles = [control_db.get_agent(agent_id) for agent_id in (args.manager, args.worker, args.tester)]
    missing = [agent_id for agent_id, profile in zip((args.manager, args.worker, args.tester), profiles) if profile is None]
    if missing:
        raise SystemExit(f"Unregistered runtime agent(s): {', '.join(missing)}")
    return profiles


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="brain-autonomy")
    sub = parser.add_subparsers(dest="action", required=True)

    recover = sub.add_parser("recover", help="Release stale runtime leases and requeue active board tasks")
    recover.add_argument("--json", action="store_true")

    cycle = sub.add_parser("cycle", help="Run exactly one autonomous task cycle")
    cycle.add_argument("--manager", required=True)
    cycle.add_argument("--worker", required=True)
    cycle.add_argument("--tester", required=True)
    cycle.add_argument("--timeout", type=int, default=3600)
    cycle.add_argument("--max-concurrent", type=int, default=3)
    cycle.add_argument("--budget-usd", type=float)
    cycle.add_argument("--attempt-cap", type=int, default=3)

    run = sub.add_parser("run", help="Run the autonomous scheduler")
    run.add_argument("--manager", required=True)
    run.add_argument("--worker", required=True)
    run.add_argument("--tester", required=True)
    run.add_argument("--cycles", type=int)
    run.add_argument("--interval", type=int, default=300)
    run.add_argument("--timeout", type=int, default=3600)
    run.add_argument("--max-concurrent", type=int, default=3)
    run.add_argument("--budget-usd", type=float)
    run.add_argument("--attempt-cap", type=int, default=3)

    args = parser.parse_args(argv)
    control_db.init_db()

    if args.action == "recover":
        result = recover_stale_leases()
        payload = {"recovered": result.recovered, "failed": result.failed, "task_ids": result.task_ids}
        print(json.dumps(payload, indent=2) if args.json else payload)
        return 0 if result.failed == 0 else 1

    manager, worker, tester = _profiles(args)
    kwargs = {
        "timeout_seconds": args.timeout,
        "max_concurrent": args.max_concurrent,
        "budget_usd": args.budget_usd,
        "attempt_cap": args.attempt_cap,
    }
    if args.action == "cycle":
        result = run_cycle(manager, worker, tester, **kwargs)
        if result is None or result.task_id is None:
            print("No eligible task available.")
            return 0
        payload = {
            "task_id": result.task_id,
            "error": result.error,
            "status": result.swarm_status,
            "recovery": {
                "requeued": result.recovery.recovered,
                "escalated": 0,
                "task_ids": result.recovery.task_ids,
            },
        }
        print(json.dumps(payload, indent=2))
        return 1 if result.error else 0

    result = scheduler(manager, worker, tester, interval_seconds=args.interval, max_cycles=args.cycles, **kwargs)
    print(json.dumps({
        "cycles_completed": result.cycles_completed,
        "tasks_processed": result.tasks_processed,
        "tasks_requeued": result.tasks_requeued,
        "tasks_escalated": result.tasks_escalated,
        "errors": result.errors,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
