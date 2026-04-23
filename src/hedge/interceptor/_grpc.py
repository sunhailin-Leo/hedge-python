"""Hedged gRPC client interceptors.

Supports both Unary-Unary and Unary-Stream (server streaming) RPCs.

Usage::

    from hedge import HedgeConfig
    from hedge.interceptor import HedgedUnaryInterceptor

    channel = grpc.aio.insecure_channel("localhost:50051")
    hedged_channel = grpc.aio.insecure_channel(
        "localhost:50051",
        interceptors=[HedgedUnaryInterceptor(config=HedgeConfig())],
    )
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import defaultdict
from typing import Any, Callable

try:
    import grpc  # type: ignore[import-untyped]
    import grpc.aio  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "grpcio is required for gRPC interceptors. "
        "Install it with: pip install hedge-python[grpc]"
    ) from exc

from hedge._options import HedgeConfig
from hedge._stats import Stats
from hedge.budget import TokenBucket
from hedge.sketch import WindowedSketch


class HedgedUnaryInterceptor(grpc.aio.UnaryUnaryClientInterceptor):  # type: ignore[misc]
    """gRPC Unary-Unary client interceptor with adaptive hedging.

    Learns per-target latency distributions and fires a backup RPC when the
    primary exceeds its estimated percentile threshold.

    Args:
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
    """

    def __init__(self, config: HedgeConfig | None = None) -> None:
        self._config = config or HedgeConfig()
        self.stats = self._config.stats or Stats()
        self._budget = TokenBucket(self._config.budget_percent, self._config.estimated_rps)
        self._sketches: dict[str, WindowedSketch] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def _sketch_for(self, target: str) -> WindowedSketch:
        if target not in self._sketches:
            sketch = WindowedSketch(0.01, self._config.window_duration)
            sketch.start_async()
            self._sketches[target] = sketch
        return self._sketches[target]

    async def _increment_counter(self, target: str) -> int:
        async with self._lock:
            self._counters[target] += 1
            return self._counters[target]

    async def intercept_unary_unary(
        self,
        continuation: Callable[..., Any],
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        """Intercept a unary-unary RPC with adaptive hedging."""
        self.stats.increment_total()

        target = client_call_details.method
        sketch = self._sketch_for(target)
        request_number = await self._increment_counter(target)

        if request_number <= self._config.warmup_requests:
            self.stats.increment_warmup()
            hedge_delay = self._config.warmup_delay
        else:
            estimate = sketch.quantile(self._config.percentile)
            hedge_delay = estimate if estimate > 0 and not math.isnan(estimate) else self._config.warmup_delay

        hedge_delay = max(hedge_delay, self._config.min_delay)
        start = time.monotonic()

        # We track the live Call object for each in-flight attempt so we can
        # cancel the underlying RPC (not just the asyncio task) when a loser
        # is dropped. ``continuation`` returns a Call object almost
        # immediately; the actual RTT is spent in ``await call``. We must
        # combine both steps inside the task, otherwise the task completes
        # instantly and the hedge timer never fires.
        call_holder: dict[asyncio.Task[Any], Any] = {}

        async def invoke() -> Any:
            call = await continuation(client_call_details, request)
            # Record the Call for the currently running task so the loser
            # branch can cancel the RPC on the wire.
            current = asyncio.current_task()
            if current is not None:
                call_holder[current] = call
            return await call

        # Launch primary
        primary_task = asyncio.create_task(invoke())

        # Wait hedge_delay
        done, _ = await asyncio.wait({primary_task}, timeout=hedge_delay)
        if done:
            response = primary_task.result()
            sketch.add(time.monotonic() - start)
            return response

        # Budget check
        if not self._budget.try_acquire():
            self.stats.increment_budget_exhausted()
            response = await primary_task
            sketch.add(time.monotonic() - start)
            return response

        # Launch hedge
        self.stats.increment_hedged()
        hedge_task = asyncio.create_task(invoke())

        done, pending = await asyncio.wait(
            {primary_task, hedge_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        winner_task = done.pop()
        elapsed = time.monotonic() - start

        # Cancel losers: cancel the underlying gRPC Call first (best-effort),
        # then await the task so we don't leak it.
        for task in pending:
            loser_call = call_holder.get(task)
            if loser_call is not None:
                with contextlib.suppress(Exception):
                    loser_call.cancel()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        if winner_task is primary_task:
            self.stats.increment_primary_wins()
        else:
            self.stats.increment_hedge_wins()

        response = winner_task.result()
        sketch.add(elapsed)
        return response


class HedgedServerStreamInterceptor(grpc.aio.UnaryStreamClientInterceptor):  # type: ignore[misc]
    """gRPC Unary-Stream (server streaming) client interceptor with adaptive hedging.

    Uses time-to-first-message (TTFM) as the hedge signal: if the primary
    stream does not yield its first message within the estimated percentile
    latency, a backup stream is started. Whichever stream yields first wins;
    the loser is cancelled.

    Args:
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
    """

    def __init__(self, config: HedgeConfig | None = None) -> None:
        self._config = config or HedgeConfig()
        self.stats = self._config.stats or Stats()
        self._budget = TokenBucket(self._config.budget_percent, self._config.estimated_rps)
        self._sketches: dict[str, WindowedSketch] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def _sketch_for(self, target: str) -> WindowedSketch:
        if target not in self._sketches:
            sketch = WindowedSketch(0.01, self._config.window_duration)
            sketch.start_async()
            self._sketches[target] = sketch
        return self._sketches[target]

    async def _increment_counter(self, target: str) -> int:
        async with self._lock:
            self._counters[target] += 1
            return self._counters[target]

    async def intercept_unary_stream(
        self,
        continuation: Callable[..., Any],
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        """Intercept a unary-stream RPC with adaptive hedging based on TTFM."""
        self.stats.increment_total()

        target = client_call_details.method
        sketch = self._sketch_for(target)
        request_number = await self._increment_counter(target)

        if request_number <= self._config.warmup_requests:
            self.stats.increment_warmup()
            hedge_delay = self._config.warmup_delay
        else:
            estimate = sketch.quantile(self._config.percentile)
            hedge_delay = estimate if estimate > 0 and not math.isnan(estimate) else self._config.warmup_delay

        hedge_delay = max(hedge_delay, self._config.min_delay)
        start = time.monotonic()

        # Combine ``await continuation`` (which may itself include connection
        # setup / header round-trips) and ``call.read()`` (TTFM) in one task,
        # so the timer reflects the real time-to-first-message.
        call_holder: dict[asyncio.Task[Any], Any] = {}

        async def invoke_and_read_first() -> tuple[Any, Any]:
            call = await continuation(client_call_details, request)
            current = asyncio.current_task()
            if current is not None:
                call_holder[current] = call
            first_msg = await call.read()
            return call, first_msg

        # Launch primary
        primary_task = asyncio.create_task(invoke_and_read_first())

        done, _ = await asyncio.wait({primary_task}, timeout=hedge_delay)
        if done:
            primary_call, first_msg = primary_task.result()
            sketch.add(time.monotonic() - start)
            return _PrependedStream(first_msg, primary_call)

        # Budget check
        if not self._budget.try_acquire():
            self.stats.increment_budget_exhausted()
            primary_call, first_msg = await primary_task
            sketch.add(time.monotonic() - start)
            return _PrependedStream(first_msg, primary_call)

        # Launch hedge
        self.stats.increment_hedged()
        hedge_task = asyncio.create_task(invoke_and_read_first())

        done, pending = await asyncio.wait(
            {primary_task, hedge_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        winner_task = done.pop()
        winner_call, first_msg = winner_task.result()
        elapsed = time.monotonic() - start
        sketch.add(elapsed)

        if winner_task is primary_task:
            self.stats.increment_primary_wins()
        else:
            self.stats.increment_hedge_wins()

        # Cancel loser: cancel the underlying gRPC Call first (best-effort),
        # then await the task so we don't leak it.
        for task in pending:
            loser_call = call_holder.get(task)
            if loser_call is not None:
                with contextlib.suppress(Exception):
                    loser_call.cancel()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        return _PrependedStream(first_msg, winner_call)


class _PrependedStream:
    """Wraps a gRPC stream, prepending an already-read first message.

    This allows the interceptor to consume the first message for TTFM
    measurement while still presenting a complete stream to the caller.
    """

    def __init__(self, first_msg: Any, call: Any) -> None:
        self._first_msg = first_msg
        self._call = call
        self._first_yielded = False

    def __aiter__(self) -> _PrependedStream:
        return self

    async def __anext__(self) -> Any:
        if not self._first_yielded:
            self._first_yielded = True
            if self._first_msg is grpc.aio.EOF:
                raise StopAsyncIteration
            return self._first_msg
        msg = await self._call.read()
        if msg is grpc.aio.EOF:
            raise StopAsyncIteration
        return msg
