"""Unit tests for the harder-to-reach branches of the gRPC interceptors.

The integration tests in ``tests/integration/test_grpc_interceptor.py`` cover
the happy paths against a real gRPC server. These unit tests use lightweight
fake ``continuation`` callables to deterministically exercise:

  * the budget-exhausted branch of :class:`HedgedUnaryInterceptor`
  * the loser-cancellation branch of :class:`HedgedUnaryInterceptor`
  * the warmup + budget-exhausted branch of
    :class:`HedgedServerStreamInterceptor`
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import grpc
import pytest

from hedge import HedgeConfig
from hedge.interceptor import HedgedServerStreamInterceptor, HedgedUnaryInterceptor


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _FakeUnaryCall:
    """Awaitable stand-in for ``grpc.aio.UnaryUnaryCall``."""

    def __init__(self, response: Any, delay: float, *, raise_on_cancel: bool = False) -> None:
        self._response = response
        self._delay = delay
        self._cancelled = False
        self._raise_on_cancel = raise_on_cancel

    def __await__(self):  # type: ignore[no-untyped-def]
        return self._wait().__await__()

    async def _wait(self) -> Any:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self._cancelled = True
            raise
        return self._response

    def cancel(self) -> bool:
        self._cancelled = True
        if self._raise_on_cancel:
            raise RuntimeError("simulated cancel failure")
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class _FakeStreamCall:
    """Awaitable / readable stand-in for ``grpc.aio.UnaryStreamCall``."""

    def __init__(self, first_msg: Any, delay: float) -> None:
        self._first_msg = first_msg
        self._delay = delay
        self._cancelled = False

    async def read(self) -> Any:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self._cancelled = True
            raise
        return self._first_msg

    def cancel(self) -> bool:
        self._cancelled = True
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


def _make_call_details() -> Any:
    """Minimal duck-typed ClientCallDetails (only ``method`` is read)."""

    class _Details:
        method = "/Fake/Method"

    return _Details()


# ---------------------------------------------------------------------------
# HedgedUnaryInterceptor unit tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestUnaryInterceptorBranches:
    async def test_budget_exhausted_returns_primary(self) -> None:
        """When the token bucket has no tokens, hedging is skipped."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.001,
            min_delay=0.001,
            budget_percent=0.0,  # bucket capacity = max(0,1) = 1, drains after first try
            estimated_rps=1.0,
        )
        interceptor = HedgedUnaryInterceptor(config=config)
        # Drain the bucket so the next try_acquire returns False.
        while interceptor._budget.try_acquire():
            pass

        async def continuation(_details: Any, _request: Any) -> _FakeUnaryCall:
            return _FakeUnaryCall(response="primary", delay=0.05)

        result = await interceptor.intercept_unary_unary(continuation, _make_call_details(), object())
        assert result == "primary"

        snap = interceptor.stats.snapshot()
        assert snap.total_requests == 1
        assert snap.budget_exhausted == 1
        assert snap.hedged_requests == 0

    async def test_hedge_wins_and_cancels_loser(self) -> None:
        """Hedge should fire, win, and cancel the slow primary call."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.005,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedUnaryInterceptor(config=config)
        slow_calls: list[_FakeUnaryCall] = []
        invocation = {"count": 0}

        async def continuation(_details: Any, _request: Any) -> _FakeUnaryCall:
            invocation["count"] += 1
            if invocation["count"] == 1:
                slow = _FakeUnaryCall(response="primary", delay=0.5)
                slow_calls.append(slow)
                return slow
            return _FakeUnaryCall(response="hedge", delay=0.01)

        result = await interceptor.intercept_unary_unary(continuation, _make_call_details(), object())
        assert result == "hedge"

        snap = interceptor.stats.snapshot()
        assert snap.hedged_requests == 1
        assert snap.hedge_wins == 1
        assert snap.primary_wins == 0
        assert slow_calls[0].cancelled, "loser primary call should have been cancelled"

    async def test_loser_cancel_failure_is_swallowed(self) -> None:
        """If the loser's ``call.cancel()`` raises, the interceptor stays up."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.005,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedUnaryInterceptor(config=config)
        invocation = {"count": 0}

        async def continuation(_details: Any, _request: Any) -> _FakeUnaryCall:
            invocation["count"] += 1
            if invocation["count"] == 1:
                return _FakeUnaryCall(response="primary", delay=0.5, raise_on_cancel=True)
            return _FakeUnaryCall(response="hedge", delay=0.01)

        result = await interceptor.intercept_unary_unary(continuation, _make_call_details(), object())
        assert result == "hedge"

    async def test_primary_wins_when_fast_enough(self) -> None:
        """Primary that returns within ``hedge_delay`` skips the hedge entirely."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.05,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedUnaryInterceptor(config=config)

        async def continuation(_details: Any, _request: Any) -> _FakeUnaryCall:
            return _FakeUnaryCall(response="primary", delay=0.001)

        result = await interceptor.intercept_unary_unary(continuation, _make_call_details(), object())
        assert result == "primary"
        snap = interceptor.stats.snapshot()
        assert snap.hedged_requests == 0


# ---------------------------------------------------------------------------
# HedgedServerStreamInterceptor unit tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestServerStreamInterceptorBranches:
    async def test_budget_exhausted_streams_primary(self) -> None:
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.001,
            min_delay=0.001,
            budget_percent=0.0,
            estimated_rps=1.0,
        )
        interceptor = HedgedServerStreamInterceptor(config=config)
        while interceptor._budget.try_acquire():
            pass

        async def continuation(_details: Any, _request: Any) -> _FakeStreamCall:
            return _FakeStreamCall(first_msg="chunk-1", delay=0.05)

        stream = await interceptor.intercept_unary_stream(continuation, _make_call_details(), object())
        first = await stream.__anext__()
        assert first == "chunk-1"

        snap = interceptor.stats.snapshot()
        assert snap.budget_exhausted == 1
        assert snap.hedged_requests == 0

    async def test_hedge_wins_first_message(self) -> None:
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.005,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedServerStreamInterceptor(config=config)
        invocation = {"count": 0}
        slow_calls: list[_FakeStreamCall] = []

        async def continuation(_details: Any, _request: Any) -> _FakeStreamCall:
            invocation["count"] += 1
            if invocation["count"] == 1:
                slow = _FakeStreamCall(first_msg="primary-first", delay=0.5)
                slow_calls.append(slow)
                return slow
            return _FakeStreamCall(first_msg="hedge-first", delay=0.01)

        stream = await interceptor.intercept_unary_stream(continuation, _make_call_details(), object())
        first = await stream.__anext__()
        assert first == "hedge-first"

        snap = interceptor.stats.snapshot()
        assert snap.hedged_requests == 1
        assert snap.hedge_wins == 1
        assert slow_calls[0].cancelled, "loser stream should have been cancelled"

    async def test_prepended_stream_eof_terminates(self) -> None:
        """``_PrependedStream`` raises StopAsyncIteration on EOF."""
        config = HedgeConfig(warmup_requests=0, min_delay=0.001, warmup_delay=0.05)
        interceptor = HedgedServerStreamInterceptor(config=config)

        # First message is real; subsequent reads return EOF.
        class _OneShotCall:
            def __init__(self) -> None:
                self._reads = 0

            async def read(self) -> Any:
                self._reads += 1
                if self._reads == 1:
                    return "only-msg"
                return grpc.aio.EOF

            def cancel(self) -> bool:
                return True

        async def continuation(_details: Any, _request: Any) -> _OneShotCall:
            return _OneShotCall()

        stream = await interceptor.intercept_unary_stream(continuation, _make_call_details(), object())
        collected = []
        async for msg in stream:
            collected.append(msg)
        assert collected == ["only-msg"]

    async def test_prepended_stream_first_msg_eof_raises_stop(self) -> None:
        """When the very first message is EOF, iteration stops immediately."""
        from hedge.interceptor._grpc import _PrependedStream

        mock_call = AsyncMock()
        stream = _PrependedStream(first_msg=grpc.aio.EOF, call=mock_call)

        collected = []
        async for msg in stream:
            collected.append(msg)
        assert collected == []

    async def test_unary_current_task_none_skips_call_holder(self) -> None:
        """When asyncio.current_task() returns None, call_holder is empty
        and the loser cancel branch gracefully skips."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.005,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedUnaryInterceptor(config=config)
        invocation = {"count": 0}

        async def continuation(_details: Any, _request: Any) -> _FakeUnaryCall:
            invocation["count"] += 1
            if invocation["count"] == 1:
                return _FakeUnaryCall(response="primary", delay=0.5)
            return _FakeUnaryCall(response="hedge", delay=0.01)

        with patch("asyncio.current_task", return_value=None):
            result = await interceptor.intercept_unary_unary(continuation, _make_call_details(), object())
        assert result == "hedge"

    async def test_stream_current_task_none_skips_call_holder(self) -> None:
        """When asyncio.current_task() returns None in stream interceptor,
        call_holder is empty and the loser cancel branch gracefully skips."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.005,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        interceptor = HedgedServerStreamInterceptor(config=config)
        invocation = {"count": 0}

        async def continuation(_details: Any, _request: Any) -> _FakeStreamCall:
            invocation["count"] += 1
            if invocation["count"] == 1:
                return _FakeStreamCall(first_msg="primary-first", delay=0.5)
            return _FakeStreamCall(first_msg="hedge-first", delay=0.01)

        with patch("asyncio.current_task", return_value=None):
            stream = await interceptor.intercept_unary_stream(continuation, _make_call_details(), object())
            first = await stream.__anext__()
        assert first == "hedge-first"
