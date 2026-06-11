"""Hedged HTTP client wrapper for tornado.httpclient.AsyncHTTPClient.

Usage::

    from hedge import HedgeConfig
    from hedge.transport import HedgedTornadoClient

    client = HedgedTornadoClient(config=HedgeConfig())
    response = await client.fetch("https://api.example.com/data")
    print(response.code, response.body)
    await client.close()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType

try:
    from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPResponse
except ImportError as exc:
    raise ImportError(
        "tornado is required for HedgedTornadoClient. Install it with: pip install hedge-python[tornado]"
    ) from exc

from hedge._options import HedgeConfig
from hedge.transport._base import HedgeScheduler, extract_host

if TYPE_CHECKING:
    from hedge._stats import Stats


class HedgedTornadoClient:
    """A tornado AsyncHTTPClient wrapper that adds adaptive hedged requests.

    Wraps a ``tornado.httpclient.AsyncHTTPClient`` and races a backup request
    when the primary exceeds its estimated latency percentile.

    Args:
        config: Hedge configuration. Defaults to ``HedgeConfig()``.
        client: An existing ``AsyncHTTPClient`` instance. If *None*, a new
            instance is created with ``force_instance=True`` so that
            ``close()`` can safely shut it down without affecting the
            global singleton.
        raise_error: Whether to raise ``tornado.httpclient.HTTPError``
            for non-200 responses. Defaults to ``True``.
    """

    def __init__(
        self,
        config: HedgeConfig | None = None,
        client: AsyncHTTPClient | None = None,
        *,
        raise_error: bool = True,
    ) -> None:
        self._owns_client = client is None
        self._client = client or AsyncHTTPClient(force_instance=True)
        self._config = config or HedgeConfig()
        self._scheduler = HedgeScheduler(self._config)
        self._raise_error = raise_error

    @property
    def stats(self) -> Stats:
        """Access the live Stats object."""
        return self._scheduler.stats

    async def fetch(
        self,
        request: HTTPRequest | str,
        **kwargs: Any,
    ) -> HTTPResponse:
        """Perform a hedged HTTP request.

        Args:
            request: URL string or ``HTTPRequest`` object.
            **kwargs: Extra keyword arguments forwarded to
                ``HTTPRequest()`` when *request* is a plain string.

        Returns:
            The ``HTTPResponse`` from whichever request finishes first.
        """
        if isinstance(request, str):
            fetch_kwargs = dict(kwargs)
            per_request_raise_error = fetch_kwargs.pop("raise_error", self._raise_error)
            request_obj = HTTPRequest(request, **fetch_kwargs)
        else:
            request_obj = request
            per_request_raise_error = self._raise_error

        host = extract_host(request_obj.url)
        sketch = self._scheduler.sketch_for(host)

        method = (request_obj.method or "GET").upper()
        can_hedge = method in ("GET", "HEAD", "OPTIONS")

        async def do_request() -> HTTPResponse:
            return await self._client.fetch(
                request_obj,
                raise_error=per_request_raise_error,
            )

        def record_latency(response: HTTPResponse, elapsed: float) -> None:
            sketch.add(elapsed)

        return await self._scheduler.execute_with_hedge(
            host=host,
            primary_func=do_request,
            hedge_func=do_request,
            record_latency=record_latency,
            can_hedge=can_hedge,
        )

    async def close(self) -> None:
        """Close the client and stop background tasks."""
        await self._scheduler.close()
        if self._owns_client:
            self._client.close()

    async def __aenter__(self) -> HedgedTornadoClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
