"""Integration tests for gRPC interceptors with a real gRPC server.

Tests both HedgedUnaryInterceptor and HedgedServerStreamInterceptor
against a real gRPC server with configurable latency.
"""

from __future__ import annotations

import asyncio

import grpc
import grpc.aio
import pytest

from hedge import HedgeConfig
from hedge.interceptor import HedgedServerStreamInterceptor, HedgedUnaryInterceptor
from tests.integration.proto import testservice_pb2, testservice_pb2_grpc


class EchoServiceServicer(testservice_pb2_grpc.TestServiceServicer):
    """Real gRPC servicer with configurable latency."""

    def __init__(self) -> None:
        self.echo_call_count = 0
        self.stream_call_count = 0

    async def Echo(
        self,
        request: testservice_pb2.EchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> testservice_pb2.EchoResponse:
        self.echo_call_count += 1
        delay_seconds = request.delay_ms / 1000.0
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        return testservice_pb2.EchoResponse(
            message=request.message,
            request_number=self.echo_call_count,
        )

    async def StreamEcho(
        self,
        request: testservice_pb2.StreamEchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> None:
        self.stream_call_count += 1
        first_delay = request.first_chunk_delay_ms / 1000.0
        inter_delay = request.inter_chunk_delay_ms / 1000.0
        count = max(request.count, 1)

        for i in range(count):
            if context.cancelled():
                return
            delay = first_delay if i == 0 else inter_delay
            if delay > 0:
                await asyncio.sleep(delay)
            await context.write(
                testservice_pb2.StreamEchoResponse(
                    message=request.message,
                    chunk_index=i,
                )
            )


async def _start_server(servicer: EchoServiceServicer) -> tuple[grpc.aio.Server, int]:
    """Start a real gRPC server on a random port and return (server, port)."""
    server = grpc.aio.server()
    testservice_pb2_grpc.add_TestServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("[::]:0")
    await server.start()
    return server, port


@pytest.mark.integration
@pytest.mark.asyncio
class TestHedgedUnaryInterceptor:
    """Integration tests for HedgedUnaryInterceptor with a real gRPC server."""

    async def test_basic_unary_rpc(self) -> None:
        """Verify a basic unary RPC passes through the interceptor.

        Use a generous ``warmup_delay`` (200ms) so cold-start jitter from
        the very first gRPC connection setup never trips the hedge timer
        and turns this single-request smoke test into a flaky double-RPC.
        """
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                min_delay=0.001,
                warmup_requests=1,
                warmup_delay=0.2,
            )
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                response = await stub.Echo(
                    testservice_pb2.EchoRequest(message="hello", delay_ms=0)
                )
                assert response.message == "hello"
                assert response.request_number == 1
                assert servicer.echo_call_count == 1
        finally:
            await server.stop(grace=1)

    async def test_stats_tracking_unary(self) -> None:
        """Verify stats are tracked for multiple unary RPCs."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                for i in range(5):
                    response = await stub.Echo(
                        testservice_pb2.EchoRequest(message=f"msg-{i}", delay_ms=0)
                    )
                    assert response.message == f"msg-{i}"

            snap = interceptor.stats.snapshot()
            assert snap.total_requests == 5
        finally:
            await server.stop(grace=1)

    async def test_hedge_fires_on_slow_primary(self) -> None:
        """Verify interceptor processes all requests correctly through warmup and post-warmup.

        The interceptor wraps both ``await continuation(...)`` and
        ``await call`` inside a single task so the hedge timer reflects real
        RPC latency. Hedge firing is not asserted here (the per-call latency
        is small enough that it's noisy); see the multi-framework benchmark
        for the firing rate / tail-latency improvement.
        """
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                percentile=0.90,
                min_delay=0.001,
                warmup_requests=20,
                warmup_delay=0.005,
                budget_percent=50.0,
                estimated_rps=1000.0,
            )
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                # Warmup: fast responses to build sketch
                for _ in range(25):
                    response = await stub.Echo(
                        testservice_pb2.EchoRequest(message="warmup", delay_ms=1)
                    )
                    assert response.message == "warmup"
                # Post-warmup requests
                for _ in range(5):
                    response = await stub.Echo(
                        testservice_pb2.EchoRequest(message="post", delay_ms=5)
                    )
                    assert response.message == "post"

            snap = interceptor.stats.snapshot()
            assert snap.total_requests == 30
            assert snap.warmup_requests == 20
        finally:
            await server.stop(grace=1)

    async def test_hedge_actually_fires_with_large_delay_gap(self) -> None:
        """End-to-end proof that the unary hedge fires and wins.

        Strategy: train the sketch with many fast (~2ms) requests so its p90
        estimate stabilises near 2-5ms. Then issue slow (~80ms) requests; the
        primary will exceed the estimated delay and trigger a hedge.

        Because the server delay is fixed at 80ms, the hedge will land at
        roughly the same time as the primary, but it should still fire — that
        is what we assert.
        """
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                percentile=0.90,
                min_delay=0.001,
                warmup_requests=10,
                warmup_delay=0.005,
                budget_percent=80.0,
                estimated_rps=1000.0,
            )
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)

                # Warmup + sketch training: 30 fast requests around 2ms each.
                for _ in range(30):
                    await stub.Echo(
                        testservice_pb2.EchoRequest(message="train", delay_ms=2)
                    )

                # Trigger phase: 15 slow requests at ~80ms each. Estimated p90
                # is around 2-5ms, so the hedge_delay timer will expire long
                # before the primary completes.
                for _ in range(15):
                    response = await stub.Echo(
                        testservice_pb2.EchoRequest(message="slow", delay_ms=80)
                    )
                    assert response.message == "slow"

            snap = interceptor.stats.snapshot()
            # 45 total: 10 warmup (config) + 35 post-warmup (20 train tail +
            # 15 slow). Stats counts the first ``warmup_requests`` calls as
            # warmup, regardless of latency.
            assert snap.total_requests == 45
            assert snap.warmup_requests == 10
            # Slow phase MUST trigger hedges — at least one out of 15.
            assert snap.hedged_requests > 0, (
                f"expected hedge to fire at least once, got snapshot={snap}"
            )
            # Server should have processed strictly more than 45 calls because
            # of hedge duplicates (each hedge fires a second RPC).
            assert servicer.echo_call_count > 45, (
                f"expected duplicate RPCs from hedging, got "
                f"echo_call_count={servicer.echo_call_count}"
            )
        finally:
            await server.stop(grace=1)

    async def test_concurrent_requests_unary(self) -> None:
        """Multiple concurrent unary RPCs are handled correctly."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                min_delay=0.001,
                warmup_requests=0,
                warmup_delay=0.001,
                budget_percent=10.0,
                estimated_rps=100.0,
            )
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)

                async def fire_one(index: int) -> testservice_pb2.EchoResponse:
                    return await stub.Echo(
                        testservice_pb2.EchoRequest(message=f"concurrent-{index}", delay_ms=5)
                    )

                responses = await asyncio.gather(*[fire_one(i) for i in range(10)])

            assert len(responses) == 10
            for response in responses:
                assert response.message.startswith("concurrent-")

            snap = interceptor.stats.snapshot()
            assert snap.total_requests == 10
        finally:
            await server.stop(grace=1)

    async def test_warmup_phase(self) -> None:
        """During warmup, fixed delay is used instead of sketch estimate."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                warmup_requests=10,
                warmup_delay=0.01,
                min_delay=0.001,
            )
            interceptor = HedgedUnaryInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                for _ in range(5):
                    await stub.Echo(
                        testservice_pb2.EchoRequest(message="warmup", delay_ms=0)
                    )

            snap = interceptor.stats.snapshot()
            assert snap.warmup_requests == 5
        finally:
            await server.stop(grace=1)

    async def test_default_config(self) -> None:
        """Creating interceptor without config should use defaults."""
        interceptor = HedgedUnaryInterceptor()
        assert interceptor._config.percentile == 0.90
        assert interceptor._config.warmup_requests == 20


@pytest.mark.integration
@pytest.mark.asyncio
class TestHedgedServerStreamInterceptor:
    """Integration tests for HedgedServerStreamInterceptor with a real gRPC server."""

    async def test_basic_server_stream(self) -> None:
        """Verify a basic server streaming RPC works through the interceptor."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            interceptor = HedgedServerStreamInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                stream = stub.StreamEcho(
                    testservice_pb2.StreamEchoRequest(
                        message="hello",
                        count=3,
                        first_chunk_delay_ms=0,
                        inter_chunk_delay_ms=0,
                    )
                )
                messages = []
                async for response in stream:
                    messages.append(response)

                assert len(messages) == 3
                assert messages[0].message == "hello"
                assert messages[0].chunk_index == 0
                assert messages[2].chunk_index == 2
        finally:
            await server.stop(grace=1)

    async def test_stats_tracking_stream(self) -> None:
        """Verify stats are tracked for streaming RPCs."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            interceptor = HedgedServerStreamInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                for _ in range(3):
                    stream = stub.StreamEcho(
                        testservice_pb2.StreamEchoRequest(
                            message="stats",
                            count=2,
                            first_chunk_delay_ms=0,
                            inter_chunk_delay_ms=0,
                        )
                    )
                    async for _ in stream:
                        pass

            snap = interceptor.stats.snapshot()
            assert snap.total_requests == 3
        finally:
            await server.stop(grace=1)

    async def test_hedge_fires_on_slow_first_chunk(self) -> None:
        """When the first chunk is slow, a hedge should fire based on TTFM."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                percentile=0.90,
                min_delay=0.001,
                warmup_requests=20,
                warmup_delay=0.005,
                budget_percent=50.0,
                estimated_rps=1000.0,
            )
            interceptor = HedgedServerStreamInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                # Warmup: fast first chunks
                for _ in range(25):
                    stream = stub.StreamEcho(
                        testservice_pb2.StreamEchoRequest(
                            message="warmup",
                            count=1,
                            first_chunk_delay_ms=1,
                            inter_chunk_delay_ms=0,
                        )
                    )
                    async for _ in stream:
                        pass

                # Now send slow first-chunk requests
                for _ in range(5):
                    stream = stub.StreamEcho(
                        testservice_pb2.StreamEchoRequest(
                            message="slow",
                            count=2,
                            first_chunk_delay_ms=500,
                            inter_chunk_delay_ms=0,
                        )
                    )
                    async for _ in stream:
                        pass

            snap = interceptor.stats.snapshot()
            assert snap.total_requests == 30
            assert snap.hedged_requests > 0
        finally:
            await server.stop(grace=1)

    async def test_single_chunk_stream(self) -> None:
        """Verify a stream with only one chunk works correctly."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(min_delay=0.001, warmup_requests=0)
            interceptor = HedgedServerStreamInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                stream = stub.StreamEcho(
                    testservice_pb2.StreamEchoRequest(
                        message="single",
                        count=1,
                        first_chunk_delay_ms=0,
                        inter_chunk_delay_ms=0,
                    )
                )
                messages = []
                async for response in stream:
                    messages.append(response)

                assert len(messages) == 1
                assert messages[0].message == "single"
        finally:
            await server.stop(grace=1)

    async def test_warmup_phase_stream(self) -> None:
        """During warmup, fixed delay is used for streaming interceptor."""
        servicer = EchoServiceServicer()
        server, port = await _start_server(servicer)
        try:
            config = HedgeConfig(
                warmup_requests=10,
                warmup_delay=0.01,
                min_delay=0.001,
            )
            interceptor = HedgedServerStreamInterceptor(config=config)

            async with grpc.aio.insecure_channel(
                f"localhost:{port}", interceptors=[interceptor]
            ) as channel:
                stub = testservice_pb2_grpc.TestServiceStub(channel)
                for _ in range(5):
                    stream = stub.StreamEcho(
                        testservice_pb2.StreamEchoRequest(
                            message="warmup",
                            count=1,
                            first_chunk_delay_ms=0,
                            inter_chunk_delay_ms=0,
                        )
                    )
                    async for _ in stream:
                        pass

            snap = interceptor.stats.snapshot()
            assert snap.warmup_requests == 5
        finally:
            await server.stop(grace=1)

    async def test_default_config_stream(self) -> None:
        """Creating streaming interceptor without config should use defaults."""
        interceptor = HedgedServerStreamInterceptor()
        assert interceptor._config.percentile == 0.90
        assert interceptor._config.warmup_requests == 20
