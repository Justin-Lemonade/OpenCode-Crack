"""
Tests for src/runtime/agent_runtime.py (roadmap D-130, broken out of C-072).

The AgentRuntime adapter talks to the OpenCode HTTP API; every test here
uses a fake http_client so no real OpenCode server is required. All DB
operations use tmp_path-isolated control.db files.
"""
from pathlib import Path

import pytest

from opencode_crack.runtime import agent_runtime, control_db
from opencode_crack.runtime.agent_profile import AgentProfile


def _profile(agent_id="test-worker-01", model="claude-sonnet-4-6"):
    return AgentProfile(agent_id, "worker", model)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "control.db"
    control_db.init_db(p)
    return p


@pytest.fixture
def profile(db):
    prof = _profile()
    control_db.register_agent(prof, db_path=db)
    return prof


@pytest.fixture
def runtime(db):
    return agent_runtime.AgentRuntime(base_url="http://opencode.test", db_path=db)


class FakeCalls:
    """Records (method, url, body) tuples so tests can assert on them."""

    def __init__(self):
        self.calls = []
        self.responses = {}

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        response = self.responses.get((method, url))
        if isinstance(response, Exception):
            raise response
        return response if response is not None else {}


class TestStart:
    def test_start_calls_post_session_with_payload_and_records_session(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-123", "title": "x"}

        session_id = runtime.start(profile, task_id="T-1", http_client=calls)

        assert session_id == "oc-123"
        method, url, body = calls.calls[0]
        assert method == "POST"
        assert url == "http://opencode.test/session"
        assert "T-1" in body["title"]
        # OpenCode's current session API uses providerID/modelID.
        assert body["model"] == {"providerID": "anthropic", "modelID": "claude-sonnet-4-6"}

        with control_db._connect(db) as conn:
            row = conn.execute("SELECT * FROM oc_sessions WHERE session_id=?", ("oc-123",)).fetchone()
        assert row["agent_id"] == profile.agent_id
        assert row["task_id"] == "T-1"
        assert row["status"] == "active"

    def test_start_uses_provider_from_model_colon(self, runtime, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-1"}
        colon_profile = AgentProfile("colon-01", "worker", "openai:gpt-4o")
        control_db.register_agent(colon_profile, db_path=db)

        runtime.start(colon_profile, http_client=calls)

        body = calls.calls[0][2]
        assert body["model"] == {"providerID": "openai", "modelID": "gpt-4o"}

    def test_start_raises_runtime_error_when_client_fails(self, runtime, profile):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = agent_runtime._OpenCodeError("boom")

        with pytest.raises(RuntimeError, match="Failed to create OpenCode session"):
            runtime.start(profile, http_client=calls)

    def test_start_raises_when_response_has_no_id(self, runtime, profile):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {}

        with pytest.raises(RuntimeError, match="no id"):
            runtime.start(profile, http_client=calls)

    def test_start_marks_agent_running_in_db(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-9"}

        runtime.start(profile, http_client=calls)

        with control_db._connect(db) as conn:
            row = conn.execute("SELECT status FROM agents WHERE agent_id=?", (profile.agent_id,)).fetchone()
        assert row["status"] == "running"

    def test_start_sends_initial_message_after_start(self, runtime, profile):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-5"}
        calls.responses[("POST", "http://opencode.test/session/oc-5/message")] = {"ok": True}

        runtime.start(profile, task_id="T-2", initial_message="hello there", http_client=calls)

        chat_calls = [c for c in calls.calls if c[0] == "POST" and c[1].endswith("/message")]
        assert len(chat_calls) == 1
        assert chat_calls[0][1] == "http://opencode.test/session/oc-5/message"
        assert chat_calls[0][2] == {"parts": [{"type": "text", "text": "hello there"}]}


class TestSend:
    def test_send_posts_message_content(self, runtime):
        calls = FakeCalls()

        runtime.send("oc-1", "a message", http_client=calls)

        method, url, body = calls.calls[0]
        assert method == "POST"
        assert url == "http://opencode.test/session/oc-1/message"
        assert body == {"parts": [{"type": "text", "text": "a message"}]}

    def test_send_returns_provider_response(self, runtime):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session/oc-1/message")] = {"message": "accepted"}

        response = runtime.send("oc-1", "hi", http_client=calls)

        assert response == {"message": "accepted"}

    def test_send_raises_runtime_error_on_failure(self, runtime):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session/oc-1/message")] = agent_runtime._OpenCodeError("down")

        with pytest.raises(RuntimeError, match="Failed to send message"):
            runtime.send("oc-1", "hi", http_client=calls)


class TestStatus:
    def test_status_active_when_not_archived(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-1"}
        runtime.start(profile, http_client=calls)
        calls.responses[("GET", "http://opencode.test/session/oc-1")] = {
            "title": "the title", "time": {"archived": None}
        }

        status = runtime.status("oc-1", http_client=calls)

        assert status.oc_status == "active"
        assert status.oc_title == "the title"

    def test_status_archived_when_time_archived_set(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-2"}
        runtime.start(profile, http_client=calls)
        calls.responses[("GET", "http://opencode.test/session/oc-2")] = {
            "time": {"archived": "2026-08-18T00:00:00Z"}
        }

        status = runtime.status("oc-2", http_client=calls)

        assert status.oc_status == "archived"

    def test_status_sets_error_field_when_unreachable(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-3"}
        runtime.start(profile, http_client=calls)
        calls.responses[("GET", "http://opencode.test/session/oc-3")] = agent_runtime._OpenCodeError("unreachable")

        status = runtime.status("oc-3", http_client=calls)

        assert status.error is not None
        assert "unreachable" in status.error

    def test_status_unknown_when_session_not_in_db(self, runtime, db):
        status = runtime.status("no-such-session", http_client=FakeCalls())

        assert status.control_status == "unknown"
        assert "not found" in status.error


class TestStop:
    def test_stop_calls_delete_and_marks_archived(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-1"}
        runtime.start(profile, http_client=calls)
        calls.responses[("DELETE", "http://opencode.test/session/oc-1")] = {}

        runtime.stop("oc-1", http_client=calls)

        assert any(c[0] == "DELETE" for c in calls.calls)
        with control_db._connect(db) as conn:
            row = conn.execute("SELECT status FROM oc_sessions WHERE session_id=?", ("oc-1",)).fetchone()
        assert row["status"] == "archived"

    def test_stop_records_archived_even_when_delete_fails(self, runtime, profile, db):
        calls = FakeCalls()
        calls.responses[("POST", "http://opencode.test/session")] = {"id": "oc-1"}
        runtime.start(profile, http_client=calls)
        calls.responses[("DELETE", "http://opencode.test/session/oc-1")] = agent_runtime._OpenCodeError("gone")

        runtime.stop("oc-1", http_client=calls)

        with control_db._connect(db) as conn:
            row = conn.execute("SELECT status FROM oc_sessions WHERE session_id=?", ("oc-1",)).fetchone()
        assert row["status"] == "archived"


class TestServerHealth:
    def test_healthy_returns_true_for_200(self, runtime):
        calls = FakeCalls()
        calls.responses[("GET", "http://opencode.test/")] = {"ok": True}

        assert runtime.is_server_healthy(http_client=calls) is True

    def test_healthy_returns_false_on_connection_error(self, runtime):
        calls = FakeCalls()
        calls.responses[("GET", "http://opencode.test/")] = agent_runtime._OpenCodeError("refused")

        assert runtime.is_server_healthy(http_client=calls) is False

    def test_default_client_identifies_itself_not_as_blocked_library(self):
        # D-364 live finding: OpenCode >= 1.17 answers 403 to
        # `Python-urllib/*` even with valid credentials.
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("User-Agent"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            agent_runtime._default_http_client("GET", f"http://127.0.0.1:{port}/", None)
        finally:
            server.shutdown()
        assert seen
        assert "Python-urllib" not in (seen[0] or "")


class TestInferProvider:
    @pytest.mark.parametrize(
        "model_id,expected",
        [
            ("claude-sonnet-4-6", "anthropic"),
            ("claude-opus-4-6", "anthropic"),
            ("gpt-4o", "openai"),
            ("gpt-4o-mini", "openai"),
            ("o1-preview", "openai"),
            ("llama3.3:70b", "ollama"),
            ("llama3.1", "ollama"),
            ("mistral-7b", "ollama"),
            ("qwen2.5", "ollama"),
            ("totally-unknown-model", "unknown"),
        ],
    )
    def test_maps_model_ids(self, model_id, expected):
        assert agent_runtime._infer_provider(model_id) == expected
