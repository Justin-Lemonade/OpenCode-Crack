"""Disposable OpenCode server leases: runtime isolation between ladder tasks.

Problem (D-364 / CN-010): two ladder tasks must never share one mutable
OpenCode server by accident. D-332 and D-333 both reached for
``127.0.0.1:4101`` while D-333 required a server restart — restarting or
probing another task's live server corrupts its evidence. ``AgentRuntime``
itself picks no port (it talks to ``OPENCODE_BASE_URL``) and its
``start_server()`` launches bare ``serve`` with no ownership record.

Contract (for D-333 and every later ladder task):
  1. One task = one server = one lease file. Allocate before use, release
     after. A port is never chosen "by convention": ``allocate()`` probes
     and refuses occupied ports instead of sharing them.
  2. A task operates (restart / cleanup / session surgery) only on a
     server whose lease names it as owner. Anything else raises
     ``RuntimeOwnershipError``. The only exception is a *stale* lease
     (recorded pid dead and port not serving), which anyone may reclaim —
     there is nobody home to disturb.
  3. Health is three-state, because OpenCode >= 1.17 answers 401
     (``Basic realm="Secure Area"``) without credentials: ``unavailable``
     (nothing listens), ``alive`` (something answers but not
     authenticated), ``healthy`` (authenticated 200). A task must be able
     to tell "server down" from "server owned by another task".
  4. Cleanup kills only the pid recorded in the caller's own lease file,
     then verifies the port freed. Killing by port number alone is
     forbidden — the port may have been recycled by an unrelated process.
  5. Credentials (``OPENCODE_SERVER_USERNAME`` / ``OPENCODE_SERVER_PASSWORD``)
     are read from the process environment at call time, held in memory
     only, and never written to lease files, logs, or error messages.
     This module adds no auth mechanism; it merely presents the
     environment-provided server credentials over loopback for liveness.

Lease files live in ``leases_dir`` (default: ``<temp>/opencode-runtime-
leases``, overridable for tests) as ``port-<port>.json``. They are
disposable coordination state, not board state: the task board remains
the only record of *task* ownership; leases record *runtime* ownership.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

DEFAULT_LEASES_DIR = Path(tempfile.gettempdir()) / "opencode-runtime-leases"

# Deterministic per-task port range (loopback only). Never a single
# conventional port: the allocator hashes inside the range and then scans
# for a actually-free port, so two tasks never collide by agreement.
PORT_RANGE_START = 4110
PORT_RANGE_SIZE = 80

_SERVER_USERNAME_ENV = "OPENCODE_SERVER_USERNAME"
_SERVER_PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD"

# D-364: OpenCode >= 1.17 refuses library User-Agents (verified live:
# `Python-urllib/3.12` -> 403 on `/` with valid creds, any neutral UA ->
# 200). This names the actual client truthfully; it is identification,
# not an auth boundary (credentials are still required). Duplicated in
# agent_runtime.py on purpose so this module stays dependency-free.
HTTP_USER_AGENT = "opencode-crack-runtime-lease"


class RuntimeInUseError(RuntimeError):
    """A port/lease is held by a live owner that is not the caller."""


class RuntimeOwnershipError(RuntimeError):
    """Caller tried to operate a runtime owned by another task."""


@dataclass
class RuntimeLease:
    task_id: str
    owner: str
    owner_session: str
    pid: int
    port: int
    cwd: str
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _leases_dir(leases_dir: Optional[Path] = None) -> Path:
    path = Path(leases_dir) if leases_dir else DEFAULT_LEASES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lease_path(port: int, leases_dir: Optional[Path] = None) -> Path:
    return _leases_dir(leases_dir) / f"port-{port}.json"


def _port_free(port: int) -> bool:
    """True when nothing listens on 127.0.0.1:port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _pick_port(task_id: str, hint: Optional[int] = None) -> int:
    """Deterministic start point per task, then first free port. Raises
    RuntimeInUseError only if the whole range is exhausted (never steals)."""
    if hint is not None:
        if _port_free(hint):
            return hint
        raise RuntimeInUseError(f"Requested port {hint} is already occupied; refusing to share it.")
    start = PORT_RANGE_START + (int(hashlib.sha1(task_id.encode()).hexdigest(), 16) % PORT_RANGE_SIZE)
    for offset in range(PORT_RANGE_SIZE):
        port = PORT_RANGE_START + ((start - PORT_RANGE_START + offset) % PORT_RANGE_SIZE)
        if _port_free(port):
            return port
    raise RuntimeInUseError("No free port in the disposable-server range; refusing to share.")


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid exists (no signal sent)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _basic_auth_header() -> Optional[str]:
    """In-memory Basic header from env creds, or None when unset. Values
    are never logged, persisted, or included in errors by this module."""
    username = os.environ.get(_SERVER_USERNAME_ENV)
    password = os.environ.get(_SERVER_PASSWORD_ENV)
    if not username or not password:
        return None
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {raw}"


def _http_status(base_url: str, auth: Optional[str]) -> Optional[int]:
    """GET / status code, or None when nothing answers."""
    request = urllib.request.Request(base_url.rstrip("/") + "/")
    request.add_header("User-Agent", HTTP_USER_AGENT)
    if auth:
        request.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def probe_server(base_url: str) -> str:
    """Three-state health without credentials: 'unavailable' (connection
    refused), 'alive' (answers, e.g. 401 — someone's server), 'healthy'
    (200 without auth, i.e. a pre-1.17-style open server). Never raises."""
    status = _http_status(base_url, None)
    if status is None:
        return "unavailable"
    return "healthy" if status == 200 else "alive"


def probe_server_authed(base_url: str) -> str:
    """Same three states with env credentials: 'healthy' only on
    authenticated 200. 'alive' here means reachable but not authenticated
    with these credentials (wrong creds, or someone else's server when
    creds are unset). Never raises, never exposes credential values."""
    auth = _basic_auth_header()
    if auth is None:
        return probe_server(base_url)
    status = _http_status(base_url, auth)
    if status is None:
        return "unavailable"
    return "healthy" if status == 200 else "alive"


def read_lease(port: int, leases_dir: Optional[Path] = None) -> Optional[RuntimeLease]:
    path = _lease_path(port, leases_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeLease(**{k: data[k] for k in RuntimeLease.__dataclass_fields__})
    except (ValueError, KeyError, TypeError):
        return None


def list_leases(leases_dir: Optional[Path] = None) -> list[RuntimeLease]:
    directory = _leases_dir(leases_dir)
    leases = []
    for path in sorted(directory.glob("port-*.json")):
        try:
            port = int(path.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        lease = read_lease(port, leases_dir)
        if lease is not None:
            leases.append(lease)
    return leases


def _resolve_opencode_binary() -> str:
    """Find a directly-executable opencode binary.

    Plain ``shutil.which("opencode")`` on Windows may resolve to the
    WinGet ``opencode.ps1`` shim, which ``CreateProcess`` cannot launch
    (D-332: ``%1 is not a valid Win32 application``). Prefer a real
    ``opencode.exe``: same-stem sibling first, then PATH.
    """
    import shutil

    found = shutil.which("opencode")
    if found:
        stem = Path(found)
        if stem.suffix.lower() in (".ps1", ".cmd", ".bat", ".vbs", ".js"):
            sibling = stem.with_suffix(".exe")
            if sibling.exists():
                return str(sibling)
            nested = stem.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
            if nested.exists():
                return str(nested)
        else:
            return found
    exe = shutil.which("opencode.exe")
    if exe:
        return exe
    return "opencode"


def _default_server_factory(port: int, cwd: Path) -> subprocess.Popen:
    """Launch `opencode serve --pure` on an explicit port. Factored out so
    tests can inject fakes instead of spawning real servers."""
    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [_resolve_opencode_binary(), "serve", "--pure", "--port", str(port)],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **extra,
    )


def allocate(task_id: str, owner: str, *, cwd: Path, owner_session: str = "",
             port: Optional[int] = None, leases_dir: Optional[Path] = None,
             timeout_seconds: int = 60,
             server_factory: Optional[Callable[[int, Path], subprocess.Popen]] = None) -> RuntimeLease:
    """Start a disposable server this task owns and record the lease.

    Refuses (never shares) when the chosen port is occupied or a live
    lease names another owner. Reclaims a stale lease (recorded pid dead
    and port not serving) with the reclaim noted on the new lease's
    lineage in the activity of the caller — the lease file itself only
    ever describes the current holder.
    """
    directory = _leases_dir(leases_dir)
    chosen = _pick_port(task_id, hint=port)
    existing = read_lease(chosen, leases_dir)
    if existing is not None:
        if _pid_alive(existing.pid) or probe_server(f"http://127.0.0.1:{chosen}") != "unavailable":
            raise RuntimeInUseError(
                f"Port {chosen} is held by live lease owner={existing.owner!r} "
                f"task={existing.task_id!r}; refusing to share. Pick another port."
            )
        (_lease_path(chosen, leases_dir)).unlink(missing_ok=True)
    factory = server_factory or _default_server_factory
    proc = factory(chosen, Path(cwd))
    base_url = f"http://127.0.0.1:{chosen}"
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = ""
                try:
                    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                    stderr = err[-500:]
                except (OSError, ValueError):
                    pass
                raise RuntimeError(f"`opencode serve` exited early (rc={proc.returncode}). stderr tail: {stderr}")
            if probe_server_authed(base_url) == "healthy" or (
                _basic_auth_header() is None and probe_server(base_url) == "healthy"
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Disposable server on :{chosen} not healthy within {timeout_seconds}s.")
    except Exception:
        try:
            proc.terminate()
        except (OSError, ValueError):
            pass
        raise
    lease = RuntimeLease(
        task_id=task_id,
        owner=owner,
        owner_session=owner_session,
        pid=proc.pid,
        port=chosen,
        cwd=str(Path(cwd)),
        created_at=_now(),
    )
    try:
        tmp_path = directory / f".port-{chosen}.tmp"
        tmp_path.write_text(json.dumps(asdict(lease), indent=2), encoding="utf-8")
        tmp_path.replace(_lease_path(chosen, leases_dir))
    except Exception:
        # Lease not recorded: must not leave an orphan server behind.
        try:
            proc.terminate()
        except (OSError, ValueError):
            pass
        raise
    return lease


def release(port: int, *, owner: str, leases_dir: Optional[Path] = None,
            terminate_timeout: int = 10) -> str:
    """Release a lease and stop its server. Returns 'released' or 'stale'.

    Only the recorded owner may release a live lease — anything else
    raises RuntimeOwnershipError (stale takeovers go through the board's
    release --force first, then allocate fresh; they never adopt a live
    lease). A stale lease (recorded pid dead) is removed without killing
    anything and reported as 'stale'. Killing is by recorded pid only,
    then the port is verified free — never kill-by-port.
    """
    lease = read_lease(port, leases_dir)
    if lease is None:
        if _port_free(port):
            return "stale"
        raise RuntimeInUseError(
            f"Port {port} is occupied but has no lease file; owner unknown. "
            "Refusing to touch it — pick another port."
        )
    if lease.owner != owner:
        if not _pid_alive(lease.pid) and _port_free(port):
            _lease_path(port, leases_dir).unlink(missing_ok=True)
            return "stale"
        raise RuntimeOwnershipError(
            f"Runtime on :{port} is owned by {lease.owner!r} (task {lease.task_id!r}), "
            f"not {owner!r}. Restart/cleanup of another task's server is forbidden."
        )
    if not _pid_alive(lease.pid):
        _lease_path(port, leases_dir).unlink(missing_ok=True)
        return "stale"
    _terminate_pid(lease.pid, terminate_timeout)
    deadline = time.monotonic() + terminate_timeout
    while time.monotonic() < deadline:
        if _port_free(port) and not _pid_alive(lease.pid):
            break
        time.sleep(0.25)
    _lease_path(port, leases_dir).unlink(missing_ok=True)
    return "released"


def _terminate_pid(pid: int, timeout: int) -> None:
    """Terminate exactly one pid (from our own lease file) with a kill
    fallback. Windows-safe: terminate() then wait, then kill on timeout."""
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
        except psutil.NoSuchProcess:
            pass
        return
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
        if not handle:
            return
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, 15)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(0.25)
        os.kill(pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
