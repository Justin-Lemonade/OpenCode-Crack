from pathlib import Path

from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.swarm_coordinator import build_swarm_config, write_swarm_config


def _profiles():
    return [
        AgentProfile("manager", "manager", "openrouter/openai/gpt-4o-mini", ["read"]),
        AgentProfile("coder", "worker", "openrouter/openai/gpt-4o-mini", ["read", "write"] , manager_id="manager"),
        AgentProfile("tester", "tester", "openrouter/openai/gpt-4o-mini", ["read"], manager_id="manager"),
    ]


def test_build_config_preserves_roles_and_manager_relationships():
    config = build_swarm_config(_profiles(), task_id="D-201", task_prompt="Implement the bounded change.")

    assert [a["name"] for a in config["agents"]] == ["manager", "coder", "tester"]
    assert "Role: manager" in config["agents"][0]["task"]
    assert "AI-Brain task id: D-201" in config["agents"][1]["task"]
    assert "Manager: manager" in config["agents"][1]["task"]
    assert config["agents"][1]["tools"]["write"] is True
    assert config["agents"][0]["tools"]["write"] is False


def test_write_config_creates_valid_json(tmp_path: Path):
    config = build_swarm_config(_profiles())
    path = write_swarm_config(config, tmp_path / "swarm.json")

    assert path.exists()
    assert '"agents"' in path.read_text(encoding="utf-8")
