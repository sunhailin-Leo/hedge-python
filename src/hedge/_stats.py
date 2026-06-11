"""Thread-safe statistics for hedge operations."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class Stats:
    """Thread-safe counters for hedge operations.

    All fields use a lock for atomic updates and are safe to read concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests: int = 0
        self.hedged_requests: int = 0
        self.hedge_wins: int = 0
        self.primary_wins: int = 0
        self.budget_exhausted: int = 0
        self.warmup_requests: int = 0

    def increment_total(self) -> None:
        with self._lock:
            self.total_requests += 1

    def increment_hedged(self) -> None:
        with self._lock:
            self.hedged_requests += 1

    def increment_hedge_wins(self) -> None:
        with self._lock:
            self.hedge_wins += 1

    def increment_primary_wins(self) -> None:
        with self._lock:
            self.primary_wins += 1

    def increment_budget_exhausted(self) -> None:
        with self._lock:
            self.budget_exhausted += 1

    def increment_warmup(self) -> None:
        with self._lock:
            self.warmup_requests += 1

    def snapshot(self) -> StatsSnapshot:
        """Take a consistent point-in-time copy of all counters."""
        with self._lock:
            return StatsSnapshot(
                total_requests=self.total_requests,
                hedged_requests=self.hedged_requests,
                hedge_wins=self.hedge_wins,
                primary_wins=self.primary_wins,
                budget_exhausted=self.budget_exhausted,
                warmup_requests=self.warmup_requests,
            )

    def hedge_rate(self) -> float:
        """Return hedged_requests / total_requests, or 0.0 if no requests."""
        with self._lock:
            if self.total_requests == 0:
                return 0.0
            return self.hedged_requests / self.total_requests


@dataclass(frozen=True)
class StatsSnapshot:
    """Immutable point-in-time snapshot of Stats."""

    total_requests: int
    hedged_requests: int
    hedge_wins: int
    primary_wins: int
    budget_exhausted: int
    warmup_requests: int
