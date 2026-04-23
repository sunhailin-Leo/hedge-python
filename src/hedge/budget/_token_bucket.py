"""Token bucket algorithm for controlling hedge request rate."""

from __future__ import annotations

import threading
import time


class TokenBucket:
    """Controls hedge request rate using a token bucket algorithm.

    The bucket refills at ``estimated_rps * budget_percent / 100`` tokens per
    second. During genuine outages the bucket drains and hedging stops,
    preventing the load-doubling spiral that would deepen the incident.

    Args:
        budget_percent: Max hedge rate as percent of total traffic.
        estimated_rps: Expected requests per second.
    """

    def __init__(self, budget_percent: float = 10.0, estimated_rps: float = 100.0) -> None:
        self._lock = threading.Lock()
        self._budget_percent = budget_percent
        self._rate = estimated_rps * (budget_percent / 100.0)
        self._max_burst = max(self._rate * 2, 1.0)
        self._tokens = self._max_burst
        self._last_refill = time.monotonic()

    def try_acquire(self) -> bool:
        """Return True if a hedge token is available, False if over budget."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._last_refill = now

            self._tokens += elapsed * self._rate
            if self._tokens > self._max_burst:
                self._tokens = self._max_burst

            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    def set_rps(self, rps: float) -> None:
        """Update the hedge rate as traffic changes, preserving the budget percentage."""
        with self._lock:
            self._rate = rps * (self._budget_percent / 100.0)
            max_burst = max(self._rate * 2, 1.0)
            self._max_burst = max_burst
            if self._tokens > self._max_burst:
                self._tokens = self._max_burst
