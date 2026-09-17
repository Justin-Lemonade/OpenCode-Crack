"""Tests for src/runtime/swarm_config.py (roadmap D-143, broken out of C-074).

Pure serializer tests: AgentProfile -> minimal OpenCode Swarm agent config
shape. No DB, no subprocess, no network.
"""
import json

import pytest

from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_config import (
    ROLE_TASK_TEMPLATES,
    render_agent_config,
    render_swarm_config,
)


def _profile(role, agent_id=None, model="claude-sonnet-4-6", tools=None, **kwargs):
    return AgentProfile(agent_id or f"{role}-01", role, model,
                        tool_permissions=tools or [], **kwargs)


class TestRenderAgentConfig:
    def test_manager_represents_role_name_model_task_tools(self):
        config = render_agent_config(
            _profile("manager", tools=["read_file", "query_database"]), "D-999"
        )
        assert config["name"] == "manager-01"
        assert config["role"] == "manager"
        assert config["model"] == "claude-sonnet-4-6"
        assert "D-999" in config["task"]
        assert "manager" in config["task"].lower()
        assert config["tools"] == {"read_file": True, "query_database": True}

    def test_worker_represents_role_and_tools(self):
        config = render_agent_config(
            _profile("worker", tools=["read_file", "write_file", "run_python"]), "D-998"
        )
        assert config["name"] == "worker-01"
        assert config["role"] == "worker"
        assert "D-998" in config["task"]
        assert config["tools"] == {"read_file": True, "write_file": True, "run_python": True}

    def test_tester_represents_role(self):
        config = render_agent_config(_profile("tester", tools=["read_file"]), "D-997")
        assert config["role"] == "tester"
        assert config["tools"] == {"read_file": True}

    def test_empty_tool_restrictions_render_empty_allow_map(self):
        config = render_agent_config(_profile("worker"), "D-996")
        assert config["tools"] == {}

    def test_unset_optional_fields_are_omitted_not_null(self):
        config = render_agent_config(_profile("worker"), "D-995")
        assert "manager_id" not in config
        assert "personality" not in config
        assert "notes" not in config
        assert None not in config.values()

    def test_set_optional_fields_are_included(self):
        config = render_agent_config(
            _profile("worker", manager_id="manager-01", personality="Careful.",
                     notes="Reports to the manager."),
            "D-994",
        )
        assert config["manager_id"] == "manager-01"
        assert config["personality"] == "Careful."
        assert config["notes"] == "Reports to the manager."

    def test_unknown_role_falls_back_to_generic_task(self):
        config = render_agent_config(_profile("monitor"), "D-993")
        assert config["role"] == "monitor"
        assert "D-993" in config["task"]


class TestRenderSwarmConfig:
    def test_builds_full_config_with_three_roles(self):
        manager = _profile("manager", agent_id="manager-01", tools=["read_file"])
        worker = _profile("worker", agent_id="worker-01", tools=["read_file", "write_file"])
        tester = _profile("tester", agent_id="tester-01", tools=["read_file"])

        config = render_swarm_config("Bounded task A", [manager, worker, tester])

        assert config["name"] == "ai-brain-manager-01"
        assert config["model"] == manager.model
        assert config["maxRounds"] == 5
        assert [a["name"] for a in config["agents"]] == ["manager-01", "worker-01", "tester-01"]
        assert [a["role"] for a in config["agents"]] == ["manager", "worker", "tester"]
        assert all("Bounded task A" in a["task"] for a in config["agents"])

    def test_swarm_name_and_model_derive_from_first_profile(self):
        config = render_swarm_config("T", [_profile("tester", agent_id="t0", model="gpt-5")])
        assert config["name"] == "ai-brain-t0"
        assert config["model"] == "gpt-5"

    def test_max_rounds_is_configurable(self):
        config = render_swarm_config("T", [_profile("worker")], max_rounds=3)
        assert config["maxRounds"] == 3

    def test_empty_profile_list_raises(self):
        with pytest.raises(ValueError, match="at least one agent profile"):
            render_swarm_config("T", [])


class TestOutputIsValidJson:
    @pytest.mark.parametrize("role", ["manager", "worker", "tester"])
    def test_agent_config_round_trips_as_json(self, role):
        config = render_agent_config(
            _profile(role, tools=["read_file"], manager_id="m-1", personality="X", notes="Y"),
            "D-900",
        )
        assert json.loads(json.dumps(config)) == config

    def test_full_config_round_trips_as_json(self):
        config = render_swarm_config(
            "T", [_profile("manager"), _profile("worker"), _profile("tester")]
        )
        assert json.loads(json.dumps(config)) == config
        assert isinstance(json.dumps(config), str)

    def test_no_null_values_anywhere_in_full_config(self):
        config = render_swarm_config(
            "T", [_profile("manager", manager_id="m"), _profile("worker"), _profile("tester")]
        )
        text = json.dumps(config)
        assert "null" not in text


class TestRoleTaskTemplates:
    def test_every_valid_role_has_a_template(self):
        for role in ("manager", "worker", "tester"):
            assert ROLE_TASK_TEMPLATES[role]
            assert "{task}" in ROLE_TASK_TEMPLATES[role]
