"""
Permission-aware tool execution (roadmap C-007, CRITICAL priority).

"Never allow the model to directly decide which shell/file/network
operations are acceptable." This module is the layer the roadmap's
diagram describes, sitting between a tool request and the registry's
actual execution:

    model requests tool
           |
       tool registry        (C-006, src/tools/registry.py)
           |
       permission policy     <- THIS MODULE
           |
      allowed / denied / approval required
           |
        executor             (execute_tool(), below)

Model-independent by construction: nothing — cloud model, local model,
CLI, Telegram — should call ToolRegistry.call() directly. Everything
should go through execute_tool() here, so there is exactly one place
the "is this OK to run?" decision gets made, no matter who's asking.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.tools.registry import ToolRegistry, ToolSpec, registry as default_registry


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PermissionResult:
    decision: Decision
    reason: str


def evaluate(spec: ToolSpec, agent_kind: str = "any") -> PermissionResult:
    """Decide whether a tool call should run, based only on what the
    tool itself declares (side_effect_level, supported_agents,
    whether it has a handler). Deliberately simple and auditable — one
    function, no hidden state — because getting this wrong is a
    security bug, not a missing feature. A richer policy (per-agent,
    per-domain, config-driven) can replace this later without changing
    execute_tool()'s contract.

    This is a coarse, tool-level gate — it does not inspect the
    caller's actual arguments (e.g. *which* file write_file would
    touch). Argument-level validation is a smaller, separate concern
    for whenever write_file/run_shell/etc. get real handlers.
    """
    if not spec.implemented:
        return PermissionResult(Decision.DENY, f"{spec.name!r} has no handler yet (see registry docstring).")

    if spec.supported_agents != ["any"] and agent_kind not in spec.supported_agents:
        return PermissionResult(
            Decision.DENY,
            f"{spec.name!r} is only available to agents of kind {spec.supported_agents}, not {agent_kind!r}.",
        )

    if spec.side_effect_level in ("none", "read"):
        return PermissionResult(Decision.ALLOW, "read-only or no side effects.")

    if spec.side_effect_level == "write":
        # Matches what the CLI already does today: remember/ingest run
        # unguarded because a human is the one invoking the CLI directly.
        # A future policy could distinguish "human-initiated" from
        # "model-initiated" call sites; that distinction doesn't exist
        # yet, so writes are allowed once a real handler exists.
        return PermissionResult(Decision.ALLOW, "write with an existing, already-trusted handler.")

    # side_effect_level == "destructive"
    return PermissionResult(
        Decision.REQUIRE_APPROVAL,
        f"{spec.name!r} is destructive (side_effect_level='destructive') and needs explicit human approval.",
    )


class PermissionDenied(Exception):
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Permission denied for {tool_name!r}: {reason}")


class ApprovalRequired(Exception):
    """Raised when a tool call needs human approval before running.

    No approval channel exists yet (a Telegram confirm button, a CLI
    prompt, a Mini App review queue — none of that is built). Until
    one is, this exception itself IS the safety boundary: a destructive
    call simply does not happen, rather than silently running or
    silently being skipped."""
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Approval required for {tool_name!r}: {reason}")


def execute_tool(name: str, agent_kind: str = "any", registry: ToolRegistry = None, **kwargs):
    """The one path every caller should use to run a tool: model
    (cloud or local), CLI, Telegram, future API. Looks the tool up,
    evaluates the permission policy, then executes — or raises one of
    the exceptions above. Never silently skips a call.

    `registry` defaults to the shared, module-level registry; a
    caller (mainly tests) can pass a different ToolRegistry instance.
    """
    reg = registry if registry is not None else default_registry
    spec = reg.get(name)
    result = evaluate(spec, agent_kind=agent_kind)

    if result.decision is Decision.DENY:
        raise PermissionDenied(name, result.reason)
    if result.decision is Decision.REQUIRE_APPROVAL:
        raise ApprovalRequired(name, result.reason)

    return reg.call(name, **kwargs)
