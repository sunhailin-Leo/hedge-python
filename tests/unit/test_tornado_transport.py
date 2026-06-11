"""Unit tests for HedgedTornadoClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from tornado.httpclient import HTTPRequest, HTTPResponse

from hedge._options import HedgeConfig
from hedge.transport._tornado import HedgedTornadoClient


@pytest.fixture
def config() -> HedgeConfig:
    return HedgeConfig(warmup_requests=0, warmup_delay=0.001)


def _make_tornado_response(
    code: int = 200,
    body: bytes = b"OK",
) -> HTTPResponse:
    """Create a minimal HTTPResponse for testing."""
    request = HTTPRequest("https://example.com/data")
    response = HTTPResponse(request, code)
    response.buffer = None  # type: ignore[assignment]
    response._body = body
    return response


class TestHedgedTornadoClientInit:
    def test_default_config(self) -> None:
        client = HedgedTornadoClient()
        assert client._config.percentile == 0.90
        assert client._owns_client is True
        client._client.close()

    def test_custom_config(self, config: HedgeConfig) -> None:
        client = HedgedTornadoClient(config=config)
        assert client._config.warmup_requests == 0
        client._client.close()

    def test_external_client_not_owned(self) -> None:
        from tornado.httpclient import AsyncHTTPClient

        external = AsyncHTTPClient(force_instance=True)
        client = HedgedTornadoClient(client=external)
        assert client._owns_client is False
        external.close()

    def test_stats_accessible(self) -> None:
        client = HedgedTornadoClient()
        assert client.stats is not None
        client._client.close()


class TestHedgedTornadoClientFetch:
    @pytest.mark.asyncio
    async def test_fetch_with_url_string(self, config: HedgeConfig) -> None:
        mock_response = _make_tornado_response()

        with patch.object(HedgedTornadoClient, "__init__", lambda self, **kw: None):
            client = HedgedTornadoClient.__new__(HedgedTornadoClient)
            client._config = config
            client._raise_error = True
            client._owns_client = False
            client._client = AsyncMock()
            client._client.fetch = AsyncMock(return_value=mock_response)

            from hedge.transport._base import HedgeScheduler

            client._scheduler = HedgeScheduler(config)

            response = await client.fetch("https://example.com/data")
            assert response.code == 200
            await client._scheduler.close()

    @pytest.mark.asyncio
    async def test_fetch_with_http_request(self, config: HedgeConfig) -> None:
        mock_response = _make_tornado_response()

        with patch.object(HedgedTornadoClient, "__init__", lambda self, **kw: None):
            client = HedgedTornadoClient.__new__(HedgedTornadoClient)
            client._config = config
            client._raise_error = True
            client._owns_client = False
            client._client = AsyncMock()
            client._client.fetch = AsyncMock(return_value=mock_response)

            from hedge.transport._base import HedgeScheduler

            client._scheduler = HedgeScheduler(config)

            request = HTTPRequest("https://example.com/api", method="GET")
            response = await client.fetch(request)
            assert response.code == 200
            await client._scheduler.close()

    @pytest.mark.asyncio
    async def test_post_not_hedged(self, config: HedgeConfig) -> None:
        mock_response = _make_tornado_response(code=201)

        with patch.object(HedgedTornadoClient, "__init__", lambda self, **kw: None):
            client = HedgedTornadoClient.__new__(HedgedTornadoClient)
            client._config = config
            client._raise_error = True
            client._owns_client = False
            client._client = AsyncMock()
            client._client.fetch = AsyncMock(return_value=mock_response)

            from hedge.transport._base import HedgeScheduler

            client._scheduler = HedgeScheduler(config)

            request = HTTPRequest("https://example.com/api", method="POST", body=b"{}")
            response = await client.fetch(request)
            assert response.code == 201
            await client._scheduler.close()


class TestHedgedTornadoClientStats:
    @pytest.mark.asyncio
    async def test_total_incremented(self, config: HedgeConfig) -> None:
        mock_response = _make_tornado_response()

        with patch.object(HedgedTornadoClient, "__init__", lambda self, **kw: None):
            client = HedgedTornadoClient.__new__(HedgedTornadoClient)
            client._config = config
            client._raise_error = True
            client._owns_client = False
            client._client = AsyncMock()
            client._client.fetch = AsyncMock(return_value=mock_response)

            from hedge.transport._base import HedgeScheduler

            client._scheduler = HedgeScheduler(config)

            await client.fetch("https://example.com/data")
            snapshot = client.stats.snapshot()
            assert snapshot.total_requests >= 1
            await client._scheduler.close()


class TestHedgedTornadoClientLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        async with HedgedTornadoClient() as client:
            assert isinstance(client, HedgedTornadoClient)

    @pytest.mark.asyncio
    async def test_close_owned_client(self) -> None:
        client = HedgedTornadoClient()
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_external_client_not_closed(self) -> None:
        from tornado.httpclient import AsyncHTTPClient

        external = AsyncHTTPClient(force_instance=True)
        client = HedgedTornadoClient(client=external)
        await client.close()
        # External client should not be closed by HedgedTornadoClient
        external.close()
