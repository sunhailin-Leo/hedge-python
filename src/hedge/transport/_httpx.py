"""Hedged transport for httpx.AsyncClient.

Usage::

    import httpx
    from hedge import HedgeConfig
    from hedge.transport import HedgedHttpxTransport

    transport = HedgedHttpxTransport(config=HedgeConfig())
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://api.example.com/data")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import httpx
except ImportError as exc:
    raise ImportError(
        "httpx is required for HedgedHttpxTransport. Install it with: pip install hedge-python[httpx]"
    ) from exc

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler

if TYPE_CHECKING:
    from hedge._stats import Stats


class HedgedHttpxTransport(httpx.AsyncBaseTransport):
    """An httpx async transport that adds adaptive hedged requests.

    Wraps an inner transport (default: ``httpx.AsyncHTTPTransport``) and
    races a backup request when the primary exceeds its estimated latency
    percentile.

    Args:
        inner: The underlying transport to wrap. Defaults to a new
            ``httpx.AsyncHTTPTransport()``.
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        config: HedgeConfig | None = None,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._config = config or HedgeConfig()
        self._scheduler = HedgeScheduler(self._config)

    @property
    def stats(self) -> Stats:
        """Access the live Stats object."""
        return self._scheduler.stats

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle an outgoing request with adaptive hedging."""
        host = str(request.url.host)
        sketch = self._scheduler.sketch_for(host)

        can_hedge = request.method.upper() in ("GET", "HEAD", "OPTIONS")

        async def do_request() -> httpx.Response:
            return await self._inner.handle_async_request(request)

        def record_latency(response: httpx.Response, elapsed: float) -> None:
            sketch.add(elapsed)

        return await self._scheduler.execute_with_hedge(
            host=host,
            primary_func=do_request,
            hedge_func=do_request,
            record_latency=record_latency,
            can_hedge=can_hedge,
        )

    async def aclose(self) -> None:
        """Close the transport and stop background tasks."""
        await self._scheduler.close()
        await self._inner.aclose()
