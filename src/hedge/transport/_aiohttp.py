"""Hedged session wrapper for aiohttp.ClientSession.

Usage::

    from hedge import HedgeConfig
    from hedge.transport import HedgedAiohttpSession

    async with HedgedAiohttpSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        data = await resp.json()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from types import TracebackType

try:
    import aiohttp
except ImportError as exc:
    raise ImportError(
        "aiohttp is required for HedgedAiohttpSession. "
        "Install it with: pip install hedge-python[aiohttp]"
    ) from exc

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler

if TYPE_CHECKING:
    from hedge._stats import Stats


class HedgedAiohttpSession:
    """An aiohttp session wrapper that adds adaptive hedged requests.

    Wraps an ``aiohttp.ClientSession`` and races a backup request when the
    primary exceeds its estimated latency percentile.

    Args:
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
        **session_kwargs: Additional keyword arguments passed to
            ``aiohttp.ClientSession()``.
    """

    def __init__(
        self,
        config: HedgeConfig | None = None,
        **session_kwargs: Any,
    ) -> None:
        self._config = config or HedgeConfig()
        self._session_kwargs = session_kwargs
        self._session: aiohttp.ClientSession | None = None
        self._scheduler = HedgeScheduler(self._config)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(**self._session_kwargs)
        return self._session

    @property
    def stats(self) -> Stats:
        """Access the live Stats object."""
        return self._scheduler.stats

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> aiohttp.ClientResponse:
        """Perform a hedged request."""
        parsed = urlparse(url)
        host = parsed.netloc or parsed.hostname or url
        sketch = self._scheduler.sketch_for(host)

        can_hedge = method.upper() in ("GET", "HEAD", "OPTIONS")
        session = self._get_session()

        async def do_request() -> aiohttp.ClientResponse:
            return await session.request(method, url, **kwargs)

        def record_latency(response: aiohttp.ClientResponse, elapsed: float) -> None:
            sketch.add(elapsed)

        return await self._scheduler.execute_with_hedge(
            host=host,
            primary_func=do_request,
            hedge_func=do_request,
            record_latency=record_latency,
            can_hedge=can_hedge,
        )

    async def get(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a hedged GET request."""
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a POST request (no hedging; body cannot be safely replayed)."""
        return await self._request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a PUT request (no hedging)."""
        return await self._request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a DELETE request (no hedging)."""
        return await self._request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a hedged HEAD request."""
        return await self._request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Perform a hedged OPTIONS request."""
        return await self._request("OPTIONS", url, **kwargs)

    async def close(self) -> None:
        """Close the session and stop background tasks."""
        await self._scheduler.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> HedgedAiohttpSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
