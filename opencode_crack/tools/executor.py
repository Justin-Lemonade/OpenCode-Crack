"""
Safe tool execution (C-080).

ToolRegistry.call() invokes `spec.handler(**kwargs)` directly with no
timeout, no cancellation, and no output-size cap -- a hung or runaway
handler blocks forever, and there's no bound on how much a handler
could log/return. This module wraps a handler call with both, without
touching registry.py's own contract (call() itself is unchanged; this
is a layer execute_tool() uses instead of calling registry.call()
directly).

Uses a worker thread rather than signal.alarm() so this works
regardless of what thread execute_tool() is called from (signal-based
timeouts only work on the main thread) -- correct for a Telegram bot
handler, a CLI invocation, or a future async caller alike.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any, Optional

# Output summaries in the audit log are capped hard -- a runaway tool
# must never be able to blow up log/storage size. This is a size limit
# on the *summary text*, independent of and in addition to whatever
# redaction happens for destructive/sensitive tools (see permissions.py).
MAX_OUTPUT_SUMMARY_CHARS = 2000

DEFAULT_TIMEOUT_SECONDS = 30.0


class ToolTimeout(Exception):
    """Raised when a tool handler exceeds its timeout. The underlying
    thread is NOT force-killed (Python has no safe way to do that to an
    arbitrary running thread) -- it keeps running in the background and
    its eventual result/exception is discarded. This is disclosed
    explicitly rather than pretending the call was cancelled, since a
    destructive handler that "timed out" may still complete later; the
    audit log records outcome='timed_out' precisely so this ambiguity
    is visible rather than silently swallowed."""

    def __init__(self, tool_name: str, timeout_seconds: float):
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Tool {tool_name!r} exceeded its {timeout_seconds}s timeout. "
            "The call may still be running in the background; its result, "
            "if any, will be discarded."
        )


@dataclass
class ExecutionResult:
    value: Any = None
    error: Optional[BaseException] = None
    duration_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.error is None


def summarize_output(value: Any, *, redact: bool = False) -> str:
    """Render a handler's return value as a bounded, loggable string.

    `redact=True` (destructive/sensitive tools) replaces the value
    entirely with a type/length summary rather than any of its actual
    content -- for a destructive or credential-adjacent tool, even a
    truncated real value could leak something the audit log should
    never persist. Non-sensitive tools still get a hard length cap.
    """
    if redact:
        try:
            length_hint = len(value)  # works for str/list/dict/etc.
            return f"<redacted {type(value).__name__}, len={length_hint}>"
        except TypeError:
            return f"<redacted {type(value).__name__}>"

    text = str(value)
    if len(text) > MAX_OUTPUT_SUMMARY_CHARS:
        return text[:MAX_OUTPUT_SUMMARY_CHARS] + f"... [truncated, {len(text)} chars total]"
    return text


_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="tool-exec"
)


def run_with_timeout(fn, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, tool_name: str = "<unknown>", **kwargs):
    """Run `fn(**kwargs)` with a wall-clock timeout. Raises ToolTimeout
    on expiry, or re-raises whatever `fn` itself raised. Returns
    whatever `fn` returns on success."""
    future = _executor.submit(fn, **kwargs)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        raise ToolTimeout(tool_name, timeout_seconds) from None
