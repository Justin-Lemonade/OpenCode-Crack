"""Write-side runtime agent registry CLI.

Usage:
  python -m src.orchestrator.registry_cli add manager-main manager MODEL
  python -m src.orchestrator.registry_cli remove manager-main
  python -m src.orchestrator.registry_cli list

The registry lives in control.db, not the task board, so identity survives
session termination without creating Git coordination churn.
"""
from __future__ import annotations

import argparse
import json
import sys

from opencode_crack.runtime import control_db
from opencode_crack.runtime.agent_profile import AgentProfile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-registry")
    sub = parser.add_subparsers(dest="action", required=True)

    add = sub.add_parser("add", help="Create or update a persistent runtime agent")
    add.add_argument("agent_id")
    add.add_argument("role", choices=["manager", "worker", "tester", "monitor"])
    add.add_argument("model")
    add.add_argument("--manager-id")
    add.add_argument("--personality", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--tool", action="append", default=[])

    remove = sub.add_parser("remove", help="Remove a runtime agent")
    remove.add_argument("agent_id")

    list_cmd = sub.add_parser("list", help="List registered runtime agents")
    list_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    control_db.init_db()

    if args.action == "add":
        profile = AgentProfile(
            agent_id=args.agent_id,
            role=args.role,
            model=args.model,
            tool_permissions=args.tool,
            manager_id=args.manager_id,
            personality=args.personality,
            notes=args.notes,
        )
        control_db.register_agent(profile)
        print(f"registered: {profile.agent_id} ({profile.role})")
        return 0

    if args.action == "remove":
        with control_db._connect() as conn:
            active_session = conn.execute(
                "SELECT 1 FROM oc_sessions WHERE agent_id=? AND status='active' LIMIT 1",
                (args.agent_id,),
            ).fetchone()
            active_lease = conn.execute(
                "SELECT 1 FROM leases WHERE agent_id=? LIMIT 1",
                (args.agent_id,),
            ).fetchone()
            if active_session or active_lease:
                print(f"refusing to remove {args.agent_id}: active session or lease exists", file=sys.stderr)
                return 1
            cur = conn.execute("DELETE FROM agents WHERE agent_id=?", (args.agent_id,))
        if cur.rowcount == 0:
            print(f"not found: {args.agent_id}")
            return 1
        print(f"removed: {args.agent_id}")
        return 0

    agents = control_db.list_agents()
    if args.json:
        print(json.dumps(agents, indent=2, sort_keys=True))
    else:
        for agent in agents:
            print(f"{agent['agent_id']:24} {agent['role']:8} {agent['status']:8} {agent['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
