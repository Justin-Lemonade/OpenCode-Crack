"""
Opt-in OpenCode Swarm smoke-test harness tests (roadmap D-168).

Pins the harness contract: skipped without the opt-in flag, skipped with
an actionable reason when the runtime is unavailable, one bounded
execution + normalization + event ingestion when it runs, runtime state
written only to the configured temporary directory, credentials never
printed, and distinct actionable failure causes for timeout / auth /
malformed output / server failure / missing binary.

Everything external is injected (swarm checker, OpenCode checker, swarm
runner) — no live swarm binary or OpenCode server is required.
"""
import json
from pathlib import Path

import pytest

from opencode_crack.runtime.swarm_runtime import SwarmHealth, SwarmResult
from opencode_crack.runtime.swarm_smoke import (
    CAUSE_AUTH_FAILURE,
    CAUSE_MALFORMED_OUTPUT,
    CAUSE_MISSING_BINARY,
    CAUSE_SERVER_FAILURE,
    CAUSE_TIMEOUT,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SKIPPED,
    _endpoint_label,
    _redact,
    render_report_text,
    report_to_dict,
    run_smoke,
)


def _healthy():
    return SwarmHealth(binary="swarm", available=True, version="0.4.5")


def _missing():
    return SwarmHealth(binary="swarm", available=False, error="OpenCode Swarm executable not found: swarm")


class FakeSwarmRuntime:
    """Deterministic fake implementing the SwarmRuntime.run/ingest_events
    surface the harness uses."""

    def __init__(self, result, ingest_count=None, ingest_error=None):
        self.result = result
        self.ingest_count = len(result.events) if ingest_count is None else ingest_count
        self.ingest_error = ingest_error
        self.calls = []

    def run(self, config_path, *, max_concurrent=None, budget_usd=None,
            timeout_seconds=None, event_path=None):
        self.calls.append({
            "config_path": config_path,
            "max_concurrent": max_concurrent,
            "budget_usd": budget_usd,
            "timeout_seconds": timeout_seconds,
            "event_path": event_path,
        })
        return self.result

    def ingest_events(self, result):
        if self.ingest_error is not None:
            raise self.ingest_error
        return self.ingest_count


def _passed_result():
    return SwarmResult(
        swarm_id="sw_1", status="completed",
        events=[{"id": "e1", "type": "agent-settled", "agent_id": "smoke-worker", "session_id": "s1"}],
        raw={"swarmId": "sw_1", "status": "completed", "agents": []},
        error=None,
    )


# --- opt-in semantics ---------------------------------------------------------

class TestOptIn:
    def test_not_enabled_skips_without_any_external_calls(self, tmp_path):
        def fail_health():
            pytest.fail("swarm checker must not run when opt-in flag is absent")

        report = run_smoke(enabled=False, swarm_checker=fail_health,
                           opencode_checker=lambda: pytest.fail("must not run"),
                           runner=fail_health, work_dir=tmp_path)
        assert report.status == STATUS_SKIPPED
        assert "opt-in flag absent" in (report.skip_reason or "")
        assert report.failure_cause is None

    def test_not_enabled_creates_no_work_dir(self):
        report = run_smoke(enabled=False)
        assert report.status == STATUS_SKIPPED
        assert report.work_dir is None


# --- runtime availability gating --------------------------------------------------

class TestRuntimeGating:
    def test_missing_swarm_binary_skips_with_actionable_cause(self, tmp_path):
        def no_health():
            pytest.fail("must not be called after missing swarm")

        report = run_smoke(enabled=True, swarm_checker=_missing,
                           opencode_checker=no_health, work_dir=tmp_path)
        assert report.status == STATUS_SKIPPED
        assert report.failure_cause == CAUSE_MISSING_BINARY
        assert "swarm executable not found" in (report.skip_reason or "").lower()
        assert any(c.name == "swarm_binary" and not c.passed for c in report.checks)

    def test_swarm_checker_exception_skips(self, tmp_path):
        def boom():
            raise OSError("exec not found")

        report = run_smoke(enabled=True, swarm_checker=boom, work_dir=tmp_path)
        assert report.status == STATUS_SKIPPED
        assert report.failure_cause == CAUSE_MISSING_BINARY

    def test_opencode_unhealthy_skips_with_server_cause(self, tmp_path):
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: False, work_dir=tmp_path)
        assert report.status == STATUS_SKIPPED
        assert report.failure_cause == CAUSE_SERVER_FAILURE
        assert "endpoint unreachable" in (report.skip_reason or "").lower()
        assert any(c.name == "opencode_server" and not c.passed for c in report.checks)

    def test_opencode_auth_error_is_actionable(self, tmp_path):
        def auth_boom():
            raise RuntimeError("HTTP 401 Unauthorized: invalid api key")

        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=auth_boom, work_dir=tmp_path)
        assert report.status == STATUS_SKIPPED
        assert report.failure_cause == CAUSE_AUTH_FAILURE


# --- full happy path -----------------------------------------------------------

class TestHappyPath:
    def test_passes_and_ingests_events(self, tmp_path):
        runner = FakeSwarmRuntime(_passed_result())
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner,
                           work_dir=tmp_path)
        assert report.status == STATUS_PASSED
        assert report.failure_cause is None
        names = {c.name: c.passed for c in report.checks}
        assert names == {
            "opt_in": True, "swarm_binary": True, "opencode_server": True,
            "swarm_run": True, "result_normalization": True, "event_ingestion": True,
        }
        assert report.ingested_events == 1
        assert report.swarm_id == "sw_1"
        assert report.swarm_version == "0.4.5"

    def test_runtime_state_written_only_inside_work_dir(self, tmp_path):
        runner = FakeSwarmRuntime(_passed_result())
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner,
                           work_dir=tmp_path)
        assert report.work_dir == str(tmp_path)
        assert (tmp_path / "swarm.json").is_file()
        assert (tmp_path / "swarm.db").is_file()
        assert report.ingested_events == 1
        assert json.loads((tmp_path / "swarm.json").read_text(encoding="utf-8"))["maxRounds"] == 1

    def test_default_work_dir_is_a_fresh_temp_dir(self):
        runner = FakeSwarmRuntime(_passed_result())
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner)
        assert report.status == STATUS_PASSED
        assert report.work_dir and "ai-brain-swarm-smoke-" in report.work_dir
        work = Path(report.work_dir)
        assert (work / "swarm.json").is_file()

    def test_bounded_execution_parameters_passed_through(self, tmp_path):
        runner = FakeSwarmRuntime(_passed_result())
        run_smoke(enabled=True, swarm_checker=_healthy, opencode_checker=lambda: True,
                  runner=runner, work_dir=tmp_path,
                  timeout_seconds=37, budget_usd=0.01, max_concurrent=1)
        call = runner.calls[0]
        assert call["max_concurrent"] == 1
        assert call["budget_usd"] == 0.01
        assert call["timeout_seconds"] == 37
        assert call["event_path"] == tmp_path / "swarm-events.jsonl"
        assert call["config_path"] == tmp_path / "swarm.json"

    def test_ingestion_exception_fails_with_server_cause(self, tmp_path):
        runner = FakeSwarmRuntime(_passed_result(), ingest_error=RuntimeError("db locked"))
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        assert report.status == STATUS_FAILED
        assert report.failure_cause == CAUSE_SERVER_FAILURE
        assert any(c.name == "event_ingestion" and not c.passed for c in report.checks)


# --- failure causes ---------------------------------------------------------------

class TestFailureCauses:
    def test_timeout(self, tmp_path):
        result = SwarmResult(None, "timeout", error="TimeoutExpired")
        runner = FakeSwarmRuntime(result)
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        assert report.status == STATUS_FAILED
        assert report.failure_cause == CAUSE_TIMEOUT
        check = next(c for c in report.checks if c.name == "swarm_run")
        assert "timeout" in check.detail

    def test_malformed_output(self, tmp_path):
        result = SwarmResult(None, "completed", raw={"error": "not json"}, error=None)
        runner = FakeSwarmRuntime(result, ingest_count=0)
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        assert report.status == STATUS_FAILED
        assert report.failure_cause == CAUSE_MALFORMED_OUTPUT
        check = next(c for c in report.checks if c.name == "result_normalization")
        assert not check.passed
        assert "malformed" in check.detail

    def test_auth_failure_in_run_error(self, tmp_path):
        result = SwarmResult(None, "failed", raw={}, error="401 unauthorized: invalid api key")
        runner = FakeSwarmRuntime(result, ingest_count=0)
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        assert report.status == STATUS_FAILED
        assert report.failure_cause == CAUSE_AUTH_FAILURE
        check = next(c for c in report.checks if c.name == "swarm_run")
        assert not check.passed
        assert "authentication" in check.detail.lower()

    def test_runner_raises_is_server_failure(self, tmp_path):
        class Boom:
            def run(self, *a, **k):
                raise RuntimeError("connection reset")
            def ingest_events(self, result):
                return 0
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=Boom(), work_dir=tmp_path)
        assert report.status == STATUS_FAILED
        assert report.failure_cause == CAUSE_SERVER_FAILURE
        assert any(c.name == "swarm_run" and not c.passed for c in report.checks)

    def test_failed_status_with_valid_output_is_passing_plumbing(self, tmp_path):
        result = SwarmResult("sw_9", "failed",
                             raw={"swarmId": "sw_9", "status": "failed", "agents": []},
                             events=[{"id": "e1", "type": "agent-turn-done"}],
                             error="model returned no output")
        runner = FakeSwarmRuntime(result)
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        assert report.status == STATUS_PASSED
        check = next(c for c in report.checks if c.name == "swarm_run")
        assert check.passed
        assert "failed" in check.detail  # surfaced, not hidden


# --- credential safety --------------------------------------------------------------

class TestCredentialSafety:
    def test_endpoint_label_strips_userinfo(self):
        assert _endpoint_label("http://user:secret@localhost:15000") == "http://localhost:15000"
        assert _endpoint_label("http://localhost:15000") == "http://localhost:15000"

    def test_report_endpoint_is_credential_stripped(self, tmp_path, monkeypatch):
        from opencode_crack import config
        monkeypatch.setattr(config, "OPENCODE_BASE_URL", "http://user:secret@localhost:15000")
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True,
                           runner=FakeSwarmRuntime(_passed_result()), work_dir=tmp_path)
        assert report.endpoint == "http://localhost:15000"
        assert "secret" not in report.endpoint

    def test_redact_strips_secret_shapes(self):
        assert _redact("sk-ant-api03-SuperSecretTokenHere") == "sk-***"
        assert _redact("Authorization: Bearer abc123") == "Authorization: Bearer ***"
        assert _redact("api_key=sk-abcdefgh12345678") == "api_key=***"
        assert _redact("http://user:pass@host/session") == "http://host/session"
        assert _redact(None) == ""
        assert _redact("plain text stays") == "plain text stays"

    def test_redacted_error_appears_in_report_not_raw(self, tmp_path):
        result = SwarmResult(None, "error", raw={}, error="HTTP 401 from http://user:pass@host: api_key=sk-abcdefgh12345678")
        runner = FakeSwarmRuntime(result)
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True, runner=runner, work_dir=tmp_path)
        text = render_report_text(report)
        assert "user:pass@" not in text
        assert "sk-abcdefgh12345678" not in text
        assert "***" in text


# --- rendering ---------------------------------------------------------------------

class TestRendering:
    def test_skipped_rendering_mentions_reason(self):
        report = run_smoke(enabled=False)
        text = render_report_text(report)
        assert "SKIPPED" in text
        assert "--run" in text

    def test_passed_rendering_lists_all_checks(self, tmp_path):
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True,
                           runner=FakeSwarmRuntime(_passed_result()), work_dir=tmp_path)
        text = render_report_text(report)
        assert "PASSED" in text
        assert "[PASS] swarm_binary" in text
        assert "[PASS] event_ingestion" in text
        assert "endpoint:" in text
        assert "work dir (temporary):" in text

    def test_report_to_dict_round_trips(self, tmp_path):
        report = run_smoke(enabled=True, swarm_checker=_healthy,
                           opencode_checker=lambda: True,
                           runner=FakeSwarmRuntime(_passed_result()), work_dir=tmp_path)
        data = report_to_dict(report)
        assert data["status"] == STATUS_PASSED
        assert data["ingested_events"] == 1
        assert any(c["name"] == "result_normalization" and c["passed"] for c in data["checks"])
        assert json.loads(json.dumps(data)) == data
