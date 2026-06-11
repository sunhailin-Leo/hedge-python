"""tornado + hedge-python: basic adaptive hedging example.

Uses :class:`HedgedTornadoClient` as a wrapper around
``tornado.httpclient.AsyncHTTPClient``. ``GET`` / ``HEAD`` / ``OPTIONS`` are
hedged; other methods pass through unchanged.

Run::

    uv run python examples/tornado_basic.py
"""

from __future__ import annotations

import asyncio

from hedge import HedgeConfig, Stats
from hedge.transport import HedgedTornadoClient

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

    async with HedgedTornadoClient(config=config) as client:
        async def fire(index: int) -> int:
            resp = await client.fetch(f"{URL}?i={index}")
            return resp.code

        results = await asyncio.gather(*[fire(i) for i in range(NUM_REQUESTS)])

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
