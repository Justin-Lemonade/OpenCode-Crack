"""Live OpenCode parent/child experiment harness.

This script talks to a real OpenCode HTTP server. It does not simulate child
sessions or infer concurrency from session creation alone.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ChildObservation:
    child_id: str
    prompt_started_at: float
    prompt_finished_at: float | None = None
    execution_start: float | None = None
    execution_end: float | None = None
    status: str = "created"
    result: Any = None
    error: str | None = None


def request_json(base_url: str, method: str, path: str, body: dict | None = None) -> Any:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=payload,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenCode {method} {path} failed: {exc}") from exc


def collect_events(base_url: str, events: list[dict], stop: threading.Event) -> None:
    """Collect raw SSE events until the experiment finishes or the server closes."""
    request = urllib.request.Request(base_url.rstrip("/") + "/global/event", headers={"Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            event_lines: list[str] = []
            while not stop.is_set():
                line = response.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").strip()
                if decoded.startswith("data:"):
                    event_lines.append(decoded[5:].strip())
                elif not decoded and event_lines:
                    raw = "\n".join(event_lines)
                    try:
                        events.append({"observed_at": time.time(), "payload": json.loads(raw)})
                    except json.JSONDecodeError:
                        events.append({"observed_at": time.time(), "raw": raw})
                    event_lines.clear()
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def read_child_state(base_url: str, child_id: str) -> tuple[dict, list[dict]]:
    """Read the documented session state and message history for one child."""
    state = request_json(base_url, "GET", f"/api/session/{child_id}")
    messages = request_json(base_url, "GET", f"/api/session/{child_id}/message")
    return state, messages.get("data", []) if isinstance(messages, dict) else []


def delete_session(base_url: str, session_id: str) -> str | None:
    try:
        request_json(base_url, "DELETE", f"/session/{session_id}")
    except RuntimeError as exc:
        return str(exc)
    return None


def peak_overlap(intervals: list[tuple[float, float]]) -> int:
    """Calculate peak simultaneous execution from observed intervals."""
    events: list[tuple[float, int]] = []
    for start, end in intervals:
        if end < start:
            raise ValueError("execution interval ends before it starts")
        events.extend(((start, 1), (end, -1)))
    current = peak = 0
    # End events must be processed before start events at the same timestamp.
    for timestamp, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        peak = max(peak, current)
    return peak


def create_session(base_url: str, *, parent_id: str | None, title: str, model: str | None = None) -> dict:
    body: dict[str, Any] = {"title": title}
    if parent_id:
        body["parentID"] = parent_id
    if model:
        provider_id, model_id = model.split("/", 1)
        body["model"] = {"providerID": provider_id, "id": model_id}
    return request_json(base_url, "POST", "/session", body)


def prompt_child(base_url: str, child_id: str, child_number: int) -> dict:
    prompt = (
        f"You are live experiment child-{child_number:02d}. Do not use tools. "
        "Reply with exactly one JSON object and no explanation: "
        f'{{"child_id":"child-{child_number:02d}","result":6765,"status":"success"}}.'
    )
    return request_json(
        base_url,
        "POST",
        f"/api/session/{child_id}/prompt",
        {"prompt": {"text": prompt}},
    )


def run_experiment(base_url: str, level: int, output_path: Path, wait_seconds: int = 20, model: str | None = None) -> dict:
    started = time.time()
    events: list[dict] = []
    stop_events = threading.Event()
    event_thread = threading.Thread(target=collect_events, args=(base_url, events, stop_events), daemon=True)
    event_thread.start()
    parent = create_session(base_url, parent_id=None, title=f"AI-Brain live parent level {level}", model=model)
    parent_id = parent.get("id")
    if not parent_id:
        raise RuntimeError(f"OpenCode returned no parent session id: {parent!r}")

    children: list[ChildObservation] = []
    raw_prompts: list[dict] = []
    for index in range(level):
        child = create_session(
            base_url,
            parent_id=parent_id,
            title=f"AI-Brain live child {index + 1:02d} level {level}",
            model=model,
        )
        child_id = child.get("id")
        if not child_id:
            raise RuntimeError(f"OpenCode returned no child session id: {child!r}")
        observation = ChildObservation(child_id=child_id, prompt_started_at=time.time())
        try:
            raw_prompts.append(prompt_child(base_url, child_id, index + 1))
            observation.status = "prompted"
        except RuntimeError as exc:
            observation.status = "failed"
            observation.error = str(exc)
        observation.prompt_finished_at = time.time()
        children.append(observation)

    time.sleep(wait_seconds)
    child_states: dict[str, dict] = {}
    child_messages: dict[str, list[dict]] = {}
    for child in children:
        try:
            child_states[child.child_id], child_messages[child.child_id] = read_child_state(base_url, child.child_id)
        except RuntimeError as exc:
            child.error = str(exc)
            child.status = "inspection_failed"
    stop_events.set()
    for child in children:
        statuses = [
            event for event in events
            if event.get("payload", {}).get("payload", {}).get("type") == "session.status"
            and event.get("payload", {}).get("payload", {}).get("properties", {}).get("sessionID") == child.child_id
        ]
        busy_events = [
            event for event in statuses
            if event["payload"]["payload"]["properties"]["status"].get("type") == "busy"
        ]
        idle_events = [
            event for event in statuses
            if event["payload"]["payload"]["properties"]["status"].get("type") == "idle"
        ]
        if busy_events:
            child.execution_start = busy_events[0]["observed_at"]
            child.status = "executing"
        if idle_events:
            child.execution_end = idle_events[-1]["observed_at"]
            child.status = "completed"

    busy_intervals = [
        (child.execution_start, child.execution_end or time.time())
        for child in children
        if child.execution_start is not None
    ]
    result = {
        "schema": "opencode-parent-child-experiment.v1",
        "measured": True,
        "warning": "A prompt accepted by HTTP is not execution proof. Claim concurrency only from child result timestamps or runtime events.",
        "server": base_url,
        "model_requested": model,
        "level": level,
        "parent": parent,
        "children": [asdict(child) for child in children],
        "raw_prompt_responses": raw_prompts,
        "requested": level,
        "created": len(children),
        "prompted": sum(child.status == "prompted" for child in children),
        "failed": sum(child.status == "failed" for child in children),
        "started_at": started,
        "finished_at": time.time(),
        "peak_concurrent_from_observed_intervals": None,
        "peak_busy_observed_lower_bound": peak_overlap(busy_intervals) if busy_intervals else None,
        "events": [],
        "child_states": child_states,
        "child_messages": child_messages,
    }
    result["events"] = events
    intervals = [
        (child["execution_start"], child["execution_end"])
        for child in result["children"]
        if child["execution_start"] is not None and child["execution_end"] is not None
    ]
    result["peak_concurrent_from_observed_intervals"] = peak_overlap(intervals) if intervals else None
    cleanup_errors = []
    for child in children:
        error = delete_session(base_url, child.child_id)
        if error:
            cleanup_errors.append({"session_id": child.child_id, "error": error})
    error = delete_session(base_url, parent_id)
    if error:
        cleanup_errors.append({"session_id": parent_id, "error": error})
    result["cleanup_errors"] = cleanup_errors
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--level", type=int, choices=(1, 2, 4, 8, 12, 16))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--model", default="opencode/big-pickle")
    args = parser.parse_args()
    try:
        result = run_experiment(args.base_url, args.level, args.output, args.wait_seconds, args.model)
    except RuntimeError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ("parent", "requested", "created", "prompted", "failed")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())