"""
AI-Brain runtime layer — OpenCode integration (Phase B).

See OPEN_CODE_SWARM_INTEGRATION_PLAN.md and docs/opencode_phase_a_findings.md
for architecture decisions and state ownership mapping.
"""
from src.runtime.agent_profile import AgentProfile, VALID_ROLES
from src.runtime.agent_runtime import AgentRuntime, SessionStatus
from src.runtime import control_db

__all__ = ["AgentProfile", "AgentRuntime", "SessionStatus", "VALID_ROLES", "control_db"]
