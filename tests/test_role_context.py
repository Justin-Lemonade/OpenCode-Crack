"""Tests for src/runtime/role_context.py (D-155)."""
from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.role_context import ROLE_CONTEXT_FIXTURES, get_role_context


class TestRoleContextFixtures:
    def test_all_valid_roles_have_fixtures(self):
        for role in ("manager", "worker", "tester", "monitor"):
            assert role in ROLE_CONTEXT_FIXTURES

    def test_each_fixture_has_required_fields(self):
        required = {
            "role", "system", "task_scope", "responsibilities",
            "allowed_evidence_sources", "escalation_boundary",
            "default_output_format",
        }
        for role, fixture in ROLE_CONTEXT_FIXTURES.items():
            assert required <= fixture.keys(), f"{role} missing fields: {required - fixture.keys()}"

    def test_shared_context_not_duplicated_in_source(self):
        for role, fixture in ROLE_CONTEXT_FIXTURES.items():
            assert fixture["system"] == "multi-agent coordination system"
            assert fixture["task_scope"] == "bounded task"


class TestGetRoleContext:
    def test_known_role_returns_fixture(self):
        ctx = get_role_context("manager")
        assert ctx["role"] == "manager"
        assert "responsibilities" in ctx

    def test_unknown_role_returns_empty_dict(self):
        assert get_role_context("wizard") == {}

    def test_returned_dict_is_mutable_copy(self):
        ctx = get_role_context("worker")
        ctx["role"] = "mutated"
        assert ROLE_CONTEXT_FIXTURES["worker"]["role"] == "worker"


class TestAgentProfileRoleContext:
    def test_role_context_static_method_returns_fixture(self):
        ctx = AgentProfile.role_context("tester")
        assert ctx["role"] == "tester"
        assert "pass/fail verdict" in ctx["default_output_format"]

    def test_role_context_empty_for_unknown_role(self):
        assert AgentProfile.role_context("unknown") == {}


class TestRenderAgentConfigIncludesRoleContext:
    def test_manager_config_includes_role_context(self):
        from opencode_crack.runtime.swarm_config import render_agent_config
        profile = AgentProfile("m-01", "manager", "model", tool_permissions=[])
        config = render_agent_config(profile, "D-900")
        assert "role_context" in config
        assert config["role_context"]["role"] == "manager"
        assert "responsibilities" in config["role_context"]

    def test_worker_config_includes_role_context(self):
        from opencode_crack.runtime.swarm_config import render_agent_config
        profile = AgentProfile("w-01", "worker", "model", tool_permissions=[])
        config = render_agent_config(profile, "D-900")
        assert config["role_context"]["role"] == "worker"

    def test_tester_config_includes_role_context(self):
        from opencode_crack.runtime.swarm_config import render_agent_config
        profile = AgentProfile("t-01", "tester", "model", tool_permissions=[])
        config = render_agent_config(profile, "D-900")
        assert config["role_context"]["role"] == "tester"

    def test_monitor_config_includes_role_context(self):
        from opencode_crack.runtime.swarm_config import render_agent_config
        profile = AgentProfile("mon-01", "monitor", "model", tool_permissions=[])
        config = render_agent_config(profile, "D-900")
        assert config["role_context"]["role"] == "monitor"

    def test_unknown_role_excludes_role_context(self, monkeypatch):
        from opencode_crack.runtime import swarm_config
        original = swarm_config.get_role_context
        monkeypatch.setattr(swarm_config, "get_role_context", lambda role: {})
        try:
            profile = AgentProfile("x-01", "manager", "model", tool_permissions=[])
            config = swarm_config.render_agent_config(profile, "D-900")
            assert "role_context" not in config
        finally:
            monkeypatch.setattr(swarm_config, "get_role_context", original)

    def test_role_context_json_round_trip(self):
        from opencode_crack.runtime.swarm_config import render_agent_config
        import json
        profile = AgentProfile("m-01", "manager", "model", tool_permissions=[])
        config = render_agent_config(profile, "D-900")
        assert json.loads(json.dumps(config)) == config
