"""httpx + hedge-python: basic adaptive hedging example.

Wraps an ``httpx.AsyncClient`` with :class:`HedgedHttpxTransport`. The transport
learns the per-host p90 latency from a DDSketch and races a backup request
when the primary exceeds that estimate.

Run::

    uv run python examples/httpx_basic.py

The example issues 50 GET requests against ``https://httpbin.org/delay/0`` with
a tight ``estimated_rps`` so a few hedges actually fire on a real network. At
the end it prints a :class:`Stats` snapshot showing how many hedges ran, how
many won, and the budget usage.
"""

from __future__ import annotations

import asyncio

import httpx

from hedge import HedgeConfig, Stats
from hedge.transport import HedgedHttpxTransport

URL = "https://httpbin.org/delay/0"
NUM_REQUESTS = 50


async def main() -> None:
    stats = Stats()
    config = HedgeConfig(
        percentile=0.90,
        budget_percent=20.0,
        estimated_rps=50.0,
        warmup_requests=10,
        warmup_delay=0.05,
        stats=stats,
    )
    transport = HedgedHttpxTransport(config=config)

    async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
        async def fire(index: int) -> int:
            resp = await client.get(URL, params={"i": str(index)})
            return resp.status_code

        results = await asyncio.gather(*[fire(i) for i in range(NUM_REQUESTS)])

    await transport.aclose()

    snap = stats.snapshot()
    print(f"completed {len(results)} requests, all 200: {all(r == 200 for r in results)}")
    print(
        f"  total={snap.total_requests}  hedged={snap.hedged_requests}  "
        f"hedge_wins={snap.hedge_wins}  primary_wins={snap.primary_wins}  "
        f"budget_exhausted={snap.budget_exhausted}"
    )
    print(f"  hedge_rate={stats.hedge_rate():.2%}")


if __name__ == "__main__":
    asyncio.run(main())
