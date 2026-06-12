"""Unit tests for the HedgeScheduler base logic."""

import asyncio

import pytest

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler, extract_host


class TestExtractHost:
    def test_simple_url(self) -> None:
        assert extract_host("https://example.com/path") == "example.com"

    def test_url_with_port(self) -> None:
        assert extract_host("https://example.com:8080/path") == "example.com:8080"

    def test_strips_userinfo(self) -> None:
        assert extract_host("https://user:pass@example.com/path") == "example.com"

    def test_strips_userinfo_with_port(self) -> None:
        assert extract_host("https://user:pass@example.com:443/path") == "example.com:443"

    def test_ipv4(self) -> None:
        assert extract_host("http://192.168.1.1:3000/api") == "192.168.1.1:3000"

    def test_ipv6_no_port(self) -> None:
        assert extract_host("http://[::1]/path") == "::1"

    def test_ipv6_with_port(self) -> None:
        assert extract_host("http://[::1]:8080/path") == "[::1]:8080"

    def test_ipv6_full_with_port(self) -> None:
        assert extract_host("http://[2001:db8::1]:9090/api") == "[2001:db8::1]:9090"

    def test_fallback_to_raw_url(self) -> None:
        assert extract_host("not-a-url") == "not-a-url"

    def test_invalid_port_falls_back_to_hostname(self) -> None:
        assert extract_host("http://example.com:bad/path") == "example.com"


@pytest.mark.asyncio
class TestHedgeScheduler:
    async def test_sketch_creation_per_host(self) -> None:
        """Each host should get its own WindowedSketch."""
        scheduler = HedgeScheduler(HedgeConfig(window_duration=1.0))
        sketch_a = scheduler.sketch_for("host-a")
        sketch_b = scheduler.sketch_for("host-b")
        assert sketch_a is not sketch_b
        assert scheduler.sketch_for("host-a") is sketch_a
        await scheduler.close()

    async def test_counter_increments(self) -> None:
        """Counter should increment per host independently."""
        scheduler = HedgeScheduler(HedgeConfig())
        assert scheduler.increment_counter("host-a") == 1
        assert scheduler.increment_counter("host-a") == 2
        assert scheduler.increment_counter("host-b") == 1
        await scheduler.close()

    async def test_warmup_delay(self) -> None:
        """During warmup, the delay should be the warmup_delay value."""
        config = HedgeConfig(warmup_requests=5, warmup_delay=0.05)
        scheduler = HedgeScheduler(config)
        delay = scheduler.compute_hedge_delay("host-a", 1)
        assert delay == 0.05
        snap = scheduler.stats.snapshot()
        assert snap.warmup_requests == 1
        await scheduler.close()

    async def test_post_warmup_delay(self) -> None:
        """After warmup, delay should come from sketch or fallback to warmup_delay."""
        config = HedgeConfig(warmup_requests=2, warmup_delay=0.01, min_delay=0.001)
        scheduler = HedgeScheduler(config)
        # No data in sketch yet, should fallback
        delay = scheduler.compute_hedge_delay("host-a", 10)
        assert delay == 0.01
        await scheduler.close()

    async def test_min_delay_enforced(self) -> None:
        """Delay should never go below min_delay."""
        config = HedgeConfig(warmup_requests=0, warmup_delay=0.0001, min_delay=0.005)
        scheduler = HedgeScheduler(config)
        sketch = scheduler.sketch_for("host-a")
        sketch.add(0.0001)  # Very fast
        delay = scheduler.compute_hedge_delay("host-a", 100)
        assert delay >= 0.005
        await scheduler.close()

    async def test_execute_primary_wins_fast(self) -> None:
        """If the primary is fast, no hedge should fire."""
        config = HedgeConfig(warmup_requests=0, warmup_delay=1.0, min_delay=1.0)
        scheduler = HedgeScheduler(config)
        sketch = scheduler.sketch_for("host-a")

        async def fast_primary() -> str:
            await asyncio.sleep(0.001)
            return "primary"

        async def slow_hedge() -> str:
            await asyncio.sleep(10.0)
            return "hedge"

        result = await scheduler.execute_with_hedge(
            host="host-a",
            primary_func=fast_primary,
            hedge_func=slow_hedge,
            record_latency=lambda r, t: sketch.add(t),
        )
        assert result == "primary"
        assert scheduler.stats.snapshot().hedged_requests == 0
        await scheduler.close()

    async def test_execute_hedge_wins(self) -> None:
        """If the primary is slow, the hedge should win."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.01,
            min_delay=0.001,
            budget_percent=100.0,
            estimated_rps=1000.0,
        )
        scheduler = HedgeScheduler(config)
        sketch = scheduler.sketch_for("host-a")
        # Pre-fill sketch so delay is computed from data
        for _ in range(30):
            sketch.add(0.005)

        async def slow_primary() -> str:
            await asyncio.sleep(10.0)
            return "primary"

        async def fast_hedge() -> str:
            await asyncio.sleep(0.001)
            return "hedge"

        result = await scheduler.execute_with_hedge(
            host="host-a",
            primary_func=slow_primary,
            hedge_func=fast_hedge,
            record_latency=lambda r, t: sketch.add(t),
        )
        assert result == "hedge"
        snap = scheduler.stats.snapshot()
        assert snap.hedged_requests >= 1
        assert snap.hedge_wins >= 1
        await scheduler.close()

    async def test_execute_no_hedge_when_not_allowed(self) -> None:
        """If can_hedge=False, should wait for primary even if slow."""
        config = HedgeConfig(warmup_requests=0, warmup_delay=0.001, min_delay=0.001)
        scheduler = HedgeScheduler(config)
        sketch = scheduler.sketch_for("host-a")

        async def slow_primary() -> str:
            await asyncio.sleep(0.05)
            return "primary"

        async def fast_hedge() -> str:
            return "hedge"

        result = await scheduler.execute_with_hedge(
            host="host-a",
            primary_func=slow_primary,
            hedge_func=fast_hedge,
            record_latency=lambda r, t: sketch.add(t),
            can_hedge=False,
        )
        assert result == "primary"
        assert scheduler.stats.snapshot().hedged_requests == 0
        await scheduler.close()

    async def test_budget_exhaustion(self) -> None:
        """When budget is exhausted, should wait for primary."""
        config = HedgeConfig(
            warmup_requests=0,
            warmup_delay=0.001,
            min_delay=0.001,
            budget_percent=1.0,
            estimated_rps=0.1,  # Very low: ~0.001 tokens/s
        )
        scheduler = HedgeScheduler(config)
        sketch = scheduler.sketch_for("host-a")

        # Drain the bucket
        while scheduler.budget.try_acquire():
            pass

        async def slow_primary() -> str:
            await asyncio.sleep(0.05)
            return "primary"

        async def fast_hedge() -> str:
            return "hedge"

        result = await scheduler.execute_with_hedge(
            host="host-a",
            primary_func=slow_primary,
            hedge_func=fast_hedge,
            record_latency=lambda r, t: sketch.add(t),
        )
        assert result == "primary"
        assert scheduler.stats.snapshot().budget_exhausted >= 1
        await scheduler.close()

    async def test_close_cleans_up(self) -> None:
        """Close should stop all sketches."""
        scheduler = HedgeScheduler(HedgeConfig(window_duration=1.0))
        scheduler.sketch_for("host-a")
        scheduler.sketch_for("host-b")
        await scheduler.close()
        assert len(scheduler._sketches) == 0
