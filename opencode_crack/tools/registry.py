"""
Unified tool registry (roadmap C-006, adapted for this framework by
C-076).

One declarative catalog of every capability a project can expose to a
model or interface. Each ToolSpec declares:
    name               unique tool identifier
    description        what it does, for a model deciding whether to call it
    schema             dict describing accepted kwargs (name -> type hint string)
    permissions        list of permission strings this tool needs
    timeout_seconds    max time a call may run before being treated as failed
    side_effect_level  "none" | "read" | "write" | "destructive"
    supported_agents   which kinds of agent may call this (default ["any"])
    handler            the actual callable, or None if not implemented yet

IMPORTANT — this registry does NOT itself enforce a permission policy;
that's permissions.py's job, which reads a ToolSpec's declared
permissions but has no opinion on which specific tools exist.

AI-Brain's original version of this file also shipped a
`_register_defaults()` that pre-populated the registry with ~26
project-specific tools (search_memory, remember, ingest_document,
send_telegram, and others backed by AI-Brain's own `brain.py`). None
of that belongs in a framework with no opinion on where a consuming
project's domain data lives or which interfaces it exposes — dropped
here along with its `from opencode_crack import brain` dependency. This module
now exports an empty `registry`; whatever project adopts this
framework registers its own tools against it, the same way AI-Brain's
own copy of this file still does internally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

_VALID_SIDE_EFFECT_LEVELS = {"none", "read", "write", "destructive"}

#: Type-hint strings the registry schema may use, mapped to the runtime
#: types they describe. Hints are declarative only, so this table is the
#: small closed set call() validates against.
_TYPE_HINTS = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "None": type(None),
}

_HINT_CACHE: dict[str, tuple] = {}


def _types_for_hint(hint: str) -> tuple:
    """Resolve a declarative type-hint string (e.g. ``"str | None"``) to
    runtime types for isinstance checks. Fails closed: an unsupported
    hint raises rather than silently skipping validation, so a future
    tool can't declare a type this registry can't enforce."""
    if hint in _HINT_CACHE:
        return _HINT_CACHE[hint]
    parts = [p.strip() for p in hint.split("|")]
    resolved = []
    for part in parts:
        if part not in _TYPE_HINTS:
            raise ValueError(
                f"Unsupported type hint {part!r} in schema (from {hint!r}); "
                f"supported hints: {sorted(_TYPE_HINTS)}"
            )
        resolved.append(_TYPE_HINTS[part])
    _HINT_CACHE[hint] = tuple(resolved)
    return _HINT_CACHE[hint]


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    permissions: list[str]
    timeout_seconds: int
    side_effect_level: str
    supported_agents: list[str] = field(default_factory=lambda: ["any"])
    handler: Optional[Callable] = None  # None = declared but not yet implemented

    def __post_init__(self) -> None:
        if self.side_effect_level not in _VALID_SIDE_EFFECT_LEVELS:
            raise ValueError(
                f"Invalid side_effect_level {self.side_effect_level!r} for tool "
                f"{self.name!r}; must be one of {sorted(_VALID_SIDE_EFFECT_LEVELS)}"
            )

    @property
    def implemented(self) -> bool:
        return self.handler is not None


class ToolRegistry:
    """Holds every declared tool. One shared instance (`registry`,
    below) is what brain.py and interfaces should look tools up in,
    rather than each hard-coding which functions exist where."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"No such tool: {name!r}. Known tools: {sorted(self._tools)}")
        return self._tools[name]

    def list_tools(self, side_effect_level: Optional[str] = None, implemented_only: bool = False) -> list[ToolSpec]:
        tools = list(self._tools.values())
        if side_effect_level is not None:
            tools = [t for t in tools if t.side_effect_level == side_effect_level]
        if implemented_only:
            tools = [t for t in tools if t.implemented]
        return sorted(tools, key=lambda t: t.name)

    def call(self, name: str, **kwargs):
        """Execute a tool by name.

        No permission policy exists yet (C-007), so this enforces the
        conservative interim rule described in the module docstring:
        refuse anything without a wired handler. That's currently the
        only thing standing between "declared in the catalog" and
        "actually dangerous to let a model run".

        Kwarg types are validated against each tool's declared schema
        (D-027) before the handler runs, so wrong-typed arguments fail
        loudly instead of passing straight through. Missing args are
        still left to the handler's own signature enforcement, which is
        the behavior the existing suite pins."""
        spec = self.get(name)
        if spec.handler is None:
            raise NotImplementedError(
                f"Tool {name!r} is declared (side_effect_level={spec.side_effect_level!r}) "
                f"but has no handler yet. {spec.description}"
            )
        for arg, hint in spec.schema.items():
            if arg not in kwargs:
                continue
            if not isinstance(kwargs[arg], _types_for_hint(hint)):
                raise TypeError(
                    f"Tool {name!r} argument {arg!r} must be {hint!r}, "
                    f"got {type(kwargs[arg]).__name__}"
                )
        try:
            return spec.handler(**kwargs)
        except Exception as e:
            # Keep the original exception type (so callers that catch
            # TypeError, FileNotFoundError, etc. keep working) but attach
            # the failing tool's name as context. If the exception type
            # can't take attributes, re-raise as-is rather than losing the
            # original entirely.
            try:
                e.tool_name = name
            except Exception:
                pass
            raise


registry = ToolRegistry()

