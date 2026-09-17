"""
Tests for src/runtime/agent_profile.py (Phase B — C-072).
Pure dataclass tests — no DB interaction.
"""
import json
import pytest
from opencode_crack.runtime.agent_profile import AgentProfile, VALID_ROLES


class TestValidConstruction:
    def test_each_valid_role_constructs(self):
        for role in VALID_ROLES:
            profile = AgentProfile(agent_id=f"agent-{role}", role=role, model="claude-sonnet-4-6")
            assert profile.role == role

    def test_defaults_are_sane(self):
        profile = AgentProfile("a-01", "worker", "gpt-5")
        assert profile.tool_permissions == []
        assert profile.manager_id is None
        assert profile.personality == ""
        assert profile.notes == ""


class TestValidation:
    def test_empty_agent_id_raises(self):
        with pytest.raises(ValueError, match="agent_id"):
            AgentProfile("", "worker", "claude-sonnet-4-6")

    def test_whitespace_agent_id_raises(self):
        with pytest.raises(ValueError, match="agent_id"):
            AgentProfile("   ", "worker", "claude-sonnet-4-6")

    def test_bad_role_raises(self):
        with pytest.raises(ValueError, match="role"):
            AgentProfile("a-01", "president", "claude-sonnet-4-6")

    def test_empty_model_raises(self):
        with pytest.raises(ValueError, match="model"):
            AgentProfile("a-01", "worker", "")

    def test_whitespace_model_raises(self):
        with pytest.raises(ValueError, match="model"):
            AgentProfile("a-01", "worker", "   ")

    def test_empty_model_with_other_fields_raises(self):
        with pytest.raises(ValueError, match="model"):
            AgentProfile("a-01", "manager", " ", tool_permissions=["read"], manager_id="m-1")


class TestToolPermissionsJson:
    def test_empty_round_trips(self):
        profile = AgentProfile("a-01", "worker", "m")
        assert json.loads(profile.tool_permissions_json()) == []

    def test_single_round_trips(self):
        profile = AgentProfile("a-01", "worker", "m", tool_permissions=["read"])
        assert json.loads(profile.tool_permissions_json()) == ["read"]

    def test_multiple_round_trips(self):
        profile = AgentProfile("a-01", "worker", "m", tool_permissions=["read", "write", "execute"])
        assert json.loads(profile.tool_permissions_json()) == ["read", "write", "execute"]


class TestFromRow:
    def test_reconstructs_matching_profile(self):
        original = AgentProfile(
            "a-01", "tester", "gpt-5",
            tool_permissions=["read", "write"],
            manager_id="m-1",
            personality="Suspicious.",
            notes="Runs the eval suite.",
        )
        row = {
            "agent_id": original.agent_id,
            "role": original.role,
            "model": original.model,
            "tool_permissions": original.tool_permissions_json(),
            "manager_id": original.manager_id,
            "personality": original.personality,
            "notes": original.notes,
        }
        restored = AgentProfile.from_row(row)
        assert restored == original

    def test_from_row_matches_original_fields(self):
        original = AgentProfile("a-02", "worker", "claude-sonnet-4-6")
        row = {
            "agent_id": "a-02",
            "role": "worker",
            "model": "claude-sonnet-4-6",
            "tool_permissions": "[]",
            "manager_id": None,
        }
        restored = AgentProfile.from_row(row)
        assert restored.agent_id == original.agent_id
        assert restored.role == original.role
        assert restored.model == original.model
        assert restored.tool_permissions == []
        assert restored.manager_id is None

    def test_malformed_tool_permissions_falls_back_to_empty(self):
        row = {
            "agent_id": "a-03",
            "role": "worker",
            "model": "m",
            "tool_permissions": "{not valid json",
        }
        assert AgentProfile.from_row(row).tool_permissions == []

    def test_none_tool_permissions_falls_back_to_empty(self):
        row = {
            "agent_id": "a-04",
            "role": "worker",
            "model": "m",
            "tool_permissions": None,
        }
        assert AgentProfile.from_row(row).tool_permissions == []

    def test_missing_tool_permissions_falls_back_to_empty(self):
        row = {"agent_id": "a-05", "role": "worker", "model": "m"}
        assert AgentProfile.from_row(row).tool_permissions == []

    def test_whitespace_only_tool_permissions_falls_back_to_empty(self):
        row = {
            "agent_id": "a-06",
            "role": "worker",
            "model": "m",
            "tool_permissions": "   ",
        }
        assert AgentProfile.from_row(row).tool_permissions == []

    def test_manager_id_nullable_round_trip(self):
        profile = AgentProfile("a-07", "worker", "m")
        row = {
            "agent_id": profile.agent_id,
            "role": profile.role,
            "model": profile.model,
            "tool_permissions": profile.tool_permissions_json(),
            "manager_id": None,
        }
        restored = AgentProfile.from_row(row)
        assert restored.manager_id is None
        assert restored == profile

    def test_missing_optional_fields_use_defaults(self):
        row = {"agent_id": "a-08", "role": "monitor", "model": "m"}
        restored = AgentProfile.from_row(row)
        assert restored.manager_id is None
        assert restored.personality == ""
        assert restored.notes == ""
        assert restored.tool_permissions == []