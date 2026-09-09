"""httpx + hedge-python: per-endpoint latency profiles (issue #2).

Same host, wildly different endpoints — e.g. a fast ``/fast-lookup`` (~5ms)
and a slow ``/bulk-export`` (~500ms). With the default per-host sketch, a
few bulk-export calls drag the shared p90 estimate up, and the fast endpoint
suddenly waits far too long before hedging. With
``HedgeConfig(key_level="endpoint")`` each host+path pair gets its own
DDSketch, while the ASYNC client — and its single connection pool — stays
shared.

Fully self-contained: the "server" is a mock transport that sleeps by path,
so no network access is needed.

Run::

    uv run python examples/httpx_endpoint_profiles.py
"""

from __future__ import annotations

import asyncio

import httpx

from hedge import HedgeConfig, Stats
from hedge.transport import HedgedHttpxTransport

BASE = "http://api.example.test"
FAST_URL = f"{BASE}/fast-lookup"
SLOW_URL = f"{BASE}/bulk-export"
FAST_DELAY = 0.005
SLOW_DELAY = 0.5
NUM_WARMUP = 25  # enough samples to train the sketches


class PerPathDelayTransport(httpx.AsyncBaseTransport):
    """Mock origin server: latency depends only on the request path."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        delay = FAST_DELAY if request.url.path == "/fast-lookup" else SLOW_DELAY
        await asyncio.sleep(delay)
        return httpx.Response(200, text="ok")


async def run(config: HedgeConfig, label: str) -> None:
    stats = Stats()
    config.stats = stats
    transport = HedgedHttpxTransport(inner=PerPathDelayTransport(), config=config)

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        # Train: alternate fast/slow traffic on the same host.
        for _ in range(NUM_WARMUP):
            await client.get(FAST_URL)
            await client.get(SLOW_URL)

        sketches = transport._scheduler._sketches
        print(f"[{label}] sketch keys: {sorted(sketches)}")
        for key in sorted(sketches):
            print(f"  {key!r}: p90 ≈ {sketches[key].quantile(0.90):.3f}s")

        # A genuinely fast straggler case: primary is instant, so no hedge.
        await client.get(FAST_URL)
        snap = stats.snapshot()
        print(
            f"  totals after training: hedged={snap.hedged_requests}  "
            f"hedge_wins={snap.hedge_wins}  budget_exhausted={snap.budget_exhausted}"
        )


async def main() -> None:
    print("== per-host (default): one sketch pools both endpoints ==")
    await run(HedgeConfig(warmup_requests=0, warmup_delay=0.02, min_delay=0.001), "host")
    print()
    print("== per-endpoint: each host+path learns its own p90 ==")
    await run(
        HedgeConfig(
            key_level="endpoint",
            warmup_requests=0,
            warmup_delay=0.02,
            min_delay=0.001,
        ),
        "endpoint",
    )


if __name__ == "__main__":
    asyncio.run(main())
