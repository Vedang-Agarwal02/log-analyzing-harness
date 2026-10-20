import random

import pytest

from investigator.harness.budget import Budget, BudgetExceeded, BudgetLimits
from investigator.harness.llm import Usage
from investigator.harness.retry import CircuitBreaker, RetryPolicy


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_retry_delays_double_and_cap_without_jitter() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=4.0, jitter=0.0)
    rng = random.Random(0)
    assert [policy.delay_for(n, rng) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]


def test_retry_jitter_stays_within_band() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=8.0, jitter=0.5)
    rng = random.Random(1)
    for _ in range(50):
        assert 0.75 <= policy.delay_for(1, rng) <= 1.25


def test_circuit_breaker_opens_and_recovers_after_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=2, cooldown_s=10.0, clock=clock)
    assert breaker.allow("logs:a")
    breaker.record_failure("logs:a")
    assert breaker.allow("logs:a")
    breaker.record_failure("logs:a")
    assert breaker.is_open("logs:a") and not breaker.allow("logs:a")
    assert breaker.allow("logs:b")
    clock.now = 10.0
    assert breaker.allow("logs:a") and not breaker.is_open("logs:a")
    breaker.record_failure("logs:a")
    breaker.record_success("logs:a")
    breaker.record_failure("logs:a")
    assert breaker.allow("logs:a")


def test_budget_tokens_respect_reserve() -> None:
    budget = Budget(
        BudgetLimits(
            max_total_tokens=1000, reserve_tokens=300, max_llm_calls=100, reserve_llm_calls=0
        )
    )
    budget.record_llm_call(Usage(input_tokens=600, output_tokens=100))
    with pytest.raises(BudgetExceeded) as caught:
        budget.check()
    assert caught.value.kind == "tokens"
    budget.check(use_reserve=True)
    assert budget.exhausted() and not budget.exhausted(use_reserve=True)
    budget.record_llm_call(Usage(input_tokens=300))
    assert budget.exhausted(use_reserve=True)


def test_budget_llm_calls_tool_calls_and_wall_clock() -> None:
    clock = FakeClock()
    limits = BudgetLimits(
        max_llm_calls=3,
        reserve_llm_calls=1,
        max_tool_calls=2,
        max_wall_seconds=100,
        reserve_seconds=20,
    )
    budget = Budget(limits, clock=clock)
    budget.record_llm_call(Usage())
    budget.record_llm_call(Usage())
    with pytest.raises(BudgetExceeded, match="llm_calls"):
        budget.check()
    budget.check(use_reserve=True)
    budget.record_tool_call()
    budget.record_tool_call()
    with pytest.raises(BudgetExceeded) as caught:
        budget.check_tool_call()
    assert caught.value.kind == "tool_calls"
    fresh = Budget(BudgetLimits(max_wall_seconds=100, reserve_seconds=20), clock=clock)
    clock.now = 85.0
    with pytest.raises(BudgetExceeded, match="wall_clock"):
        fresh.check()
    fresh.check(use_reserve=True)
    snapshot = fresh.snapshot()
    assert snapshot["elapsed_s"] == 85.0 and snapshot["llm_calls"] == 0
