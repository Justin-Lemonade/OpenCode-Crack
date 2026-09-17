"""
Tests for permission-aware tool execution (opencode_crack/tools/
permissions.py, roadmap C-007, adapted for this framework by C-076).

Covers the three-way decision (allow / deny / require_approval) using
synthetic ToolSpecs on throwaway registries, so the policy logic is
exercised in isolation from whatever tools a consuming project happens
to register.

AI-Brain's original version of this test also exercised the policy
end-to-end through its own pre-registered default tools (search_memory,
remember, run_shell, forget) and through `brain.run_tool()` /
`brain.remember()` directly. Those don't exist in this framework —
dropped along with the `from src import brain` / `facts_db` imports —
but the destructive-tool and agent-kind-restriction cases they were
proving are still covered here against synthetic specs.
"""
import pytest

from opencode_crack.tools.permissions import (
    ApprovalRequired,
    Decision,
    PermissionDenied,
    evaluate,
    execute_tool,
)
from opencode_crack.tools.registry import ToolRegistry, ToolSpec


def test_unknown_tool_name_raises_keyerror():
    """A tool name that isn't registered at all is not a permission
    decision — execute_tool() surfaces the unknown name as KeyError
    from the registry lookup, never a silent skip (D-079)."""
    fresh = ToolRegistry()
    with pytest.raises(KeyError):
        execute_tool("tool_that_was_never_registered", registry=fresh)


def test_destructive_unimplemented_tool_is_denied_not_approval():
    """The not-implemented check runs before the destructive branch: a
    destructive tool with no handler is DENY, not REQUIRE_APPROVAL —
    you can't approve running something that doesn't exist yet (D-079)."""
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="destructive_but_not_wired",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="destructive",
    ))
    result = evaluate(fresh.get("destructive_but_not_wired"))
    assert result.decision is Decision.DENY
    assert "no handler yet" in result.reason


def test_agent_kind_restriction_denies_implemented_read_tool():
    """The agent-kind check applies regardless of implementation: an
    implemented read-only tool restricted to cloud agents is DENY for a
    local agent — for the RIGHT reason (agent kind, not missing handler)."""
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="cloud_only_read",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="read",
        supported_agents=["cloud"],
        handler=lambda: "ok",
    ))
    result = evaluate(fresh.get("cloud_only_read"), agent_kind="local")
    assert result.decision is Decision.DENY
    assert "only available to agents of kind" in result.reason

    # The same tool for a cloud agent is ALLOW.
    assert evaluate(fresh.get("cloud_only_read"), agent_kind="cloud").decision is Decision.ALLOW


def test_agent_kind_restriction_denies_unsupported_agent():
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="cloud_only_probe",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="read",
        supported_agents=["cloud"],
        handler=lambda: "ok",
    ))
    spec = fresh.get("cloud_only_probe")
    result = evaluate(spec, agent_kind="local")
    assert result.decision is Decision.DENY
    assert "only available to agents of kind" in result.reason


def test_destructive_implemented_tool_requires_approval():
    """A destructive tool with a real handler must REQUIRE_APPROVAL —
    the branch is generic, not tied to any particular tool."""
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="synthetic_destructive_tool",
        description="test",
        schema={},
        permissions=["destructive:test"],
        timeout_seconds=1,
        side_effect_level="destructive",
        handler=lambda: "should never run",
    ))
    spec = fresh.get("synthetic_destructive_tool")
    result = evaluate(spec)
    assert result.decision is Decision.REQUIRE_APPROVAL


def test_execute_tool_raises_permission_denied_for_unimplemented():
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="unimplemented_probe",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="read",
    ))
    with pytest.raises(PermissionDenied):
        execute_tool("unimplemented_probe", registry=fresh)


def test_execute_tool_raises_approval_required_for_destructive():
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="synthetic_destructive_tool_2",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="destructive",
        handler=lambda: "should never run",
    ))
    with pytest.raises(ApprovalRequired):
        execute_tool("synthetic_destructive_tool_2", registry=fresh)
