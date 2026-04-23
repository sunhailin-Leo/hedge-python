"""Shared async hedge scheduling logic used by all transports."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, TypeVar

from hedge._stats import Stats
from hedge.budget import TokenBucket
from hedge.sketch import WindowedSketch

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from hedge._options import HedgeConfig

T = TypeVar("T")


class HedgeScheduler:
    """Core async hedge scheduling shared across frameworks.

    Manages per-host sketches, request counters, token bucket budget,
    and the race-then-cancel logic.

    This is an internal building block; users should use the framework-specific
    transports (httpx, aiohttp) or interceptors (gRPC).
    """

    def __init__(self, config: HedgeConfig) -> None:
        self.config = config
        self.stats = config.stats or Stats()
        self.budget = TokenBucket(config.budget_percent, config.estimated_rps)
        self._sketches: dict[str, WindowedSketch] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def sketch_for(self, host: str) -> WindowedSketch:
        """Get or create a WindowedSketch for the given host."""
        if host not in self._sketches:
            sketch = WindowedSketch(
                relative_accuracy=0.01,
                window_duration=self.config.window_duration,
            )
            sketch.start_async()
            self._sketches[host] = sketch
        return self._sketches[host]

    async def increment_counter(self, host: str) -> int:
        """Atomically increment and return the request counter for a host."""
        async with self._lock:
            self._counters[host] += 1
            return self._counters[host]

    def compute_hedge_delay(self, host: str, request_number: int) -> float:
        """Compute the hedge delay in seconds for a given host and request number."""
        if request_number <= self.config.warmup_requests:
            self.stats.increment_warmup()
            delay = self.config.warmup_delay
        else:
            sketch = self.sketch_for(host)
            estimate = sketch.quantile(self.config.percentile)
            delay = estimate if estimate > 0 and not math.isnan(estimate) else self.config.warmup_delay

        return max(delay, self.config.min_delay)

    async def execute_with_hedge(
        self,
        host: str,
        primary_func: Callable[[], Awaitable[T]],
        hedge_func: Callable[[], Awaitable[T]],
        record_latency: Callable[[T, float], None],
        can_hedge: bool = True,
    ) -> T:
        """Execute primary request with hedge racing logic.

        Args:
            host: Target host key for per-host sketch tracking.
            primary_func: Async callable that performs the primary request.
            hedge_func: Async callable that performs the hedge request.
            record_latency: Callback to record latency to the sketch.
            can_hedge: Whether the request is safe to hedge (idempotent).

        Returns:
            The result from whichever request finishes first.
        """
        self.stats.increment_total()

        request_number = await self.increment_counter(host)
        hedge_delay = self.compute_hedge_delay(host, request_number)
        start = time.monotonic()

        # Launch primary
        primary_task = asyncio.create_task(primary_func())

        # Wait for hedge delay
        done, _ = await asyncio.wait({primary_task}, timeout=hedge_delay)
        if done:
            result = primary_task.result()
            elapsed = time.monotonic() - start
            record_latency(result, elapsed)
            return result

        # Check if hedging is allowed
        if not can_hedge:
            result = await primary_task
            elapsed = time.monotonic() - start
            record_latency(result, elapsed)
            return result

        # Check budget
        if not self.budget.try_acquire():
            self.stats.increment_budget_exhausted()
            result = await primary_task
            elapsed = time.monotonic() - start
            record_latency(result, elapsed)
            return result

        # Launch hedge
        self.stats.increment_hedged()
        hedge_task = asyncio.create_task(hedge_func())

        done, pending = await asyncio.wait(
            {primary_task, hedge_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        winner_task = done.pop()
        elapsed = time.monotonic() - start

        # Cancel losers
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        if winner_task is primary_task:
            self.stats.increment_primary_wins()
        else:
            self.stats.increment_hedge_wins()

        result = winner_task.result()
        record_latency(result, elapsed)
        return result

    async def close(self) -> None:
        """Stop all background sketch rotation tasks."""
        for sketch in self._sketches.values():
            sketch.stop()
        self._sketches.clear()
