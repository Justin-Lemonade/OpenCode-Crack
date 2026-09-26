"""
Permission-aware tool execution (roadmap C-007, CRITICAL priority; the
approval channel/executor/audit log below are C-080).

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

import time
from dataclasses import dataclass
from enum import Enum

from opencode_crack.tools import executor
from opencode_crack.tools.registry import ToolRegistry, ToolSpec, registry as default_registry


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

    As of C-080, a durable approval channel exists (src.storage.approvals):
    raising this records a pending_approvals row and attaches its id
    (`request_id`) to the exception, so a caller (Telegram handler, CLI)
    can surface "approve/deny request #N" rather than a dead end. The
    call still does NOT run here -- approval is a separate, explicit step
    via approve_request()/run_approved() below."""
    def __init__(self, tool_name: str, reason: str, request_id: int | None = None):
        self.tool_name = tool_name
        self.reason = reason
        self.request_id = request_id
        suffix = f" (request id {request_id})" if request_id is not None else ""
        super().__init__(f"Approval required for {tool_name!r}: {reason}{suffix}")


def execute_tool(
    name: str,
    agent_kind: str = "any",
    registry: ToolRegistry = None,
    *,
    timeout_seconds: float = executor.DEFAULT_TIMEOUT_SECONDS,
    db_path=None,
    **kwargs,
):
    """The one path every caller should use to run a tool: model
    (cloud or local), CLI, Telegram, future API. Looks the tool up,
    evaluates the permission policy, executes through the safe executor
    (timeout-bounded, C-080), and records one audit_log entry for every
    outcome -- allowed, denied, approval-required, timed out, or
    errored. Never silently skips a call, and never skips the audit
    write on any path.

    `registry` defaults to the shared, module-level registry; a
    caller (mainly tests) can pass a different ToolRegistry instance.
    `db_path` similarly defaults to the real approvals/audit database;
    tests pass a throwaway path for isolation.
    """
    from src.storage import approvals

    kwargs_for_db_path = {"db_path": db_path} if db_path is not None else {}
    approvals.init_db(**kwargs_for_db_path)

    reg = registry if registry is not None else default_registry
    spec = reg.get(name)
    result = evaluate(spec, agent_kind=agent_kind)

    if result.decision is Decision.DENY:
        approvals.record_audit_entry(
            tool_name=name, agent_kind=agent_kind, decision="deny",
            outcome="denied", **kwargs_for_db_path,
        )
        raise PermissionDenied(name, result.reason)

    if result.decision is Decision.REQUIRE_APPROVAL:
        pending = approvals.create_pending_approval(
            name, kwargs, agent_kind=agent_kind, reason=result.reason, **kwargs_for_db_path,
        )
        approvals.record_audit_entry(
            tool_name=name, agent_kind=agent_kind, decision="require_approval",
            outcome="pending", request_id=pending["id"], **kwargs_for_db_path,
        )
        raise ApprovalRequired(name, result.reason, request_id=pending["id"])

    # ALLOW: run through the safe executor rather than reg.call() directly,
    # so every allowed call is timeout-bounded and every outcome (success,
    # timeout, handler error) gets exactly one audit entry.
    is_sensitive = spec.side_effect_level == "destructive"
    start = time.monotonic()
    try:
        value = executor.run_with_timeout(
            reg.call, name=name, timeout_seconds=timeout_seconds, tool_name=name, **kwargs,
        )
    except executor.ToolTimeout as exc:
        approvals.record_audit_entry(
            tool_name=name, agent_kind=agent_kind, decision=result.decision.value,
            outcome="timed_out", duration_ms=(time.monotonic() - start) * 1000,
            **kwargs_for_db_path,
        )
        raise
    except Exception as exc:
        approvals.record_audit_entry(
            tool_name=name, agent_kind=agent_kind, decision=result.decision.value,
            outcome="errored", duration_ms=(time.monotonic() - start) * 1000,
            output_summary=executor.summarize_output(str(exc), redact=is_sensitive),
            **kwargs_for_db_path,
        )
        raise

    approvals.record_audit_entry(
        tool_name=name, agent_kind=agent_kind, decision=result.decision.value,
        outcome="ran", duration_ms=(time.monotonic() - start) * 1000,
        output_summary=executor.summarize_output(value, redact=is_sensitive),
        **kwargs_for_db_path,
    )
    return value


def approve_request(request_id: int, *, approver: str, db_path=None) -> dict:
    """Approve a pending request. Does NOT run the tool -- call
    run_approved() separately, so a caller can approve now and run
    later (or from a different process), matching C-080's recovery
    requirement (durable state survives a restart)."""
    from src.storage import approvals

    kwargs_for_db_path = {"db_path": db_path} if db_path is not None else {}
    ok = approvals.decide_pending_approval(
        request_id, status="approved", decided_by=approver, **kwargs_for_db_path,
    )
    if not ok:
        raise ValueError(f"no pending request with id {request_id!r} (already decided, or does not exist)")
    return approvals.get_pending_approval(request_id, **kwargs_for_db_path)


def deny_request(request_id: int, *, approver: str, reason: str | None = None, db_path=None) -> dict:
    """Deny a pending request. Terminal -- a denied request cannot
    later be run via run_approved()."""
    from src.storage import approvals

    kwargs_for_db_path = {"db_path": db_path} if db_path is not None else {}
    ok = approvals.decide_pending_approval(
        request_id, status="denied", decided_by=approver, decision_reason=reason, **kwargs_for_db_path,
    )
    if not ok:
        raise ValueError(f"no pending request with id {request_id!r} (already decided, or does not exist)")
    return approvals.get_pending_approval(request_id, **kwargs_for_db_path)


def run_approved(
    request_id: int, *, registry: ToolRegistry = None,
    timeout_seconds: float = executor.DEFAULT_TIMEOUT_SECONDS, db_path=None,
):
    """Execute a request that has already been approved. Raises
    ValueError if the request doesn't exist, is still pending, or was
    denied -- only an 'approved' request can be run, and only once
    (this does not re-check it back to pending, so a second call finds
    it already 'approved' and will happily run it again; if
    exactly-once execution matters for a given tool, the caller is
    responsible for tracking that itself for now -- see the report for
    why this is left open)."""
    from src.storage import approvals

    kwargs_for_db_path = {"db_path": db_path} if db_path is not None else {}
    approvals.init_db(**kwargs_for_db_path)
    request = approvals.get_pending_approval(request_id, **kwargs_for_db_path)
    if request is None:
        raise ValueError(f"no request with id {request_id!r}")
    if request["status"] != "approved":
        raise ValueError(
            f"request {request_id!r} has status {request['status']!r}, not 'approved' -- cannot run it"
        )

    reg = registry if registry is not None else default_registry
    spec = reg.get(request["tool_name"])
    is_sensitive = spec.side_effect_level == "destructive"
    start = time.monotonic()
    try:
        value = executor.run_with_timeout(
            reg.call, name=request["tool_name"], timeout_seconds=timeout_seconds,
            tool_name=request["tool_name"], **request["args"],
        )
    except executor.ToolTimeout:
        approvals.record_audit_entry(
            tool_name=request["tool_name"], agent_kind=request["agent_kind"], decision="approved_run",
            outcome="timed_out", duration_ms=(time.monotonic() - start) * 1000,
            request_id=request_id, **kwargs_for_db_path,
        )
        raise
    except Exception as exc:
        approvals.record_audit_entry(
            tool_name=request["tool_name"], agent_kind=request["agent_kind"], decision="approved_run",
            outcome="errored", duration_ms=(time.monotonic() - start) * 1000,
            output_summary=executor.summarize_output(str(exc), redact=is_sensitive),
            request_id=request_id, **kwargs_for_db_path,
        )
        raise

    approvals.record_audit_entry(
        tool_name=request["tool_name"], agent_kind=request["agent_kind"], decision="approved_run",
        outcome="ran", duration_ms=(time.monotonic() - start) * 1000,
        output_summary=executor.summarize_output(value, redact=is_sensitive),
        request_id=request_id, **kwargs_for_db_path,
    )
    return value
