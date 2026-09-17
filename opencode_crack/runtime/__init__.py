"""
Runtime layer — OpenCode integration.

See OPEN_CODE_SWARM_INTEGRATION_PLAN.md and docs/opencode_phase_a_findings.md
for architecture decisions and state ownership mapping.
"""
from opencode_crack.runtime.agent_profile import AgentProfile, VALID_ROLES
from opencode_crack.runtime.agent_runtime import AgentRuntime, SessionStatus
from opencode_crack.runtime import control_db

__all__ = ["AgentProfile", "AgentRuntime", "SessionStatus", "VALID_ROLES", "control_db"]
