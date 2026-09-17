"""
Tests for the unified tool registry (opencode_crack/tools/registry.py,
roadmap C-006, adapted for this framework by C-076).

Tests the registry's own bookkeeping (register/get/list/call) — the
things that matter regardless of what tools a consuming project
registers: implemented tools can run, and unimplemented tools refuse
to run rather than silently no-op.

AI-Brain's original version of this test also covered its own
pre-populated default tool set (search_memory, remember, run_shell,
etc., all backed by `brain.py`) via `_register_defaults()`. That
registration doesn't exist in this framework — `registry` starts
empty, and every test here either uses a throwaway `fresh =
ToolRegistry()` or a synthetic ToolSpec, so none of it depends on any
particular tool being pre-registered.
"""
import pytest

from opencode_crack.tools.registry import ToolRegistry, ToolSpec, registry


def test_list_tools_returns_deterministic_sorted_order():
    """list_tools() must return tools in a deterministic (sorted-by-name)
    order, independent of registration order and across repeated calls —
    so callers can rely on a stable sequence across processes (D-075)."""
    fresh = ToolRegistry()

    def _spec(name):
        return ToolSpec(
            name=name,
            description="test",
            schema={},
            permissions=[],
            timeout_seconds=1,
            side_effect_level="none",
        )

    # Register in deliberately non-sorted order.
    for name in ("zulu", "alpha", "mike", "bravo"):
        fresh.register(_spec(name))

    expected = ["alpha", "bravo", "mike", "zulu"]

    first = [t.name for t in fresh.list_tools()]
    second = [t.name for t in fresh.list_tools()]

    assert first == expected
    assert second == expected


def test_list_tools_filters_keep_deterministic_order():
    """side_effect_level and implemented_only filters must preserve the
    same sorted-by-name determinism as the unfiltered listing (D-075)."""
    fresh = ToolRegistry()
    for name in ("zulu", "alpha", "mike", "bravo"):
        fresh.register(ToolSpec(
            name=name,
            description="test",
            schema={},
            permissions=[],
            timeout_seconds=1,
            side_effect_level="read",
        ))
    # One implemented tool, distinct side-effect level, registered first
    # to prove filtering is order-independent.
    fresh.register(ToolSpec(
        name="writer",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="write",
        handler=lambda: "ok",
    ))

    assert [t.name for t in fresh.list_tools(side_effect_level="read")] == [
        "alpha", "bravo", "mike", "zulu",
    ]
    assert [t.name for t in fresh.list_tools(implemented_only=True)] == ["writer"]

    # Repeated calls with a filter are stable too.
    assert [t.name for t in fresh.list_tools(side_effect_level="read")] == [
        "alpha", "bravo", "mike", "zulu",
    ]


def test_get_unknown_tool_raises_keyerror():
    fresh = ToolRegistry()
    with pytest.raises(KeyError):
        fresh.get("does_not_exist")


def test_call_handlerless_tool_raises_not_implemented_with_name():
    """A declared tool with no handler must fail with an explicit
    NotImplementedError naming the tool — never a null dereference, a
    silent no-op, or a generic TypeError from schema validation (D-077)."""
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="declared_but_no_handler",
        description="not yet wired up",
        schema={"arg": "str"},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
    ))

    with pytest.raises(NotImplementedError) as exc_info:
        fresh.call("declared_but_no_handler", arg="anything")

    assert "declared_but_no_handler" in str(exc_info.value)


def test_call_handlerless_tool_raises_before_schema_validation():
    """The missing-handler check runs before kwarg type validation, so a
    handlerless tool called with a wrong-typed argument still surfaces
    NotImplementedError (the missing handler) rather than a confusing
    TypeError about the argument (D-077)."""
    fresh = ToolRegistry()
    fresh.register(ToolSpec(
        name="handlerless_with_schema",
        description="not yet wired up",
        schema={"n": "int"},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
    ))

    with pytest.raises(NotImplementedError) as exc_info:
        fresh.call("handlerless_with_schema", n="not_an_int")

    assert "handlerless_with_schema" in str(exc_info.value)


def test_duplicate_registration_rejected():
    fresh = ToolRegistry()
    spec = ToolSpec(
        name="dupe_test_tool",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
    )
    fresh.register(spec)
    with pytest.raises(ValueError):
        fresh.register(spec)


def test_duplicate_registration_rejects_second_spec_and_preserves_first():
    """A second, distinct spec with the same canonical name must be
    rejected (ValueError), and the first registration must survive — a
    duplicate must never silently replace the original (D-076)."""
    fresh = ToolRegistry()
    first = ToolSpec(
        name="replacement_probe",
        description="original registration",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
        handler=lambda: "original",
    )
    second = ToolSpec(
        name="replacement_probe",
        description="malicious or accidental replacement",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
        handler=lambda: "replacement",
    )
    fresh.register(first)

    with pytest.raises(ValueError):
        fresh.register(second)

    still_there = fresh.get("replacement_probe")
    assert still_there is first
    assert still_there.description == "original registration"
    assert fresh.call("replacement_probe") == "original"


def test_duplicate_registration_is_exact_name_match_only():
    """Canonical name today means exact `spec.name` match: case-differing
    names are NOT canonicalized and register as distinct tools. Pin that
    behavior — do not change it (D-076)."""
    fresh = ToolRegistry()
    lower = ToolSpec(
        name="case_sensitive_probe",
        description="lowercase",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
    )
    upper = ToolSpec(
        name="CASE_SENSITIVE_PROBE",
        description="uppercase",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
    )
    fresh.register(lower)
    fresh.register(upper)  # must not raise: distinct name, distinct tool

    names = {t.name for t in fresh.list_tools()}
    assert names == {"case_sensitive_probe", "CASE_SENSITIVE_PROBE"}
    assert fresh.get("case_sensitive_probe") is lower
    assert fresh.get("CASE_SENSITIVE_PROBE") is upper


def test_invalid_side_effect_level_rejected():
    with pytest.raises(ValueError):
        ToolSpec(
            name="bad_tool",
            description="test",
            schema={},
            permissions=[],
            timeout_seconds=1,
            side_effect_level="extremely_dangerous",  # not a real level
        )


def test_handler_exception_retains_tool_name_and_original_type():
    """A handler that raises must surface the failing tool name without
    changing the original exception type (D-078)."""
    fresh = ToolRegistry()

    def _boom(**kwargs):
        raise ValueError("kernel panic")

    fresh.register(ToolSpec(
        name="explode",
        description="test",
        schema={},
        permissions=[],
        timeout_seconds=1,
        side_effect_level="none",
        handler=_boom,
    ))

    with pytest.raises(ValueError) as exc_info:
        fresh.call("explode")

    assert exc_info.value.args == ("kernel panic",)
    assert getattr(exc_info.value, "tool_name", None) == "explode"


def test_registry_starts_empty():
    """Unlike AI-Brain's copy of this module, this framework's registry
    has no _register_defaults() — a project adopting this framework
    registers its own tools. Pin that the shared instance starts empty
    so a future accidental default-registration doesn't go unnoticed."""
    assert registry.list_tools() == []
