from pathlib import Path

from opencode_crack.runtime.agent_profile import AgentProfile
from opencode_crack.runtime.agent_runtime import AgentRuntime


def test_send_uses_documented_message_endpoint_and_parts():
    calls = []

    def fake(method, url, body):
        calls.append((method, url, body))
        return {"info": {"id": "msg-1"}, "parts": []}

    result = AgentRuntime(base_url="http://localhost:4096", db_path=Path("control.db")).send(
        "ses-1", "hello", http_client=fake
    )

    assert result["info"]["id"] == "msg-1"
    assert calls == [
        (
            "POST",
            "http://localhost:4096/session/ses-1/message",
            {"parts": [{"type": "text", "text": "hello"}]},
        )
    ]


def test_start_uses_provider_id_model_id():
    calls = []

    def fake(method, url, body):
        calls.append((method, url, body))
        return {"id": "ses-1"}

    profile = AgentProfile("worker", "worker", "openrouter/openai/gpt-4o-mini")
    runtime = AgentRuntime(base_url="http://localhost:4096", db_path=Path("control.db"))

    # Replace persistence calls for this API-shape test.
    import opencode_crack.runtime.agent_runtime as module
    original_start = module.control_db.record_session_start
    original_status = module.control_db.set_agent_status
    module.control_db.record_session_start = lambda **kwargs: None
    module.control_db.set_agent_status = lambda *args, **kwargs: None
    try:
        assert runtime.start(profile, http_client=fake) == "ses-1"
    finally:
        module.control_db.record_session_start = original_start
        module.control_db.set_agent_status = original_status

    assert calls[0][2]["model"] == {
        "providerID": "openrouter",
        "modelID": "openai/gpt-4o-mini",
    }
