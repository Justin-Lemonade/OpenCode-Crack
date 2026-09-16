"""Deterministic safety budgets for agent loops (C-022)."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class LoopBudget:
    max_turns: int = 20
    max_tool_calls: int = 100
    max_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be >= 0")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")


@dataclass
class LoopUsage:
    turns: int = 0
    tool_calls: int = 0


class LoopBudgetExceeded(RuntimeError):
    """Raised when an agent would exceed its configured execution budget."""


class LoopGuard:
    """Enforce a bounded execution window for one agent run."""

    def __init__(self, budget: LoopBudget, *, clock=monotonic) -> None:
        self.budget = budget
        self.usage = LoopUsage()
        self._clock = clock
        self._started = clock()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining_seconds(self) -> float:
        return max(0.0, self.budget.max_seconds - self.elapsed_seconds)

    def before_turn(self) -> None:
        if self.usage.turns >= self.budget.max_turns:
            raise LoopBudgetExceeded("maximum agent turns exceeded")
        if self.elapsed_seconds >= self.budget.max_seconds:
            raise LoopBudgetExceeded("maximum agent runtime exceeded")
        self.usage.turns += 1

    def before_tool_call(self) -> None:
        if self.usage.tool_calls >= self.budget.max_tool_calls:
            raise LoopBudgetExceeded("maximum tool calls exceeded")
        if self.elapsed_seconds >= self.budget.max_seconds:
            raise LoopBudgetExceeded("maximum agent runtime exceeded")
        self.usage.tool_calls += 1

    def exhausted(self) -> bool:
        return (
            self.usage.turns >= self.budget.max_turns
            or self.usage.tool_calls >= self.budget.max_tool_calls
            or self.elapsed_seconds >= self.budget.max_seconds
        )
