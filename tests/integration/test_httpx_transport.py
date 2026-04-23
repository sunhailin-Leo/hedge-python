"""Integration tests for HedgedHttpxTransport."""

import asyncio

import httpx
import pytest

from hedge import HedgeConfig
from hedge.transport._httpx import HedgedHttpxTransport


class SlowTransport(httpx.AsyncBaseTransport):
    """A mock transport that simulates variable latency."""

    def __init__(self, normal_delay: float = 0.01, straggler_delay: float = 0.5, straggler_rate: float = 0.0):
        self._normal_delay = normal_delay
        self._straggler_delay = straggler_delay
        self._straggler_rate = straggler_rate
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._call_count += 1
        call_num = self._call_count
        # Every Nth request is a straggler based on rate
        is_straggler = self._straggler_rate > 0 and (call_num % int(1 / self._straggler_rate) == 0)
        delay = self._straggler_delay if is_straggler else self._normal_delay
        await asyncio.sleep(delay)
        return httpx.Response(200, text=f"response-{call_num}")


class AlwaysSlowTransport(httpx.AsyncBaseTransport):
    """A transport where every request is slow."""

    def __init__(self, delay: float = 1.0):
        self._delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self._delay)
        return httpx.Response(200, text="slow-response")


class FailingTransport(httpx.AsyncBaseTransport):
    """A transport that alternates between fast success and slow failure."""

    def __init__(self):
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._call_count += 1
        if self._call_count % 2 == 0:
            await asyncio.sleep(0.5)
            raise httpx.ConnectError("connection failed")
        await asyncio.sleep(0.01)
        return httpx.Response(200, text="ok")


@pytest.mark.integration
@pytest.mark.asyncio
class TestHedgedHttpxTransport:
    async def test_basic_request(self) -> None:
        """Verify that a basic request passes through successfully."""
        inner = SlowTransport(normal_delay=0.001)
        config = HedgeConfig(min_delay=0.001, warmup_requests=0)
        transport = HedgedHttpxTransport(inner=inner, config=config)

        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("http://testhost/path")
            assert resp.status_code == 200
            assert "response" in resp.text

        await transport.aclose()

    async def test_stats_tracking(self) -> None:
        """Verify that stats are properly tracked."""
        inner = SlowTransport(normal_delay=0.001)
        config = HedgeConfig(min_delay=0.001, warmup_requests=0)
        transport = HedgedHttpxTransport(inner=inner, config=config)

        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(5):
                await client.get("http://testhost/path")

        snap = transport.stats.snapshot()
        assert snap.total_requests == 5
        await transport.aclose()

    async def test_hedge_fires_on_slow_primary(self) -> None:
        """When the primary is slow, a hedge should fire and win."""
        call_count = 0

        class PrimarySlowTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                nonlocal call_count
                call_count += 1
                current = call_count
                if current <= 25:
                    # Warmup: fast responses
                    await asyncio.sleep(0.001)
                elif current % 2 == 0:
                    # Even calls (primary for hedged requests): slow
                    await asyncio.sleep(1.0)
                else:
                    # Odd calls (hedge requests): fast
                    await asyncio.sleep(0.001)
                return httpx.Response(200, text=f"resp-{current}")

        config = HedgeConfig(
            percentile=0.90,
            min_delay=0.001,
            warmup_requests=20,
            warmup_delay=0.005,
            budget_percent=50.0,
            estimated_rps=1000.0,
        )
        transport = HedgedHttpxTransport(inner=PrimarySlowTransport(), config=config)

        async with httpx.AsyncClient(transport=transport) as client:
            # Warmup requests
            for _ in range(25):
                await client.get("http://testhost/path")
            # Now trigger hedging
            for _ in range(5):
                await client.get("http://testhost/path")

        snap = transport.stats.snapshot()
        assert snap.total_requests == 30
        assert snap.hedged_requests > 0
        await transport.aclose()

    async def test_budget_exhaustion(self) -> None:
        """When the budget is exhausted, no more hedges should fire.

        Issues requests concurrently so many reach the hedge decision while
        the bucket is empty; the first acquires the single starting token,
        the rest are blocked and increment budget_exhausted.
        """
        config = HedgeConfig(
            min_delay=0.001,
            warmup_requests=0,
            warmup_delay=0.001,
            budget_percent=0.01,
            estimated_rps=0.01,  # 0.000001 tokens/s, max_burst=1
        )
        inner = AlwaysSlowTransport(delay=0.05)
        transport = HedgedHttpxTransport(inner=inner, config=config)

        async with httpx.AsyncClient(transport=transport) as client:

            async def fire_one() -> None:
                await client.get("http://testhost/path")

            await asyncio.gather(*[fire_one() for _ in range(10)])

        snap = transport.stats.snapshot()
        # At least one hedge should have been attempted and at least one blocked
        assert snap.hedged_requests >= 1
        assert snap.budget_exhausted >= 1
        await transport.aclose()

    async def test_no_hedge_on_post(self) -> None:
        """POST requests should not be hedged (non-idempotent)."""
        call_count = 0

        class CountingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.1)
                return httpx.Response(200)

        config = HedgeConfig(min_delay=0.001, warmup_requests=0, warmup_delay=0.001)
        transport = HedgedHttpxTransport(inner=CountingTransport(), config=config)

        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("http://testhost/path", content=b"data")

        snap = transport.stats.snapshot()
        assert snap.hedged_requests == 0
        await transport.aclose()

    async def test_default_transport(self) -> None:
        """Creating transport without inner should use default."""
        transport = HedgedHttpxTransport()
        assert transport._inner is not None
        await transport.aclose()

    async def test_default_config(self) -> None:
        """Creating transport without config should use defaults."""
        inner = SlowTransport(normal_delay=0.001)
        transport = HedgedHttpxTransport(inner=inner)
        assert transport._config.percentile == 0.90
        await transport.aclose()
