"""
Deterministic identity/personality prompt renderer (roadmap D-156).

An AgentProfile's compact identity — role, personality, manager
relationship, and tool permissions — is rendered into a new OpenCode
session *from the profile alone*, with no previous conversation transcript
required. The renderer is pure and deterministic: same profile in, same
prompt out, every time.

Design rules (from the roadmap acceptance):

  * deterministic prompt/context renderer — no I/O, no RNG, no model call;
  * personality and role are included once, compactly;
  * empty optional fields (manager_id, personality, notes) are omitted,
    never emitted as empty lines or nulls;
  * `identity_fields` carries every field needed to reconstruct the same
    stable identity in a fresh session (round-trip tested).

The role context from D-155 (responsibilities + escalation boundary) is
folded in so a new session gets its role expectations without re-reading a
past transcript.
"""
from __future__ import annotations

from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.role_context import get_role_context


def identity_fields(profile: AgentProfile) -> dict:
    """Compact stable identity dict. Optional fields are omitted when empty."""
    fields = {
        "agent_id": profile.agent_id,
        "role": profile.role,
        "model": profile.model,
        "tool_permissions": list(profile.tool_permissions),
    }
    if profile.manager_id:
        fields["manager_id"] = profile.manager_id
    if profile.personality:
        fields["personality"] = profile.personality
    if profile.notes:
        fields["notes"] = profile.notes
    return fields


def render_identity_prompt(profile: AgentProfile) -> str:
    """Render a compact, deterministic identity prompt for a new session.

    Personality and role each appear exactly once. Empty optional fields
    produce no line. The D-155 role context (responsibilities and escalation
    boundary) is appended when the profile's role has a fixture.
    """
    lines = [
        f"You are {profile.agent_id}, the {profile.role} in this multi-agent system.",
        f"Model: {profile.model}",
    ]
    if profile.manager_id:
        lines.append(f"Manager: {profile.manager_id}")
    if profile.personality:
        lines.append(f"Personality: {profile.personality}")
    if profile.notes:
        lines.append(f"Notes: {profile.notes}")
    if profile.tool_permissions:
        lines.append(f"Tools: {', '.join(sorted(profile.tool_permissions))}")

    context = get_role_context(profile.role)
    responsibilities = context.get("responsibilities") or []
    if responsibilities:
        lines.append("Responsibilities: " + "; ".join(responsibilities))
    boundary = context.get("escalation_boundary")
    if boundary:
        lines.append(f"Boundary: {boundary}")

    return "\n".join(lines)
