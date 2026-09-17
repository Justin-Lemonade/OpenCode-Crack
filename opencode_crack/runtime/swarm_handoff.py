"""
Convert normalized Swarm results into evidence-first handoffs
(roadmap D-163, from `iteration improvement ideas status.md`).

A pure adapter between the swarm runtime and the D-157 ``Handoff`` format:
takes a normalized swarm run record (``SwarmRunRecord`` from
``swarm_normalize``) plus the known task/report/test evidence the agent
already holds, and produces a ``Handoff`` for the recipient to act on.

Design rules:
  * worker -> manager and manager -> tester are the only directions —
    enforced by ``Handoff`` itself; the adapter never invents others;
  * no narrative duplication: the runtime result is folded into the
    ``outcome`` plus a verbatim ``swarm:<swarm_id>`` reference artifact and
    a verbatim error line (when present); task id, changed paths, test
    evidence, artifacts, known risks and the requested decision all flow
    through unchanged;
  * deterministic outcome mapping: status ``completed`` -> success,
    ``failed`` -> failure, anything else -> blocked;
  * purely functional — no model calls, no DB writes, no subprocesses;
    JSON/Markdown rendering delegates to the existing ``handoff`` renderers
    so the two forms share one field source of truth and stay deterministic.
"""
from __future__ import annotations

from typing import Sequence

from opencode_crack.runtime.handoff import Handoff, TestRun
from opencode_crack.runtime.swarm_normalize import SwarmRunRecord


def outcome_for_status(status: str) -> str:
    """Map a swarm run status to a Handoff outcome deterministically."""
    if status == "completed":
        return "success"
    if status == "failed":
        return "failure"
    return "blocked"


def build_swarm_handoff(
    result: SwarmRunRecord,
    task_id: str,
    sender: str,
    recipient: str,
    decision_requested: str,
    changed_paths: Sequence[str] = (),
    tests: Sequence[TestRun] = (),
    artifacts: Sequence[str] = (),
    known_risks: Sequence[str] = (),
) -> Handoff:
    """Adapt a normalized swarm result + known evidence into a Handoff.

    The runtime result is represented without narrative: the outcome
    derives from ``result.status``, a ``swarm:<swarm_id>`` reference is
    prepended to the artifact list so the reader can open the run, and a
    verbatim ``runtime error: <error>`` line is appended to known risks when
    the run reported one. Everything else (task id, changed paths, test
    evidence, remaining artifacts/risks, requested decision) passes through
    unchanged.
    """
    outcome = outcome_for_status(result.status)

    reference_artifact = f"swarm:{result.swarm_id}" if result.swarm_id else None
    merged_artifacts = list(artifacts)
    if reference_artifact:
        merged_artifacts.insert(0, reference_artifact)

    merged_risks = list(known_risks)
    if result.error:
        merged_risks.append(f"runtime error: {result.error}")

    return Handoff(
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        outcome=outcome,
        decision_requested=decision_requested,
        changed_paths=list(changed_paths),
        tests=list(tests),
        artifacts=merged_artifacts,
        known_risks=merged_risks,
    )