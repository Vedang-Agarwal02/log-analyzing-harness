"""Run-wide budget shared by every agent. Part of it is reserved for the final report."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from investigator.harness.llm import Usage


@dataclass(frozen=True)
class BudgetLimits:
    max_total_tokens: int = 1_500_000
    max_llm_calls: int = 80
    max_tool_calls: int = 120
    max_wall_seconds: float = 900.0
    reserve_tokens: int = 150_000
    reserve_llm_calls: int = 8
    reserve_seconds: float = 120.0


class BudgetExceeded(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"budget exhausted ({kind}): {detail}")
        self.kind = kind


class Budget:
    def __init__(self, limits: BudgetLimits, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self._clock = clock
        self._started = clock()
        self.usage = Usage()
        self.llm_calls = 0
        self.tool_calls = 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def check(self, *, use_reserve: bool = False) -> None:
        """Raise BudgetExceeded if another LLM call is not allowed.

        Investigating agents run with use_reserve=False and cannot touch the reserve.
        The reporter (and the single-agent baseline) run with use_reserve=True.
        """
        limits = self.limits
        token_limit = limits.max_total_tokens - (0 if use_reserve else limits.reserve_tokens)
        call_limit = limits.max_llm_calls - (0 if use_reserve else limits.reserve_llm_calls)
        wall_limit = limits.max_wall_seconds - (0 if use_reserve else limits.reserve_seconds)
        if self.usage.total_tokens >= token_limit:
            raise BudgetExceeded(
                "tokens", f"{self.usage.total_tokens} of {token_limit} tokens used"
            )
        if self.llm_calls >= call_limit:
            raise BudgetExceeded("llm_calls", f"{self.llm_calls} of {call_limit} LLM calls used")
        if self.elapsed() >= wall_limit:
            raise BudgetExceeded(
                "wall_clock", f"{self.elapsed():.0f}s of {wall_limit:.0f}s elapsed"
            )

    def check_tool_call(self) -> None:
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExceeded(
                "tool_calls", f"{self.tool_calls} of {self.limits.max_tool_calls} tool calls used"
            )

    def record_llm_call(self, usage: Usage) -> None:
        self.llm_calls += 1
        self.usage = self.usage + usage

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def exhausted(self, *, use_reserve: bool = False) -> bool:
        try:
            self.check(use_reserve=use_reserve)
        except BudgetExceeded:
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_tokens": self.usage.total_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "elapsed_s": round(self.elapsed(), 3),
        }
