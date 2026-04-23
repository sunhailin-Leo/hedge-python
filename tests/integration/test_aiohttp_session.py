"""Integration tests for HedgedAiohttpSession."""

import asyncio

import aiohttp
import aiohttp.web
import pytest

from hedge import HedgeConfig
from hedge.transport._aiohttp import HedgedAiohttpSession


async def _start_test_server(
    normal_delay: float = 0.001,
    straggler_delay: float = 0.5,
    straggler_every: int = 0,
) -> tuple[aiohttp.web.AppRunner, str]:
    """Start a local aiohttp test server with configurable delays."""
    call_count = 0

    async def handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        nonlocal call_count
        call_count += 1
        is_straggler = straggler_every > 0 and (call_count % straggler_every == 0)
        delay = straggler_delay if is_straggler else normal_delay
        await asyncio.sleep(delay)
        return aiohttp.web.Response(text=f"response-{call_count}")

    app = aiohttp.web.Application()
    app.router.add_get("/test", handler)
    app.router.add_post("/test", handler)
    app.router.add_put("/test", handler)
    app.router.add_delete("/test", handler)
    app.router.add_route("OPTIONS", "/test", handler)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.integration
@pytest.mark.asyncio
class TestHedgedAiohttpSession:
    async def test_basic_get(self) -> None:
        """Verify that a basic GET request works."""
        runner, base_url = await _start_test_server()
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            async with HedgedAiohttpSession(config=config) as session:
                resp = await session.get(f"{base_url}/test")
                assert resp.status == 200
                text = await resp.text()
                assert "response" in text
        finally:
            await runner.cleanup()

    async def test_stats_tracking(self) -> None:
        """Verify stats are tracked for multiple requests."""
        runner, base_url = await _start_test_server()
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            async with HedgedAiohttpSession(config=config) as session:
                for _ in range(5):
                    resp = await session.get(f"{base_url}/test")
                    await resp.text()  # consume body
                snap = session.stats.snapshot()
                assert snap.total_requests == 5
        finally:
            await runner.cleanup()

    async def test_post_not_hedged(self) -> None:
        """POST requests should not be hedged."""
        runner, base_url = await _start_test_server(normal_delay=0.05)
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0, warmup_delay=0.001)
            async with HedgedAiohttpSession(config=config) as session:
                resp = await session.post(f"{base_url}/test")
                assert resp.status == 200
                snap = session.stats.snapshot()
                assert snap.hedged_requests == 0
        finally:
            await runner.cleanup()

    async def test_context_manager(self) -> None:
        """Verify async context manager works correctly."""
        runner, base_url = await _start_test_server()
        try:
            async with HedgedAiohttpSession() as session:
                resp = await session.get(f"{base_url}/test")
                assert resp.status == 200
        finally:
            await runner.cleanup()

    async def test_head_request(self) -> None:
        """HEAD requests should be hedgeable."""
        runner, base_url = await _start_test_server()
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            async with HedgedAiohttpSession(config=config) as session:
                resp = await session.head(f"{base_url}/test")
                assert resp.status == 200
        finally:
            await runner.cleanup()

    async def test_default_config(self) -> None:
        """Creating session without config should use defaults."""
        async with HedgedAiohttpSession() as session:
            assert session._config.percentile == 0.90

    async def test_put_delete_options_methods(self) -> None:
        """PUT / DELETE / OPTIONS pass through the session correctly.

        PUT and DELETE are non-idempotent here so they must not be hedged;
        OPTIONS is hedgeable but with a fast handler should not actually fire
        a hedge. The point of this test is purely to cover the three thin
        method wrappers in :class:`HedgedAiohttpSession`.
        """
        runner, base_url = await _start_test_server()
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0, warmup_delay=0.05)
            async with HedgedAiohttpSession(config=config) as session:
                resp = await session.put(f"{base_url}/test")
                assert resp.status == 200

                resp = await session.delete(f"{base_url}/test")
                assert resp.status == 200

                resp = await session.options(f"{base_url}/test")
                assert resp.status == 200

                snap = session.stats.snapshot()
                # 3 requests went through; PUT/DELETE never hedge.
                assert snap.total_requests == 3
                assert snap.hedged_requests == 0
        finally:
            await runner.cleanup()
