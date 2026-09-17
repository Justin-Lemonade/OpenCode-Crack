"""
Subagent-efficiency decision helper (roadmap D-191).

Mechanical, offline implementation of the delegation-value rule from
.agent_prompts/subagent_efficiency_policy.md. Given explicit task
estimates it returns a deterministic recommendation — ``DELEGATE``,
``DO_PRIMARY``, or ``DO_NOT_DELEGATE_SAFETY`` — plus the arithmetic and
reasons that produced it.

Policy rules implemented (evaluated in this order):

1. Safety override: a task that touches a delegation-rejection category
   (architecture, security/authentication, secret handling, persistence
   contracts, provider/router policy, core memory semantics, migrations)
   is never delegated, regardless of the token arithmetic.
2. Verifiability gate: work that is not independently verifiable stays
   with the primary agent — reviewing a delegated result would cost
   nearly as much as doing the work directly.
3. Value + ratio rule: delegate only when the policy's delegation value

       value = primary_context_tokens - delegation_overhead - review_overhead

   is strictly positive AND the avoided primary context is at least
   ``ratio`` times the combined overhead (default 2.0 — the policy's
   "practical" 2x rule). Both marginal-savings and high-review-cost
   inputs therefore correctly stay with the primary agent.

No LLM calls, no task-board access, no dispatching: the caller decides
what to do with the recommendation. The ratio is a parameter so policy
tuning never requires code changes.

Examples:

    >>> decide_delegation(4_000, 300, 200, True, False).recommendation
    'DELEGATE'
    >>> decide_delegation(250, 100, 100, True, False).recommendation
    'DO_PRIMARY'
    >>> decide_delegation(90_000, 100, 100, True, True).recommendation
    'DO_NOT_DELEGATE_SAFETY'
"""
from __future__ import annotations

from dataclasses import dataclass

DELEGATE = "DELEGATE"
DO_PRIMARY = "DO_PRIMARY"
DO_NOT_DELEGATE_SAFETY = "DO_NOT_DELEGATE_SAFETY"

# The policy's practical default: prefer delegation when avoided primary
# context is at least 2x the combined delegation + review cost.
DEFAULT_RATIO = 2.0


@dataclass(frozen=True)
class DelegationDecision:
    """Deterministic outcome of :func:`decide_delegation`.

    ``reasons`` is ordered (safety -> verifiability -> value -> ratio) so
    the first entry always names the rule that decided the outcome.
    """

    recommendation: str
    primary_context_tokens: int
    delegation_overhead_tokens: int
    review_overhead_tokens: int
    delegation_value: int
    combined_overhead: int
    meets_ratio: bool
    independently_verifiable: bool
    touches_rejection_category: bool
    reasons: tuple[str, ...]


def decide_delegation(
    primary_context_tokens: int,
    delegation_overhead_tokens: int,
    review_overhead_tokens: int,
    independently_verifiable: bool,
    touches_rejection_category: bool,
    *,
    ratio: float = DEFAULT_RATIO,
) -> DelegationDecision:
    """Return the policy recommendation for one explicitly estimated task.

    All token figures are caller-supplied estimates (never measured
    here); negative values are rejected because they indicate a broken
    estimator rather than a real task shape.
    """
    for name, value in (
        ("primary_context_tokens", primary_context_tokens),
        ("delegation_overhead_tokens", delegation_overhead_tokens),
        ("review_overhead_tokens", review_overhead_tokens),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value}")
    if ratio <= 0:
        raise ValueError(f"ratio must be positive, got {ratio}")

    combined = delegation_overhead_tokens + review_overhead_tokens
    value = primary_context_tokens - combined
    # With zero overhead any positive avoidance clears the ratio check;
    # the value>0 requirement below still guards the degenerate case.
    meets_ratio = value > 0 and (
        combined == 0 or primary_context_tokens >= ratio * combined
    )

    def arithmetic() -> str:
        return (
            f"primary={primary_context_tokens} overhead="
            f"{delegation_overhead_tokens}+{review_overhead_tokens}"
            f"={combined} value={value} ratio={ratio:g}"
        )

    if touches_rejection_category:
        return DelegationDecision(
            recommendation=DO_NOT_DELEGATE_SAFETY,
            primary_context_tokens=primary_context_tokens,
            delegation_overhead_tokens=delegation_overhead_tokens,
            review_overhead_tokens=review_overhead_tokens,
            delegation_value=value,
            combined_overhead=combined,
            meets_ratio=meets_ratio,
            independently_verifiable=independently_verifiable,
            touches_rejection_category=True,
            reasons=(
                "task touches a delegation-rejection category; safety "
                "overrides token arithmetic",
                arithmetic(),
            ),
        )

    if not independently_verifiable:
        return DelegationDecision(
            recommendation=DO_PRIMARY,
            primary_context_tokens=primary_context_tokens,
            delegation_overhead_tokens=delegation_overhead_tokens,
            review_overhead_tokens=review_overhead_tokens,
            delegation_value=value,
            combined_overhead=combined,
            meets_ratio=meets_ratio,
            independently_verifiable=False,
            touches_rejection_category=False,
            reasons=(
                "work is not independently verifiable; a delegated result "
                "would need nearly as much primary-context review as doing "
                "the work directly",
                arithmetic(),
            ),
        )

    if value <= 0:
        return DelegationDecision(
            recommendation=DO_PRIMARY,
            primary_context_tokens=primary_context_tokens,
            delegation_overhead_tokens=delegation_overhead_tokens,
            review_overhead_tokens=review_overhead_tokens,
            delegation_value=value,
            combined_overhead=combined,
            meets_ratio=False,
            independently_verifiable=True,
            touches_rejection_category=False,
            reasons=(
                f"delegation value {value} is not positive",
                arithmetic(),
            ),
        )

    if not meets_ratio:
        return DelegationDecision(
            recommendation=DO_PRIMARY,
            primary_context_tokens=primary_context_tokens,
            delegation_overhead_tokens=delegation_overhead_tokens,
            review_overhead_tokens=review_overhead_tokens,
            delegation_value=value,
            combined_overhead=combined,
            meets_ratio=False,
            independently_verifiable=True,
            touches_rejection_category=False,
            reasons=(
                f"savings are marginal: avoided {primary_context_tokens} < "
                f"{ratio:g}x combined overhead {combined}",
                arithmetic(),
            ),
        )

    return DelegationDecision(
        recommendation=DELEGATE,
        primary_context_tokens=primary_context_tokens,
        delegation_overhead_tokens=delegation_overhead_tokens,
        review_overhead_tokens=review_overhead_tokens,
        delegation_value=value,
        combined_overhead=combined,
        meets_ratio=True,
        independently_verifiable=True,
        touches_rejection_category=False,
        reasons=(
            f"positive delegation value {value} with avoided context at "
            f"least {ratio:g}x combined overhead",
            arithmetic(),
        ),
    )
