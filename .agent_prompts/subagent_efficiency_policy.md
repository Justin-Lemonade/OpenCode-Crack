# Subagent Use and Token-Efficiency Policy

## Purpose

Use subagents when doing so reduces the amount of high-value context the primary agent must consume while preserving correctness. Delegation is a token-efficiency decision, not a habit and not a way to avoid difficult engineering judgment.

**Concerns:** if a delegation pattern is systematically wasting tokens or
producing worse output, file it on the concerns board (category
`process` or `quality`) — see [`raise_concern.md`](raise_concern.md) —
instead of only fixing it locally for the one session where you noticed it.

## Primary-agent rule

The primary agent should own architecture, security, authentication, secret handling, persistence contracts, provider/router policy, core memory semantics, migrations, and any change whose correctness depends on repository-wide design judgment.

Subagents should handle isolated, bounded, reversible work such as tests, documentation research, log analysis, repository inventory, deterministic fixtures, narrow bug fixes, compatibility checks, and other work with explicit acceptance criteria.

## Token-efficiency decision rule

Before using a subagent, estimate:

`Delegation value = primary-context tokens avoided - delegation overhead - review overhead`

Delegate when the expected value is clearly positive and the task is independently verifiable. As a practical default, prefer delegation when the expected primary-context savings are at least **2x** the combined delegation and review cost.

Do NOT delegate merely because a task is large. Do NOT keep work in the primary context merely because a task is small. Scope, isolation, ambiguity, and reviewability matter more than line count.

## Strong reasons to delegate

- Reading many files or long logs.
- Running repetitive tests or benchmarks.
- Independent research that produces a compact evidence report.
- Multiple independent investigations that can run in parallel.
- Deterministic implementation with a narrow contract and dedicated tests.
- Mechanical cleanup where architectural judgment is not required.

## Strong reasons NOT to delegate

- A design decision has not been made.
- The work changes a security boundary or authentication behavior.
- The work changes core memory/persistence semantics.
- The task requires coordinating many modules whose contracts are still moving.
- A delegated result would need nearly as much primary-context review as doing the work directly.
- Delegation would cause merge contention that costs more than the context saved.

## Parallel subagents

Use parallel subagents only for genuinely independent work. Before parallelizing, check file overlap, shared state, dependencies, and whether the results need to be combined into one design decision. Never parallelize two agents against the same high-conflict file unless the task protocol explicitly provides a safe handoff.

## Evidence-first handoff

Every subagent should return compact evidence: what it inspected, what changed, tests/results, failures, and unresolved questions. The primary agent should consume the report first and fetch raw details only when necessary. Do not paste full logs or entire files into the primary context when a precise summary is sufficient.

## Protocol interaction

All implementation delegated through this policy still follows `AGENTS.md`: claim first, work only inside task scope, write the report under `reports/`, submit for review, and leave approval to the primary agent/human. New tasks that need breaking down should follow `.agent_prompts/task_breakdown.md` and the pre-dispatch critique protocol.
