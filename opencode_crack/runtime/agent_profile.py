"""
Persistent agent identity and configuration.

Each agent needs a stable identity that survives session termination.
The profile is stored in control.db (not git, not tasks.yaml) and
retrieved at the start of each new OpenCode session.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Optional

VALID_ROLES = frozenset({"manager", "worker", "tester", "monitor"})

@dataclass
class AgentProfile:
    """Stable identity for one persistent AI-Brain agent.
    Fields map 1:1 to the agents table in control.db."""
    agent_id: str
    role: str
    model: str
    tool_permissions: list[str] = field(default_factory=list)
    manager_id: Optional[str] = None
    personality: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id or not self.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if self.role not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {self.role!r}")
        if not self.model or not self.model.strip():
            raise ValueError("model must not be empty")

    def tool_permissions_json(self) -> str:
        return json.dumps(self.tool_permissions)

    @staticmethod
    def role_context(role: str) -> dict:
        """Return the compact role-context fixture for ``role`` (D-155)."""
        from opencode_crack.runtime.role_context import get_role_context
        return get_role_context(role)

    @classmethod
    def from_row(cls, row: dict) -> "AgentProfile":
        perms_raw = row.get("tool_permissions", "[]")
        try:
            perms = json.loads(perms_raw) if perms_raw else []
        except (json.JSONDecodeError, TypeError):
            perms = []
        return cls(
            agent_id=row["agent_id"], role=row["role"], model=row["model"],
            tool_permissions=perms, manager_id=row.get("manager_id"),
            personality=row.get("personality") or "", notes=row.get("notes") or "",
        )
