"""
Tests for the named prompt template module (roadmap D-061, adapted for
this framework by C-076).

Pins get_prompt() behavior: latest-version resolution, explicit-version
lookup, and KeyError for unknown names/versions. Also verifies the
swarm role contracts resolve through the registry and stay in sync
with swarm_coordinator's thin wrapper.

AI-Brain's original version of this test also covered `gap_analysis`
and `clarifying_questions` — prompts for that project's
personal-knowledge gap-analysis feature, which isn't part of this
framework. The resolution-mechanics tests below are rewritten against
`swarm_role_manager` (a key that does live in this framework's
registry) instead, so the same mechanics are still pinned.
"""
import pytest

from opencode_crack.prompts import PROMPTS, get_prompt


def test_latest_version_resolution():
    prompt = get_prompt("swarm_role_manager")
    assert prompt == PROMPTS["swarm_role_manager.v1"]
    assert "manager" in prompt.lower()


def test_explicit_version_lookup():
    assert get_prompt("swarm_role_manager", version=1) == PROMPTS["swarm_role_manager.v1"]


def test_versioned_name_form():
    assert get_prompt("swarm_role_manager.v1") == PROMPTS["swarm_role_manager.v1"]


def test_unknown_name_raises_keyerror():
    with pytest.raises(KeyError, match="no_such_prompt"):
        get_prompt("no_such_prompt")


def test_unknown_version_raises_keyerror():
    with pytest.raises(KeyError, match="swarm_role_manager"):
        get_prompt("swarm_role_manager", version=99)


def test_swarm_role_prompts_registered():
    """C-047: all four swarm role contracts must be reachable through
    the registry, not just as a bare dict in swarm_coordinator.py."""
    for role in ("manager", "worker", "tester", "monitor"):
        key = f"swarm_role_{role}.v1"
        assert key in PROMPTS
        prompt = get_prompt(f"swarm_role_{role}")
        assert prompt == PROMPTS[key]
        assert prompt.strip()


def test_swarm_coordinator_role_instructions_match_registry():
    """swarm_coordinator.ROLE_INSTRUCTIONS is a thin wrapper — it must
    stay byte-for-byte in sync with the registry, not carry its own
    copy that can silently drift from a future swarm_role_*.v2."""
    from opencode_crack.runtime.swarm_coordinator import ROLE_INSTRUCTIONS

    for role in ("manager", "worker", "tester", "monitor"):
        assert ROLE_INSTRUCTIONS[role] == get_prompt(f"swarm_role_{role}")
