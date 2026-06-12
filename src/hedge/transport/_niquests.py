"""Hedged session wrapper for niquests.AsyncSession.

Usage::

    from hedge import HedgeConfig
    from hedge.transport import HedgedNiquestsSession

    async with HedgedNiquestsSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        print(resp.status_code, resp.text)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType

try:
    import niquests
except ImportError as exc:
    raise ImportError(
        "niquests is required for HedgedNiquestsSession. Install it with: pip install hedge-python[niquests]"
    ) from exc

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler, extract_host

if TYPE_CHECKING:
    from hedge._stats import Stats


class HedgedNiquestsSession:
    """A niquests session wrapper that adds adaptive hedged requests.

    Wraps a ``niquests.AsyncSession`` and races a backup request when the
    primary exceeds its estimated latency percentile.

    Args:
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
        **session_kwargs: Additional keyword arguments passed to
            ``niquests.AsyncSession()``.
    """

    def __init__(
        self,
        config: HedgeConfig | None = None,
        **session_kwargs: Any,
    ) -> None:
        self._config = config or HedgeConfig()
        self._session_kwargs = session_kwargs
        self._session: niquests.AsyncSession | None = None
        self._scheduler = HedgeScheduler(self._config)

    def _get_session(self) -> niquests.AsyncSession:
        if self._session is None:
            self._session = niquests.AsyncSession(**self._session_kwargs)
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
    ) -> niquests.Response:
        """Perform a hedged request."""
        host = extract_host(url)
        sketch = self._scheduler.sketch_for(host)

        can_hedge = method.upper() in ("GET", "HEAD", "OPTIONS")
        session = self._get_session()

        async def do_request() -> niquests.Response:
            return await session.request(method, url, **kwargs)  # type: ignore[no-any-return]

        def record_latency(response: niquests.Response, elapsed: float) -> None:
            sketch.add(elapsed)

        return await self._scheduler.execute_with_hedge(
            host=host,
            primary_func=do_request,
            hedge_func=do_request,
            record_latency=record_latency,
            can_hedge=can_hedge,
        )

    async def get(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a hedged GET request."""
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a POST request (no hedging; body cannot be safely replayed)."""
        return await self._request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a PUT request (no hedging)."""
        return await self._request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a DELETE request (no hedging)."""
        return await self._request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a hedged HEAD request."""
        return await self._request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> niquests.Response:
        """Perform a hedged OPTIONS request."""
        return await self._request("OPTIONS", url, **kwargs)

    async def close(self) -> None:
        """Close the session and stop background tasks."""
        await self._scheduler.close()
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> HedgedNiquestsSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
