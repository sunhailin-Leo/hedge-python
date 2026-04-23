"""gRPC server-streaming + hedge-python: TTFM-based hedge demo.

For server streaming, the hedge signal is **time-to-first-message (TTFM)**: if
the primary stream doesn't yield its first chunk within the estimated p90, a
backup stream is started. Whichever yields first wins; the loser is
cancelled at the wire level.

Run::

    uv pip install hedge-python[grpc]
    uv run python examples/grpc_stream.py
"""

from __future__ import annotations

import asyncio
import random

import grpc
import grpc.aio

from hedge import HedgeConfig, Stats
from hedge.interceptor import HedgedServerStreamInterceptor
from tests.integration.proto import testservice_pb2, testservice_pb2_grpc

WARMUP = 20
TRIGGER = 15
FAST_FIRST_MS = 10
SLOW_FIRST_MS = 100
INTER_CHUNK_MS = 5
CHUNKS_PER_RESPONSE = 3
STRAGGLER_PROB = 0.30


class _DemoStreamServicer(testservice_pb2_grpc.TestServiceServicer):
    """Stream servicer with random first-chunk stragglers."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.call_count = 0

    async def Echo(  # pragma: no cover - unused in this example
        self,
        request: testservice_pb2.EchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> testservice_pb2.EchoResponse:
        return testservice_pb2.EchoResponse(message=request.message, request_number=0)

    async def StreamEcho(  # noqa: N802 — gRPC method name
        self,
        request: testservice_pb2.StreamEchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> None:
        self.call_count += 1
        first_delay_ms = request.first_chunk_delay_ms
        if first_delay_ms == 0:
            first_delay_ms = (
                SLOW_FIRST_MS if self._rng.random() < STRAGGLER_PROB else FAST_FIRST_MS
            )
        inter_delay = (request.inter_chunk_delay_ms or INTER_CHUNK_MS) / 1000.0
        count = max(request.count, 1)

        for i in range(count):
            if context.cancelled():
                return
            delay = first_delay_ms / 1000.0 if i == 0 else inter_delay
            if delay > 0:
                await asyncio.sleep(delay)
            await context.write(
                testservice_pb2.StreamEchoResponse(message=request.message, chunk_index=i)
            )


async def _start_server(rng: random.Random) -> tuple[grpc.aio.Server, _DemoStreamServicer, int]:
    servicer = _DemoStreamServicer(rng)
    server = grpc.aio.server()
    testservice_pb2_grpc.add_TestServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, servicer, port


async def _consume(stream) -> int:  # type: ignore[no-untyped-def]
    total = 0
    async for _ in stream:
        total += 1
    return total


async def main() -> None:
    rng = random.Random(42)
    server, servicer, port = await _start_server(rng)

    stats = Stats()
    config = HedgeConfig(
        percentile=0.90,
        budget_percent=50.0,
        estimated_rps=200.0,
        warmup_requests=WARMUP,
        warmup_delay=0.02,
        stats=stats,
    )
    interceptor = HedgedServerStreamInterceptor(config=config)

    try:
        async with grpc.aio.insecure_channel(
            f"127.0.0.1:{port}", interceptors=[interceptor]
        ) as channel:
            stub = testservice_pb2_grpc.TestServiceStub(channel)

            for _ in range(WARMUP):
                stream = stub.StreamEcho(
                    testservice_pb2.StreamEchoRequest(
                        message="warmup",
                        count=CHUNKS_PER_RESPONSE,
                        first_chunk_delay_ms=FAST_FIRST_MS,
                        inter_chunk_delay_ms=INTER_CHUNK_MS,
                    )
                )
                await _consume(stream)

            print(
                f"firing {TRIGGER} streaming requests with "
                f"~{STRAGGLER_PROB:.0%} first-chunk stragglers ..."
            )
            for _ in range(TRIGGER):
                stream = stub.StreamEcho(
                    testservice_pb2.StreamEchoRequest(
                        message="real",
                        count=CHUNKS_PER_RESPONSE,
                        first_chunk_delay_ms=0,
                        inter_chunk_delay_ms=0,
                    )
                )
                await _consume(stream)
    finally:
        await server.stop(grace=0)

    snap = stats.snapshot()
    print(
        f"  total={snap.total_requests}  warmup={snap.warmup_requests}  "
        f"hedged={snap.hedged_requests}  hedge_wins={snap.hedge_wins}  "
        f"primary_wins={snap.primary_wins}  budget_exhausted={snap.budget_exhausted}"
    )
    print(f"  hedge_rate={stats.hedge_rate():.2%}")
    print(f"  server saw {servicer.call_count} streams (extra are hedge duplicates)")


if __name__ == "__main__":
    asyncio.run(main())
