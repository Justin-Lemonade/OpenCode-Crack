"""
SwarmRuntime health-check tests (D-141): CLI availability and version
probing without a real OpenCode/Bun dependency.

subprocess.run and shutil.which are mocked everywhere; nothing here
executes the swarm binary. The health helper's contract (structured
unavailable result, never raises, version parsed from output) is pinned
for installed, missing, and malformed cases.
"""
import subprocess

import pytest

from opencode_crack.runtime.swarm_runtime import SwarmRuntime, _parse_version


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr("opencode_crack.runtime.swarm_runtime.shutil.which", lambda _: "/fake/bin/swarm")


@pytest.fixture
def missing(monkeypatch):
    monkeypatch.setattr("opencode_crack.runtime.swarm_runtime.shutil.which", lambda _: None)


class TestParseVersion:
    def test_plain_version(self):
        assert _parse_version("1.2.3") == "1.2.3"

    def test_two_part_version(self):
        assert _parse_version("v0.4") == "0.4"

    def test_ignores_surrounding_banner_text(self):
        assert _parse_version("opencode-swarm v0.4.5 (build 123)") == "0.4.5"

    def test_whitespace_and_newlines(self):
        assert _parse_version("  0.1.0\n") == "0.1.0"

    def test_returns_first_dotted_numeric_token(self):
        assert _parse_version("alpha 3.0 beta 4.1") == "3.0"

    def test_no_version_like_text_is_none(self):
        assert _parse_version("no version here") is None

    def test_empty_and_none_inputs_are_none(self):
        assert _parse_version("") is None
        assert _parse_version(None) is None


class TestSwarmHealthCheck:
    def test_installed_returns_version_token(self, installed, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: _completed(stdout="swarm v1.2.3\n"),
        )
        health = SwarmRuntime(binary="swarm").check()
        assert health.available is True
        assert health.version == "1.2.3"
        assert health.error is None
        assert health.binary == "swarm"

    def test_version_can_come_from_stderr(self, installed, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: _completed(stdout="", stderr="opencode-swarm 0.9.2"),
        )
        assert SwarmRuntime(binary="swarm").check().version == "0.9.2"

    def test_missing_binary_returns_structured_unavailable(self, missing, monkeypatch):
        called = []
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: called.append(1) or _completed(stdout="1.2.3"),
        )
        health = SwarmRuntime(binary="swarm").check()
        assert health.available is False
        assert health.version is None
        assert health.error is not None
        assert "not found" in health.error
        assert called == []  # subprocess must never run for a missing binary

    def test_missing_binary_does_not_raise(self, missing, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: pytest.fail("subprocess.run must not be called"),
        )
        assert SwarmRuntime(binary="swarm").check().available is False

    def test_malformed_output_is_structured_not_crash(self, installed, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: _completed(stdout="bun: command not found\n"),
        )
        health = SwarmRuntime(binary="swarm").check()
        assert health.available is True
        assert health.version is None
        assert health.error is not None
        assert "no recognizable version" in health.error

    def test_nonzero_exit_is_structured_not_crash(self, installed, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: _completed(returncode=1, stderr="boom\n"),
        )
        health = SwarmRuntime(binary="swarm").check()
        assert health.available is True
        assert health.version is None
        assert health.error is not None

    def test_binary_path_is_configurable(self, installed, monkeypatch):
        captured = {}

        def fake_run(args, *a, **k):
            captured["cmd"] = args
            return _completed(stdout="2.0.0")

        monkeypatch.setattr("opencode_crack.runtime.swarm_runtime.subprocess.run", fake_run)
        health = SwarmRuntime(binary="my-custom-swarm").check()
        assert captured["cmd"] == ["my-custom-swarm", "--version"]
        assert health.binary == "my-custom-swarm"
        assert health.version == "2.0.0"


class TestVersionMethod:
    def test_version_returns_parsed_token(self, installed, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: _completed(stdout="v3.2.1\n"),
        )
        assert SwarmRuntime(binary="swarm").version() == "3.2.1"

    def test_version_is_none_when_missing(self, missing, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.runtime.swarm_runtime.subprocess.run",
            lambda *a, **k: pytest.fail("subprocess.run must not be called"),
        )
        assert SwarmRuntime(binary="swarm").version() is None
