"""Exponential backoff with jitter, and a per-key circuit breaker."""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.25
    max_delay_s: float = 4.0
    jitter: float = 0.5

    def delay_for(self, attempt: int, rng: random.Random) -> float:
        """Delay before retry number `attempt` (1 = the first retry)."""
        raw = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return raw * (1.0 - self.jitter / 2 + rng.random() * self.jitter)


class CircuitBreaker:
    """Opens for `cooldown_s` after `threshold` consecutive failures of the same key."""

    def __init__(
        self,
        threshold: int = 4,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        if opened is None:
            return True
        if self._clock() - opened >= self._cooldown_s:
            del self._opened_at[key]
            self._failures[key] = 0
            return True
        return False

    def is_open(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        return opened is not None and self._clock() - opened < self._cooldown_s

    def record_success(self, key: str) -> None:
        self._failures[key] = 0
        self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self._threshold:
            self._opened_at[key] = self._clock()
