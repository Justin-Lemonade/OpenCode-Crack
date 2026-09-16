"""OpenCode HTTP runtime adapter.

This adapter is kept behind a narrow interface so the rest of AI-Brain does
not depend on OpenCode's HTTP details. Current routes follow the documented
OpenCode server API: POST /session, POST /session/:id/message,
GET /session/:id, and DELETE /session/:id.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.config import CONTROL_DB_PATH, OPENCODE_BASE_URL
from src.runtime import control_db
from src.runtime.agent_profile import AgentProfile

log = logging.getLogger(__name__)
HttpClient = Callable[[str, str, Optional[dict]], dict]


@dataclass
class SessionStatus:
    session_id: str
    agent_id: str
    task_id: Optional[str]
    control_status: str
    oc_status: Optional[str] = None
    oc_title: Optional[str] = None
    oc_model: Optional[dict] = None
    oc_tokens: Optional[dict] = None
    error: Optional[str] = None


class _OpenCodeError(Exception):
    pass


def _default_http_client(method: str, url: str, body: Optional[dict]) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 # D-364: OpenCode >= 1.17 answers 403 to library
                 # User-Agents even with valid credentials (verified live
                 # against `/`). Truthful client identification, not a
                 # credential or auth-model change.
                 "User-Agent": "AI-Brain-agent-runtime"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise _OpenCodeError(
            f"HTTP {e.code} {e.reason} from {method} {url}: "
            f"{e.read().decode(errors='replace')[:500]}"
        ) from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise _OpenCodeError(f"Could not reach OpenCode at {url}: {e}") from e


def _server_basic_auth_header() -> Optional[str]:
    """In-memory Basic header from OPENCODE_SERVER_USERNAME/PASSWORD env.

    D-364: OpenCode >= 1.17 answers 401 (Basic realm="Secure Area") without
    credentials, so an unauthenticated `GET /` can no longer prove health.
    Values are read at call time, held in memory only, and never logged or
    persisted by this module. Returns None when either variable is unset.
    """
    import base64
    import os

    username = os.environ.get("OPENCODE_SERVER_USERNAME")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not username or not password:
        return None
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def _authed_health_check(base_url: str) -> bool:
    """True only on HTTP 200 for `GET /`, presenting env credentials when
    set. A 401 means alive-but-unverifiable here (use
    runtime_lease.probe_server for the three-state distinction)."""
    auth = _server_basic_auth_header()
    req = urllib.request.Request(base_url.rstrip("/") + "/")
    req.add_header("User-Agent", "AI-Brain-agent-runtime")
    if auth:
        req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _infer_provider(model_id: str) -> str:
    m = model_id.lower()
    if "claude" in m:
        return "anthropic"
    if "gpt" in m or "o1" in m or "o3" in m:
        return "openai"
    if "gemini" in m:
        return "google"
    if "llama" in m or "mistral" in m or "qwen" in m:
        return "ollama"
    return "unknown"


class AgentRuntime:
    """Thin adapter between AI-Brain and the current OpenCode server API."""

    def __init__(self, base_url: str = OPENCODE_BASE_URL, db_path: Path = CONTROL_DB_PATH) -> None:
        self.base_url = base_url.rstrip("/")
        self.db_path = db_path

    def start(
        self,
        agent_profile: AgentProfile,
        task_id: Optional[str] = None,
        initial_message: Optional[str] = None,
        http_client: Optional[HttpClient] = None,
    ) -> str:
        client = http_client or _default_http_client
        payload: dict[str, Any] = {
            "title": f"[AI-Brain] {agent_profile.role} — {task_id or 'no task'}",
        }
        if agent_profile.model:
            if ":" in agent_profile.model:
                provider, model_id = agent_profile.model.split(":", 1)
            elif "/" in agent_profile.model:
                provider, model_id = agent_profile.model.split("/", 1)
            else:
                provider, model_id = _infer_provider(agent_profile.model), agent_profile.model
            payload["model"] = {"providerID": provider, "modelID": model_id}
        try:
            resp = client("POST", f"{self.base_url}/session", payload)
        except _OpenCodeError as e:
            raise RuntimeError(
                f"Failed to create OpenCode session for {agent_profile.agent_id}: {e}\n"
                f"Is `opencode serve` running at {self.base_url}?"
            ) from e
        oc_session_id = resp.get("id") or resp.get("sessionID") or resp.get("session_id", "")
        if not oc_session_id:
            raise RuntimeError(f"OpenCode returned session with no id: {resp!r}")
        control_db.record_session_start(
            session_id=oc_session_id,
            agent_id=agent_profile.agent_id,
            task_id=task_id,
            model=agent_profile.model,
            db_path=self.db_path,
        )
        control_db.set_agent_status(agent_profile.agent_id, "running", db_path=self.db_path)
        if initial_message:
            self.send(oc_session_id, initial_message, http_client=http_client)
        return oc_session_id

    def send(self, session_id: str, message: str, http_client: Optional[HttpClient] = None) -> dict:
        """Send a real OpenCode message using the documented ``parts`` shape."""
        client = http_client or _default_http_client
        payload = {"parts": [{"type": "text", "text": message}]}
        try:
            return client("POST", f"{self.base_url}/session/{session_id}/message", payload)
        except _OpenCodeError as e:
            raise RuntimeError(f"Failed to send message to session {session_id}: {e}") from e

    def status(self, session_id: str, http_client: Optional[HttpClient] = None) -> SessionStatus:
        client = http_client or _default_http_client
        with control_db._connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM oc_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return SessionStatus(
                session_id=session_id,
                agent_id="unknown",
                task_id=None,
                control_status="unknown",
                error="Session not found in control.db",
            )
        base = SessionStatus(
            session_id=session_id,
            agent_id=row["agent_id"],
            task_id=row["task_id"],
            control_status=row["status"],
        )
        try:
            resp = client("GET", f"{self.base_url}/session/{session_id}", None)
            base.oc_status = "archived" if resp.get("time", {}).get("archived") else "active"
            base.oc_title = resp.get("title")
            base.oc_model = resp.get("model")
            base.oc_tokens = resp.get("tokens")
        except _OpenCodeError as e:
            base.error = str(e)
        return base

    def stop(self, session_id: str, http_client: Optional[HttpClient] = None) -> None:
        client = http_client or _default_http_client
        try:
            client("DELETE", f"{self.base_url}/session/{session_id}", None)
        except _OpenCodeError as e:
            log.warning("Could not delete session %s on OpenCode: %s", session_id, e)
        control_db.record_session_end(session_id, status="archived", db_path=self.db_path)

    def is_server_healthy(self, http_client: Optional[HttpClient] = None) -> bool:
        # D-364: with an injected client the old behavior is preserved
        # (tests pin it); the real-client path authenticates, because
        # OpenCode >= 1.17 answers 401 without credentials.
        if http_client is not None:
            try:
                http_client("GET", f"{self.base_url}/", None)
                return True
            except _OpenCodeError:
                return False
        return _authed_health_check(self.base_url)

    @staticmethod
    def start_server(opencode_binary="opencode", *, cwd: Path, timeout_seconds=15,
                     port: Optional[int] = None, pure: bool = False) -> subprocess.Popen:
        """Launch `opencode serve`.

        ``cwd`` is required (D-290): this is a second, independent
        code-mutating execution entry point -- separate from
        ManagerLoop/SwarmRuntime's dispatch path -- since any session
        created against this server via `AgentRuntime.start()` inherits
        whatever directory the server process itself was started in. No
        current caller in this repo invokes this method, but leaving
        `cwd` optional (defaulting to the current process's directory,
        i.e. the shared checkout) would leave a silent isolation bypass
        available to any future one. Callers must resolve and validate an
        isolated lane via `src.orchestrator.worktree_guard` first and
        pass that path explicitly -- there is deliberately no implicit
        fallback.

        D-364: `port` selects an explicit loopback port (never a shared
        port by convention — prefer `runtime_lease.allocate()`, which
        probes and records ownership); `pure` adds `--pure` (no external
        plugins) for disposable experiment servers. Defaults preserve the
        historical bare-`serve` invocation.
        """
        import sys
        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        argv = [opencode_binary, "serve"]
        if pure:
            argv.append("--pure")
        if port is not None:
            argv.extend(["--port", str(port)])
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **extra,
        )
        runtime = AgentRuntime()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if runtime.is_server_healthy():
                return proc
            if proc.poll() is not None:
                stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                raise RuntimeError(
                    f"`opencode serve` exited early (rc={proc.returncode}).\nstderr: {stderr_out[:500]}"
                )
            time.sleep(0.5)
        proc.terminate()
        raise RuntimeError(f"`opencode serve` not healthy within {timeout_seconds}s. Check installation.")
