"""
D-364 (CN-010): disposable OpenCode server leases / runtime isolation.

One task = one server = one lease file. Ports are probed, never shared
by convention; only the recorded owner may restart/clean up; health is
three-state (unavailable / alive / healthy) because OpenCode >= 1.17
answers 401 without credentials.

Hermetic: injected server factories, monkeypatched probes, real loopback
sockets only (bind tests + tiny local HTTP servers). No real opencode
process is ever spawned here — live validation lives in the D-364 report.
"""
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from opencode_crack.runtime import runtime_lease
from opencode_crack.runtime.runtime_lease import (
    RuntimeInUseError,
    RuntimeLease,
    RuntimeOwnershipError,
)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeProc:
    def __init__(self, pid=424242):
        self.pid = pid
        self.stderr = None
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


def _factory_holder():
    holders = {}

    def factory(port, cwd):
        proc = FakeProc()
        holders["proc"] = proc
        holders["port"] = port
        holders["cwd"] = cwd
        return proc

    return factory, holders


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(runtime_lease, "probe_server_authed", lambda url: "healthy")
    monkeypatch.setattr(runtime_lease, "probe_server", lambda url: "healthy")


def _write_lease(leases_dir, port, owner="agent-a", task_id="D-001", pid=424242):
    leases_dir.mkdir(parents=True, exist_ok=True)
    lease = RuntimeLease(
        task_id=task_id, owner=owner, owner_session="abcd1234",
        pid=pid, port=port, cwd="C:\\tmp", created_at="2026-09-10T00:00:00Z",
    )
    (leases_dir / f"port-{port}.json").write_text(
        json.dumps({k: getattr(lease, k) for k in
                    ("task_id", "owner", "owner_session", "pid", "port", "cwd", "created_at")}),
        encoding="utf-8",
    )
    return lease


class TestPortSelection:
    def test_pick_is_deterministic_per_task(self, tmp_path):
        assert runtime_lease._pick_port("D-364") == runtime_lease._pick_port("D-364")

    def test_pick_stays_in_range(self, tmp_path):
        port = runtime_lease._pick_port("D-364")
        assert runtime_lease.PORT_RANGE_START <= port < runtime_lease.PORT_RANGE_START + runtime_lease.PORT_RANGE_SIZE

    def test_occupied_hint_refused_never_shared(self, tmp_path):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            busy = sock.getsockname()[1]
            with pytest.raises(RuntimeInUseError, match="refusing to share"):
                runtime_lease._pick_port("D-364", hint=busy)

    def test_port_free_true_then_false_while_bound(self):
        port = _free_port()
        assert runtime_lease._port_free(port) is True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
            assert runtime_lease._port_free(port) is False


class TestAllocate:
    def test_allocate_writes_lease_with_fields(self, tmp_path, healthy):
        factory, holders = _factory_holder()
        lease = runtime_lease.allocate("D-364", "agent-a", cwd=tmp_path, owner_session="s1",
                                       leases_dir=tmp_path / "leases", server_factory=factory)
        assert lease.task_id == "D-364"
        assert lease.owner == "agent-a"
        assert lease.owner_session == "s1"
        assert lease.pid == 424242
        assert holders["cwd"] == tmp_path
        assert (tmp_path / "leases" / f"port-{lease.port}.json").exists()

    def test_allocate_refuses_live_foreign_lease(self, tmp_path, healthy, monkeypatch):
        port = _free_port()
        _write_lease(tmp_path / "leases", port, owner="agent-b", pid=424242)
        monkeypatch.setattr(runtime_lease, "_pid_alive", lambda pid: True)
        factory, _ = _factory_holder()
        with pytest.raises(RuntimeInUseError, match="agent-b"):
            runtime_lease.allocate("D-364", "agent-a", cwd=tmp_path, port=port,
                                   leases_dir=tmp_path / "leases", server_factory=factory)

    def test_allocate_reclaims_stale_lease(self, tmp_path, healthy, monkeypatch):
        port = _free_port()
        _write_lease(tmp_path / "leases", port, owner="agent-b", pid=2 ** 30)
        monkeypatch.setattr(runtime_lease, "_pid_alive", lambda pid: False)
        monkeypatch.setattr(runtime_lease, "probe_server", lambda url: "unavailable")
        factory, _ = _factory_holder()
        lease = runtime_lease.allocate("D-364", "agent-a", cwd=tmp_path, port=port,
                                       leases_dir=tmp_path / "leases", server_factory=factory)
        assert lease.owner == "agent-a"


class TestRelease:
    def test_release_by_non_owner_refused(self, tmp_path, monkeypatch):
        port = _free_port()
        _write_lease(tmp_path, port, owner="agent-a", pid=424242)
        monkeypatch.setattr(runtime_lease, "_pid_alive", lambda pid: True)
        calls = []
        monkeypatch.setattr(runtime_lease, "_terminate_pid", lambda pid, timeout: calls.append(pid))
        with pytest.raises(RuntimeOwnershipError, match="owned by 'agent-a'.*not 'agent-b'"):
            runtime_lease.release(port, owner="agent-b", leases_dir=tmp_path)
        assert calls == []
        assert (tmp_path / f"port-{port}.json").exists()

    def test_release_by_owner_terminates_recorded_pid_only(self, tmp_path, monkeypatch):
        port = _free_port()
        _write_lease(tmp_path, port, owner="agent-a", pid=424242)
        alive = {"n": 0}

        def fake_alive(pid):
            alive["n"] += 1
            return alive["n"] == 1

        monkeypatch.setattr(runtime_lease, "_pid_alive", fake_alive)
        calls = []
        monkeypatch.setattr(runtime_lease, "_terminate_pid", lambda pid, timeout: calls.append(pid))
        assert runtime_lease.release(port, owner="agent-a", leases_dir=tmp_path) == "released"
        assert calls == [424242]
        assert not (tmp_path / f"port-{port}.json").exists()

    def test_release_stale_kills_nothing(self, tmp_path, monkeypatch):
        port = _free_port()
        _write_lease(tmp_path, port, owner="agent-a", pid=2 ** 30)
        monkeypatch.setattr(runtime_lease, "_pid_alive", lambda pid: False)
        calls = []
        monkeypatch.setattr(runtime_lease, "_terminate_pid", lambda pid, timeout: calls.append(pid))
        assert runtime_lease.release(port, owner="agent-a", leases_dir=tmp_path) == "stale"
        assert calls == []

    def test_release_occupied_without_lease_refuses(self, tmp_path):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            busy = sock.getsockname()[1]
            with pytest.raises(RuntimeInUseError, match="owner unknown"):
                runtime_lease.release(busy, owner="agent-a", leases_dir=tmp_path)

    def test_terminate_dead_pid_is_safe(self):
        runtime_lease._terminate_pid(2 ** 30, 1)


class TestLeaseFiles:
    def test_corrupt_lease_reads_as_none(self, tmp_path):
        (tmp_path).mkdir(parents=True, exist_ok=True)
        (tmp_path / "port-4111.json").write_text("not json{{", encoding="utf-8")
        assert runtime_lease.read_lease(4111, leases_dir=tmp_path) is None

    def test_list_leases_roundtrip(self, tmp_path):
        _write_lease(tmp_path, 4111, owner="agent-a")
        _write_lease(tmp_path, 4112, owner="agent-b")
        leases = runtime_lease.list_leases(leases_dir=tmp_path)
        assert {entry.port: entry.owner for entry in leases} == {4111: "agent-a", 4112: "agent-b"}

    def test_credentials_never_persisted(self, tmp_path, healthy, monkeypatch):
        monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "secret-user")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret-pass")
        factory, _ = _factory_holder()
        lease = runtime_lease.allocate("D-364", "agent-a", cwd=tmp_path,
                                       leases_dir=tmp_path / "leases", server_factory=factory)
        text = (tmp_path / "leases" / f"port-{lease.port}.json").read_text(encoding="utf-8")
        assert "secret-user" not in text
        assert "secret-pass" not in text


class _StatusHandler(http.server.BaseHTTPRequestHandler):
    status = 401
    seen_user_agents = []

    def do_GET(self):
        type(self).seen_user_agents.append(self.headers.get("User-Agent"))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serve_forever(server):
    with server:
        server.serve_forever()


def _local_server(status):
    handler = type("H", (_StatusHandler,), {"status": status})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=_serve_forever, args=(server,), daemon=True)
    thread.start()
    return server


class TestProbeStates:
    def test_unavailable_when_nothing_listens(self):
        assert runtime_lease.probe_server(f"http://127.0.0.1:{_free_port()}") == "unavailable"

    def test_alive_on_401_and_healthy_on_200(self):
        server401 = _local_server(401)
        server200 = _local_server(200)
        try:
            port401 = server401.server_address[1]
            port200 = server200.server_address[1]
            assert runtime_lease.probe_server(f"http://127.0.0.1:{port401}") == "alive"
            assert runtime_lease.probe_server(f"http://127.0.0.1:{port200}") == "healthy"
        finally:
            server401.shutdown()
            server200.shutdown()

    def test_authed_probe_needs_200(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "u")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "p")
        server401 = _local_server(401)
        try:
            port = server401.server_address[1]
            assert runtime_lease.probe_server_authed(f"http://127.0.0.1:{port}") == "alive"
        finally:
            server401.shutdown()

    def test_client_identifies_itself_not_as_blocked_library(self, monkeypatch):
        # D-364 live finding: OpenCode >= 1.17 answers 403 to
        # `Python-urllib/*` even with valid credentials.
        _StatusHandler.seen_user_agents = []
        server200 = _local_server(200)
        try:
            port = server200.server_address[1]
            assert runtime_lease.probe_server(f"http://127.0.0.1:{port}") == "healthy"
        finally:
            server200.shutdown()
        assert _StatusHandler.seen_user_agents
        for user_agent in _StatusHandler.seen_user_agents:
            assert "Python-urllib" not in (user_agent or "")
        assert runtime_lease.HTTP_USER_AGENT in _StatusHandler.seen_user_agents


class TestBinaryResolution:
    def test_never_resolves_to_unrunnable_shim(self):
        binary = runtime_lease._resolve_opencode_binary()
        assert isinstance(binary, str) and binary
        assert Path(binary).suffix.lower() not in (".ps1", ".cmd", ".bat", ".vbs", ".js")


class TestStartServerArgs:
    def test_port_and_pure_forwarded(self, tmp_path, monkeypatch):
        import subprocess

        from opencode_crack.runtime import agent_runtime

        captured = {}

        class FakeProc:
            def __init__(self):
                self.pid = 1
                self.returncode = None
                self.stderr = None

            def poll(self):
                return None

            def terminate(self):
                pass

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        with pytest.raises(RuntimeError, match="not healthy"):
            agent_runtime.AgentRuntime.start_server(
                opencode_binary="opencode-test", cwd=tmp_path,
                timeout_seconds=1, port=4123, pure=True,
            )
        assert captured["argv"][:2] == ["opencode-test", "serve"]
        assert "--pure" in captured["argv"]
        assert captured["argv"][captured["argv"].index("--port") + 1] == "4123"
        assert captured["kwargs"]["cwd"] == tmp_path
