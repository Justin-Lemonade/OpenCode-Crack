"""Parent-child session contract for the OpenCode serve API (roadmap D-337).

Pure builders, parsers, and classifiers pinning the shapes verified live
against OpenCode 1.17.18 on 2026-09-23 (see reports/D-337_report.md for the
full transcript with session and message IDs). No I/O, no credentials, no
provider calls: the server remains the validator for anything ambiguous,
and these helpers never reject a shape the server accepts.

Verified routes (all observed HTTP 200 unless noted):
  POST /session                      create parent or child
  GET /session/{id}                  independent query of either side
  POST /session/{id}/message         parts shape, adapter send path (200)
  POST /api/session/{id}/prompt      prompt admission shape (200 admitted)
  GET /api/session/{id}/message      outcome polling (200)
  DELETE /session/{id}               removal, returns true (200)

Verified limits (encoded in tests, documented here):
  - A bogus parentID string is ACCEPTED (200): the server records it
    verbatim and creates a dangling link. Only the empty string is
    rejected (400 BadRequest). Never assume a recorded parentID resolves.
  - An empty create body is ACCEPTED (200) with a server-generated title
    of the form New session - <timestamp>. Title is optional server-side.
  - Duplicate titles create DISTINCT sessions: no dedup, no idempotency key.
  - A prompt body missing prompt.text is rejected deterministically
    (400 InvalidRequestError naming prompt.text). An empty-string text is
    ADMITTED (200). Missing and empty are different cases.
  - Unknown session reads and prompts fail deterministically with 404
    JSON session-not-found bodies. They never resemble admitted-then-error.
  - Prompt admission is not execution: a 200 admission is followed by an
    assistant message whose finish field carries the real outcome. A
    provider refusal arrives as finish error milliseconds later.
  - Sessions SURVIVE a server restart relaunched with the same working
    directory, including the parentID link. Deleting a parent CASCADES:
    the child reads back 404 afterwards.
  - The default model is resolved server-side and varies between runs
    (space-bunny-free and big-pickle both observed under provider
    opencode). Never assert a specific default model in tests.
"""
from __future__ import annotations

from typing import Any, Optional


class ContractError(ValueError):
    """A session payload or message list violates the pinned contract."""


def build_parent_create_body(title: str) -> dict[str, Any]:
    """Request body for creating a parent session (faithful pass-through)."""
    return {"title": title}


def build_child_create_body(title: str, parent_id: str) -> dict[str, Any]:
    """Request body for creating a child linked with parentID.

    The parent_id is passed through verbatim: the server accepts any
    non-empty string (including IDs that resolve to nothing) and rejects
    only the empty string, so client-side validation would misrepresent
    the contract and is deliberately absent.
    """
    return {"title": title, "parentID": parent_id}


def parse_session_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the durable identity fields of a created or queried session.

    Raises ContractError when no session id is present: without an id no
    further contract statement is possible.
    """
    session_id = payload.get("id")
    if not session_id:
        raise ContractError("Session payload carries no id: %r" % (payload,))
    return {
        "id": session_id,
        "parent_id": payload.get("parentID"),
        "project_id": payload.get("projectID"),
        "directory": payload.get("directory"),
        "title": payload.get("title"),
    }


def check_same_project(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    """True when parent and child were created under one server directory."""
    return bool(parent.get("project_id")) and parent.get("project_id") == child.get("project_id")


def check_child_link(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    """True when the child record points at exactly this parent id."""
    return bool(child.get("parent_id")) and child.get("parent_id") == parent.get("id")


def _assistant_records(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize assistant messages across both observed shapes.

    Shape A (flat, seen on prompt outcomes): type assistant with top-level
    agent, model{id, providerID}, finish, error, and parts.
    Shape B (nested, seen on message-shape probes): info{role, finish,
    error, modelID, providerID, agent} with sibling parts.
    """
    records: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        if isinstance(info, dict) and info.get("role") == "assistant":
            texts = [
                part.get("text", "")
                for part in message.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            records.append(
                {
                    "model_id": info.get("modelID"),
                    "provider": info.get("providerID"),
                    "agent": info.get("agent"),
                    "finish": info.get("finish"),
                    "error": info.get("error"),
                    "texts": texts,
                }
            )
        elif message.get("type") == "assistant":
            model = message.get("model", {}) or {}
            texts = [
                part.get("text", "")
                for part in message.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            records.append(
                {
                    "model_id": model.get("id"),
                    "provider": model.get("providerID"),
                    "agent": message.get("agent"),
                    "finish": message.get("finish"),
                    "error": message.get("error"),
                    "texts": texts,
                }
            )
    return records


def classify_terminal_outcome(messages: list[dict[str, Any]]) -> str:
    """Classify the polled message list without claiming success early.

    Returns provider-error when any assistant record closed with an error,
    completed only for a non-error finish accompanied by non-blank text,
    and pending otherwise (no terminal assistant record yet). Provider
    refusals and genuine completions share the admitted-then-terminal
    shape, so the finish and error fields are the verdict, never the
    admission itself.
    """
    for record in _assistant_records(messages):
        error = record.get("error")
        if record.get("finish") == "error" or (isinstance(error, dict) and error):
            return "provider-error"
        if record.get("finish") and any(text.strip() for text in record.get("texts", [])):
            return "completed"
    return "pending"


def is_session_not_found(http_status: Optional[int], body_text: str) -> bool:
    """True for the deterministic unknown-session transport failure.

    Observed as HTTP 404 with a JSON body naming the missing session
    (NotFoundError or SessionNotFoundError variants). Anything admitted
    first and failing later is a provider outcome, never this case.
    """
    if http_status != 404:
        return False
    lowered = (body_text or "").lower()
    return "session not found" in lowered or "sessionnotfounderror" in lowered.replace("_", "").replace(" ", "") or "notfounderror" in lowered.replace(" ", "")
