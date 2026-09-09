"""Unit tests for per-endpoint latency profiles (``key_level`` option).

Covers ``extract_key``, scheduler isolation between endpoints on the same
host, and backward compatibility of the default per-host mode.
"""

from __future__ import annotations

import asyncio

import pytest

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler, extract_host, extract_key


class TestExtractKeyHostMode:
    """``key_level="host"`` must behave exactly like the original host keys."""

    def test_matches_extract_host(self) -> None:
        assert extract_key("https://example.com/fast-lookup", "host") == "example.com"

    def test_default_key_level_is_host(self) -> None:
        assert extract_key("https://example.com:8080/path") == extract_host("https://example.com:8080/path")
        assert extract_key("https://example.com:8080/path") == "example.com:8080"

    def test_ips_and_userinfo(self) -> None:
        assert extract_key("http://192.168.1.1:3000/api", "host") == "192.168.1.1:3000"
        assert extract_key("https://user:pass@example.com:443/api", "host") == "example.com:443"
        assert extract_key("http://[::1]:8080/api", "host") == "[::1]:8080"

    def test_slow_and_fast_path_share_sketch_key(self) -> None:
        """The issue #2 scenario: same host, different endpoints, one key."""
        fast_key = extract_key("https://api.example.com/fast-lookup", "host")
        slow_key = extract_key("https://api.example.com/bulk-export", "host")
        assert fast_key == slow_key == "api.example.com"


class TestExtractKeyEndpointMode:
    """``key_level="endpoint"`` keys sketches by host + path."""

    def test_host_plus_path(self) -> None:
        assert extract_key("https://api.example.com/fast-lookup", "endpoint") == "api.example.com /fast-lookup"
        assert extract_key("https://api.example.com/bulk-export", "endpoint") == "api.example.com /bulk-export"

    def test_same_host_different_paths_yield_distinct_keys(self) -> None:
        fast_key = extract_key("https://api.example.com/fast-lookup", "endpoint")
        slow_key = extract_key("https://api.example.com/bulk-export", "endpoint")
        assert fast_key != slow_key

    def test_same_endpoint_same_key(self) -> None:
        assert extract_key("https://api.example.com/fast-lookup", "endpoint") == (
            extract_key("https://api.example.com/fast-lookup", "endpoint")
        )

    def test_query_string_excluded(self) -> None:
        assert extract_key("https://api.example.com/items?page=1&limit=50", "endpoint") == extract_key(
            "https://api.example.com/items?page=2&limit=50", "endpoint"
        )

    def test_fragment_excluded(self) -> None:
        assert extract_key("https://api.example.com/report#section-1", "endpoint") == extract_key(
            "https://api.example.com/report#section-2", "endpoint"
        )

    def test_no_path_normalizes_to_root(self) -> None:
        assert extract_key("https://api.example.com", "endpoint") == "api.example.com /"

    def test_port_userinfo_and_ipv6_preserved(self) -> None:
        assert extract_key("https://api.example.com:8080/v1/lookup", "endpoint") == "api.example.com:8080 /v1/lookup"
        assert extract_key("https://user:pass@example.com:443/v1/lookup", "endpoint") == "example.com:443 /v1/lookup"
        assert extract_key("http://[::1]:8080/health", "endpoint") == "[::1]:8080 /health"

    def test_trailing_slash_is_significant(self) -> None:
        """Paths are used verbatim; /items and /items/ stay distinct keys."""
        assert extract_key("https://api.example.com/items", "endpoint") != (
            extract_key("https://api.example.com/items/", "endpoint")
        )

    def test_invalid_key_level_raises(self) -> None:
        with pytest.raises(ValueError, match="key_level"):
            extract_key("https://api.example.com", "bogus")

    def test_empty_host_falls_back_like_extract_host(self) -> None:
        assert extract_key("not-a-url", "endpoint") == "not-a-url not-a-url"


@pytest.mark.asyncio
class TestSchedulerKeyIsolation:
    """The scheduler must isolate sketches and counters per key."""

    async def test_endpoint_mode_separate_sketches_per_endpoint(self) -> None:
        config = HedgeConfig(key_level="endpoint", window_duration=1.0)
        scheduler = HedgeScheduler(config)
        fast_key = extract_key("https://api.example.com/fast-lookup", "endpoint")
        slow_key = extract_key("https://api.example.com/bulk-export", "endpoint")

        fast_sketch = scheduler.sketch_for(fast_key)
        slow_sketch = scheduler.sketch_for(slow_key)

        assert fast_sketch is not slow_sketch
        assert set(scheduler._sketches) == {fast_key, slow_key}
        await scheduler.close()

    async def test_host_mode_single_sketch_for_same_host(self) -> None:
        config = HedgeConfig(window_duration=1.0)
        scheduler = HedgeScheduler(config)
        fast_key = extract_key("https://api.example.com/fast-lookup")
        slow_key = extract_key("https://api.example.com/bulk-export")

        fast_sketch = scheduler.sketch_for(fast_key)
        slow_sketch = scheduler.sketch_for(slow_key)

        assert fast_sketch is slow_sketch
        await scheduler.close()

    async def test_slow_endpoint_does_not_skew_fast_endpoint_delay(self) -> None:
        """The core issue #2 scenario: a slow endpoint must not raise the
        hedge delay of a fast endpoint on the same host."""
        config = HedgeConfig(
            key_level="endpoint",
            warmup_requests=0,
            min_delay=0.001,
            window_duration=1.0,
        )
        scheduler = HedgeScheduler(config)
        fast_key = "api.example.com /fast-lookup"
        slow_key = "api.example.com /bulk-export"

        for _ in range(30):
            scheduler.sketch_for(fast_key).add(0.01)
            scheduler.sketch_for(slow_key).add(0.9)

        fast_delay = scheduler.compute_hedge_delay(fast_key, 100)
        slow_delay = scheduler.compute_hedge_delay(slow_key, 100)

        # Fast endpoint hedges near ~10ms even though the shared host window
        # contains 900ms samples for the other endpoint.
        assert fast_delay < 0.05
        assert slow_delay > 0.1
        assert fast_delay < slow_delay
        await scheduler.close()

    async def test_host_mode_pools_across_endpoints(self) -> None:
        """Default mode: request numbers are counted per host across endpoints."""
        config = HedgeConfig(window_duration=1.0)
        scheduler = HedgeScheduler(config)

        assert scheduler.increment_counter("api.example.com") == 1
        assert scheduler.increment_counter("api.example.com") == 2

        # Endpoint-mode counters are keyed independently.
        config_ep = HedgeConfig(key_level="endpoint", window_duration=1.0)
        scheduler_ep = HedgeScheduler(config_ep)
        assert scheduler_ep.increment_counter("api.example.com /a") == 1
        assert scheduler_ep.increment_counter("api.example.com /b") == 1
        assert scheduler_ep.increment_counter("api.example.com /a") == 2

        await scheduler.close()
        await scheduler_ep.close()

    async def test_budget_shared_globally_regardless_of_key_level(self) -> None:
        """The token bucket stays per client even in endpoint mode: draining
        it for one endpoint blocks hedging for every other endpoint."""
        config = HedgeConfig(
            key_level="endpoint",
            warmup_requests=0,
            warmup_delay=0.001,
            min_delay=0.001,
            budget_percent=1.0,
            estimated_rps=0.1,  # Very low: ~0.001 tokens/s
        )
        scheduler = HedgeScheduler(config)

        # Drain the bucket for one endpoint
        scheduler.sketch_for("api.example.com /fast-lookup")
        scheduler.sketch_for("api.example.com /bulk-export")
        while scheduler.budget.try_acquire():
            pass

        async def slow_primary() -> str:
            await asyncio.sleep(0.05)
            return "primary"

        async def instant_hedge() -> str:
            return "hedge"

        result = await scheduler.execute_with_hedge(
            key="api.example.com /bulk-export",
            primary_func=slow_primary,
            hedge_func=instant_hedge,
            record_latency=lambda r, t: None,
        )
        assert result == "primary"
        assert scheduler.stats.snapshot().budget_exhausted >= 1
        await scheduler.close()
