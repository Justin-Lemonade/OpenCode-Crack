"""
Opt-in OpenCode Swarm smoke-test harness (roadmap D-168).

Verifies the configured OpenCode + Swarm installation end-to-end through
the existing adapters: health, one bounded swarm execution, result
normalization, and event ingestion into control_db. The ordinary test
suite never touches external services; only this explicitly opt-in entry
point (``python -m src.main swarm-smoke --run``) exercises the live
runtime.

Guarantees:

  * **Opt-in by default** — without the opt-in flag the harness reports
    ``skipped`` and calls nothing external; with the flag it still skips
    (with an actionable reason) when the swarm binary or the OpenCode
    server is unavailable.
  * **Isolated runtime state** — the swarm config, swarm DB, and event
    stream are written to a caller-configured directory (system temp by
    default), never into the project tree.
  * **No credential leakage** — the displayed endpoint is credential-
    stripped and any captured output is redacted for common secret
    patterns before it appears in a report.
  * **Actionable failures** — missing binaries, server failure,
    authentication failure, timeout, and malformed output are each
    reported as an explicit cause, never as a bare traceback.

The swarm checker, OpenCode checker, and swarm runner are all injectable
so the exact report shape is unit-tested without a live binary or server.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.config import SWARM_SMOKE_MODEL, SWARM_SMOKE_TIMEOUT
from src.runtime.agent_profile import AgentProfile
from src.runtime.swarm_config import render_swarm_config
from src.runtime.swarm_normalize import normalize_swarm_result

# One bounded, cheap task for the smoke run: exercises the full pipeline
# without asking the model to do real work.
SMOKE_TASK = "Reply with exactly the word OK and nothing else."

# Report statuses (also the CLI exit code mapping: 0=passed, 1=failed,
# 2=skipped).
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Failure-cause vocabulary surfaced to the user for actionable diagnostics.
CAUSE_MISSING_BINARY = "missing binary"
CAUSE_SERVER_FAILURE = "server failure"
CAUSE_AUTH_FAILURE = "authentication failure"
CAUSE_TIMEOUT = "timeout"
CAUSE_MALFORMED_OUTPUT = "malformed output"

# Common authentication-failure markers in runtime output. Detected so the
# harness can say "authentication failure" instead of a generic error.
_AUTH_ERROR_RE = re.compile(
    r"(401|403|unauthori[sz]ed|authentication failed|invalid api key|invalid key|"
    r"missing api key|no api key|api key.*(invalid|missing)|permission denied|"
    r"access denied|insufficient (permissions|quota)|token.*(expired|invalid|invalidated))",
    re.IGNORECASE,
)

# Secret-shaped patterns redacted from any text that ends up in a report.
_CREDENTIAL_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # URL userinfo: http://user:pass@host -> http://host
    (re.compile(r"(://)[^/\s:@]+(?::[^/\s:@]*)?@"), r"\1"),
    # sk-<token> / key-<token>
    (re.compile(r"\b(sk|key)-[A-Za-z0-9_\-]{8,}\b"), r"\1-***"),
    # Bearer <token>
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE), "Bearer ***"),
    # key=value / key: value credential-ish fields (never the Bearer token
    # itself, which the pattern above already redacts)
    (re.compile(r"\b(api[_-]?key|token|password|secret|authorization)\s*[=:]\s*(?!Bearer\b)[^\s,;]+",
                re.IGNORECASE), r"\1=***"),
)


def _redact(text: str | None) -> str:
    """Strip common credential patterns from a string for safe reporting."""
    if not text:
        return text or ""
    out = str(text)
    for pattern, repl in _CREDENTIAL_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _endpoint_label(base_url: str) -> str:
    """Endpoint shown in reports. Strips any userinfo (user:pass@) so
    credentials embedded in a URL are never printed."""
    label = base_url
    if "://" in label:
        scheme, _, rest = label.partition("://")
        if "@" in rest:
            rest = rest.rsplit("@", 1)[1]
        label = f"{scheme}://{rest}"
    return label


@dataclass(frozen=True)
class SmokeCheck:
    """One named pass/fail check in the smoke report."""
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SmokeReport:
    """Structured outcome of a smoke run.

    ``status`` is one of passed / failed / skipped. ``failure_cause`` is
    one of the CAUSE_* constants (set on failed and on runtime-skipped
    runs) so the CLI can exit with a machine-distinguishable reason.
    """
    status: str
    checks: tuple[SmokeCheck, ...]
    skip_reason: str | None = None
    work_dir: str | None = None
    endpoint: str | None = None
    swarm_version: str | None = None
    swarm_id: str | None = None
    ingested_events: int = 0
    failure_cause: str | None = None


def run_smoke(
    *,
    enabled: bool = False,
    work_dir: Optional[Path] = None,
    swarm_binary: str = "swarm",
    model: str = SWARM_SMOKE_MODEL,
    timeout_seconds: int = SWARM_SMOKE_TIMEOUT,
    budget_usd: float = 0.05,
    max_concurrent: int = 1,
    swarm_checker: Optional[Callable[[], Any]] = None,
    opencode_checker: Optional[Callable[[], bool]] = None,
    runner: Optional[Any] = None,
) -> SmokeReport:
    """Run the opt-in live smoke test and return a structured report.

    ``enabled`` is the explicit opt-in flag: when False the harness reports
    ``skipped`` without touching any external service. When True, it still
    skips (with the reason) if the swarm binary or OpenCode server is
    unavailable. ``work_dir`` is where all runtime state is written (system
    temp when omitted). The three runtime hooks (``swarm_checker``,
    ``opencode_checker``, ``runner``) default to the real adapters and are
    injectable for deterministic tests.
    """
    if not enabled:
        return SmokeReport(
            status=STATUS_SKIPPED,
            checks=(SmokeCheck("opt_in", False, "opt-in flag absent"),),
            skip_reason=(
                "opt-in flag absent - run `python -m src.main swarm-smoke --run` "
                "to exercise the live OpenCode + Swarm runtime"
            ),
        )

    checks: list[SmokeCheck] = [SmokeCheck("opt_in", True, "explicit opt-in flag present")]

    # --- health: swarm binary -------------------------------------------------
    swarm_version = None
    try:
        health = swarm_checker() if swarm_checker is not None else _default_swarm_health(swarm_binary)
        available = bool(getattr(health, "available", False))
        swarm_version = getattr(health, "version", None)
        swarm_error = getattr(health, "error", None)
    except Exception as exc:
        available, swarm_error = False, _redact(exc)
    detail = f"binary={swarm_binary}"
    if swarm_version:
        detail += f", version={swarm_version}"
    if swarm_error:
        detail += f", {swarm_error}"
    checks.append(SmokeCheck("swarm_binary", available, detail))
    if not available:
        return SmokeReport(
            status=STATUS_SKIPPED,
            checks=tuple(checks),
            skip_reason=f"required runtime unavailable: {_redact(swarm_error or 'swarm executable not found')}",
            failure_cause=CAUSE_MISSING_BINARY,
            swarm_version=swarm_version,
        )

    # --- health: OpenCode server ----------------------------------------------
    opencode_error = None
    try:
        healthy = bool(opencode_checker()) if opencode_checker is not None else _default_opencode_health()
    except Exception as exc:
        healthy, opencode_error = False, _redact(exc)
    endpoint = _default_endpoint()
    checks.append(SmokeCheck(
        "opencode_server", healthy,
        f"{endpoint} ({'healthy' if healthy else _redact(opencode_error or 'unreachable')})",
    ))
    if not healthy:
        cause = (CAUSE_AUTH_FAILURE if _AUTH_ERROR_RE.search(opencode_error or "")
                 else CAUSE_SERVER_FAILURE)
        return SmokeReport(
            status=STATUS_SKIPPED,
            checks=tuple(checks),
            skip_reason=f"required runtime unavailable: OpenCode endpoint unreachable at {endpoint}",
            failure_cause=cause,
            endpoint=endpoint,
            swarm_version=swarm_version,
        )

    # --- execute one bounded swarm run ----------------------------------------
    work_dir_path = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="ai-brain-swarm-smoke-"))
    work_dir_path.mkdir(parents=True, exist_ok=True)
    db_path = work_dir_path / "swarm.db"
    event_path = work_dir_path / "swarm-events.jsonl"
    config_path = work_dir_path / "swarm.json"

    profile = AgentProfile("smoke-worker", "worker", model, tool_permissions=[])
    config_path.write_text(
        json.dumps(render_swarm_config(SMOKE_TASK, [profile], max_rounds=1), indent=2),
        encoding="utf-8",
    )

    from src.runtime import control_db
    control_db.init_db(db_path=db_path)

    if runner is None:
        runner = _default_runner(swarm_binary, db_path, work_dir_path)

    failure_cause: str | None = None
    try:
        result = runner.run(
            config_path,
            max_concurrent=max_concurrent,
            budget_usd=budget_usd,
            timeout_seconds=timeout_seconds,
            event_path=event_path,
        )
    except Exception as exc:
        checks.append(SmokeCheck("swarm_run", False, f"swarm run raised: {_redact(exc)}"))
        return SmokeReport(
            status=STATUS_FAILED,
            checks=tuple(checks),
            work_dir=str(work_dir_path),
            endpoint=endpoint,
            swarm_version=swarm_version,
            failure_cause=CAUSE_SERVER_FAILURE,
        )

    status = getattr(result, "status", "unknown")
    result_error = getattr(result, "error", None)
    result_raw = getattr(result, "raw", {}) or {}
    raw_error = result_raw.get("error") if isinstance(result_raw, dict) else None

    failure_cause: str | None = None
    auth_blob = " ".join(filter(None, [result_error, raw_error, opencode_error]))

    if status == "timeout":
        checks.append(SmokeCheck("swarm_run", False, f"swarm run exceeded {timeout_seconds}s timeout"))
        failure_cause = CAUSE_TIMEOUT
    elif _AUTH_ERROR_RE.search(auth_blob):
        checks.append(SmokeCheck(
            "swarm_run", False,
            f"swarm run failed: authentication failure detected"
            f"{(' (' + _redact(result_error or '') + ')') if result_error else ''}",
        ))
        failure_cause = CAUSE_AUTH_FAILURE
    elif status in ("unavailable", "error"):
        checks.append(SmokeCheck("swarm_run", False,
                                 f"swarm run failed: {_redact(result_error or 'unknown error')}"))
        failure_cause = CAUSE_MISSING_BINARY if "not found" in (result_error or "") else CAUSE_SERVER_FAILURE
    elif status in ("completed", "failed"):
        detail = f"bounded swarm execution finished (status='{status}'"
        if getattr(result, "swarm_id", None):
            detail += f", swarm_id={result.swarm_id}"
        detail += ")"
        if status == "failed" and result_error:
            detail += f"; {_redact(result_error)}"
        checks.append(SmokeCheck("swarm_run", True, detail))
    else:
        checks.append(SmokeCheck("swarm_run", False, f"swarm run returned unknown status {status!r}"))
        failure_cause = CAUSE_SERVER_FAILURE

    # --- result normalization ---------------------------------------------------
    record = normalize_swarm_result(result_raw)
    if record.status != "unknown" and record.error is None:
        checks.append(SmokeCheck(
            "result_normalization", True,
            f"normalized status='{record.status}', agents={len(record.agents)}",
        ))
    else:
        checks.append(SmokeCheck(
            "result_normalization", False,
            f"malformed output: {_redact(record.error or 'swarm returned no recognizable result')}",
        ))
        failure_cause = failure_cause or CAUSE_MALFORMED_OUTPUT

    # --- event ingestion ---------------------------------------------------------
    try:
        ingested = int(runner.ingest_events(result))
    except Exception as exc:
        checks.append(SmokeCheck("event_ingestion", False, f"event ingestion failed: {_redact(exc)}"))
        failure_cause = failure_cause or CAUSE_SERVER_FAILURE
        ingested = 0
    else:
        produced = len(getattr(result, "events", []) or [])
        checks.append(SmokeCheck(
            "event_ingestion", True,
            f"{ingested} event(s) ingested from {produced} produced",
        ))

    # --- classify any leftover failure -------------------------------------------
    if failure_cause is None and any(not c.passed for c in checks):
        failure_cause = CAUSE_SERVER_FAILURE

    overall = STATUS_FAILED if any(not c.passed for c in checks) else STATUS_PASSED
    return SmokeReport(
        status=overall,
        checks=tuple(checks),
        work_dir=str(work_dir_path),
        endpoint=endpoint,
        swarm_version=swarm_version,
        swarm_id=getattr(result, "swarm_id", None),
        ingested_events=ingested,
        failure_cause=failure_cause,
    )


def _default_swarm_health(binary: str) -> Any:
    from src.runtime.swarm_runtime import SwarmRuntime
    return SwarmRuntime(binary=binary).check()


def _default_opencode_health() -> bool:
    from src.runtime.agent_runtime import AgentRuntime
    return AgentRuntime().is_server_healthy()


def _default_runner(binary: str, db_path: Path, work_dir: Path) -> Any:
    from src.runtime.swarm_runtime import SwarmRuntime
    return SwarmRuntime(binary=binary, db_path=db_path, cwd=work_dir)


def _default_endpoint() -> str:
    from src.config import OPENCODE_BASE_URL
    return _endpoint_label(OPENCODE_BASE_URL)


def render_report_text(report: SmokeReport) -> str:
    """Human-readable rendering of a smoke report (used by the CLI)."""
    if report.status == STATUS_SKIPPED:
        lines = ["swarm smoke test: SKIPPED"]
        if report.skip_reason:
            lines.append(f"  {report.skip_reason}")
        if report.failure_cause:
            lines.append(f"  cause: {report.failure_cause}")
        return "\n".join(lines)

    lines = [f"swarm smoke test: {report.status.upper()}"]
    if report.endpoint:
        lines.append(f"  endpoint: {report.endpoint}")
    if report.swarm_version:
        lines.append(f"  swarm version: {report.swarm_version}")
    for check in report.checks:
        flag = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{flag}] {check.name}: {check.detail}")
    if report.swarm_id:
        lines.append(f"  swarm id: {report.swarm_id}")
    if report.ingested_events is not None:
        lines.append(f"  events ingested: {report.ingested_events}")
    if report.work_dir:
        lines.append(f"  work dir (temporary): {report.work_dir}")
    if report.failure_cause:
        lines.append(f"  failure cause: {report.failure_cause}")
    return "\n".join(lines)


def report_to_dict(report: SmokeReport) -> dict[str, Any]:
    """Machine-readable view of a smoke report (used by the CLI --json)."""
    return {
        "status": report.status,
        "skip_reason": report.skip_reason,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in report.checks
        ],
        "work_dir": report.work_dir,
        "endpoint": report.endpoint,
        "swarm_version": report.swarm_version,
        "swarm_id": report.swarm_id,
        "ingested_events": report.ingested_events,
        "failure_cause": report.failure_cause,
    }
