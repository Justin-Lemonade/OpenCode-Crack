"""
Smoke tests for local LLM runtimes (Ollama + llama.cpp) — roadmap D-011.

These verify the same ladder the real code depends on, in order:

    Ollama:     server up -> /api/tags (model availability) ->
                /v1/chat/completions (OpenAI-compatible endpoint) ->
                basic chat -> tool call -> structured output
    llama.cpp:  server up -> /v1/models -> /v1/chat/completions ->
                basic chat -> tool call

Like test_vector_store.py, these SKIP with a clear reason when the runtime
isn't reachable (not installed, or server not running) — a skip means
"this environment has no runtime to smoke-test", not "something is
broken". Run them on the machine where Ollama/llama.cpp actually live
(e.g. `python -m pytest tests/test_local_runtime_smoke.py -v` on Justin's
machine) to get the full live check; in a network-restricted sandbox they
skip harmlessly.

Each probe uses urllib directly (same approach as doctor.py / dispatcher.py)
so there's no extra dependency and no implicit provider key.

The check ladder intentionally mirrors what src/ uses at runtime:
- src/agent/doctor.py probes http://localhost:11434/api/tags
- src/orchestrator/dispatcher.py POSTs to http://localhost:11434/api/generate
- src/orchestrator/cli.py --backend ollama --model llama3.1 (default)
"""
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest

OLLAMA_BASE = "http://localhost:11434"
LLAMACPP_BASE = "http://localhost:8080"
DEFAULT_MODEL = "llama3.1"


def _http_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    """POST a JSON payload to url and return the parsed JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ollama_reachable() -> bool:
    try:
        _http_get_json(f"{OLLAMA_BASE}/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _llamacpp_reachable() -> bool:
    """llama.cpp server exposes /v1/models; probe it the same way the
    OpenAI-compatible surface would be used."""
    try:
        _http_get_json(f"{LLAMACPP_BASE}/v1/models", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


ollama_available = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama not reachable at localhost:11434 (not installed or server not running)",
)
llamacpp_available = pytest.mark.skipif(
    not _llamacpp_reachable(),
    reason="llama.cpp server not reachable at localhost:8080 (llama-server not running)",
)


def _pick_ollama_model() -> str:
    """Return the model name Ollama actually has, preferring the default
    the repo uses (llama3.1) but falling back to any installed model so
    the smoke test still runs on machines with a different set pulled."""
    tags = _http_get_json(f"{OLLAMA_BASE}/api/tags")
    models = [m["name"].split(":")[0] for m in tags.get("models", [])]
    if DEFAULT_MODEL in models:
        return DEFAULT_MODEL
    if models:
        return models[0]
    raise AssertionError("Ollama is running but has no models pulled")


# --- Ollama ---------------------------------------------------------------

@ollama_available
def test_ollama_server_is_reachable():
    tags = _http_get_json(f"{OLLAMA_BASE}/api/tags")
    assert "models" in tags


@ollama_available
def test_ollama_has_a_model_available():
    tags = _http_get_json(f"{OLLAMA_BASE}/api/tags")
    assert tags.get("models"), "Ollama is running but no models are pulled"


@ollama_available
def test_ollama_openai_compatible_chat_endpoint():
    model = _pick_ollama_model()
    resp = _http_json(
        f"{OLLAMA_BASE}/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "say ok"}], "stream": False},
    )
    assert resp["choices"][0]["message"]["content"]
    assert "chat.completion" in resp.get("object", "")


@ollama_available
def test_ollama_basic_chat_returns_expected_content():
    model = _pick_ollama_model()
    resp = _http_json(
        f"{OLLAMA_BASE}/v1/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "You answer with exactly one word: yes or no."},
                {"role": "user", "content": "Is the sky blue?"},
            ],
            "stream": False,
        },
    )
    content = resp["choices"][0]["message"]["content"].strip().lower()
    assert content in ("yes", "no"), f"expected yes/no, got: {content!r}"


@ollama_available
def test_ollama_tool_call():
    """Request a tool call and require the model to return a proper
    tool_calls payload rather than free text. The weather tool is
    synthetic — this tests the plumbing, not the model's world knowledge."""
    model = _pick_ollama_model()
    resp = _http_json(
        f"{OLLAMA_BASE}/v1/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get the weather in a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        },
        timeout=60.0,
    )
    msg = resp["choices"][0]["message"]
    assert msg.get("tool_calls"), f"expected tool_calls, got message: {msg!r}"
    call = msg["tool_calls"][0]["function"]
    assert call["name"] == "get_weather"
    assert "city" in call.get("arguments", "")


@ollama_available
def test_ollama_structured_output_if_supported():
    """Structured output (JSON schema) is supported in recent Ollama via
    response_format. Not all older versions support it, so this test is
    conditional on the server accepting the field."""
    model = _pick_ollama_model()
    try:
        resp = _http_json(
            f"{OLLAMA_BASE}/v1/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Return a JSON object with a key 'name' set to 'test'."}],
                "stream": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "name_response",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        },
                    },
                },
            },
            timeout=60.0,
        )
    except urllib.error.HTTPError as e:
        if e.code in (400, 404, 501):
            pytest.skip(f"Ollama does not support response_format json_schema (HTTP {e.code})")
        raise
    content = resp["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    assert parsed.get("name") == "test"


# --- llama.cpp ------------------------------------------------------------

@llamacpp_available
def test_llamacpp_models_endpoint():
    resp = _http_get_json(f"{LLAMACPP_BASE}/v1/models")
    assert "data" in resp


@llamacpp_available
def test_llamacpp_openai_compatible_chat_endpoint():
    resp = _http_json(
        f"{LLAMACPP_BASE}/v1/chat/completions",
        {"model": "local-model", "messages": [{"role": "user", "content": "say ok"}], "stream": False},
        timeout=60.0,
    )
    assert resp["choices"][0]["message"]["content"]


@llamacpp_available
def test_llamacpp_tool_call():
    resp = _http_json(
        f"{LLAMACPP_BASE}/v1/chat/completions",
        {
            "model": "local-model",
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get the weather in a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        },
        timeout=60.0,
    )
    msg = resp["choices"][0]["message"]
    assert msg.get("tool_calls"), f"expected tool_calls, got message: {msg!r}"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"


@llamacpp_available
def test_llamacpp_binary_present():
    """llama-server (llama.cpp's OpenAI-compatible server) must be on PATH
    for a llama.cpp runtime to be startable. Server-reachability tests
    above require the server already running; this check additionally
    verifies the binary exists so `llama-server` can be launched."""
    assert shutil.which("llama-server") or (
        Path("llama-server").exists() and Path("llama-server").is_file()
    ), "llama-server binary not found on PATH (and not in repo root)"


@ollama_available
def test_ollama_binary_present():
    """Companion check: the ollama binary should be on PATH for the server
    to be startable/manageable from this machine."""
    assert shutil.which("ollama"), "ollama binary not found on PATH"