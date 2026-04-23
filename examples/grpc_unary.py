"""gRPC unary + hedge-python: self-contained client + local server demo.

Spins up a local gRPC server with deterministic latency (10ms baseline,
random 80ms straggler), trains the sketch with a warmup phase, then issues a
batch of slow requests where the hedge is guaranteed to fire.

The example reuses the ``testservice.proto`` from the test suite, so make sure
you've installed the ``grpc`` extra and have run the project at least once::

    uv pip install hedge-python[grpc]
    uv run python examples/grpc_unary.py
"""

from __future__ import annotations

import asyncio
import random

import grpc
import grpc.aio

from hedge import HedgeConfig, Stats
from hedge.interceptor import HedgedUnaryInterceptor
from tests.integration.proto import testservice_pb2, testservice_pb2_grpc

WARMUP = 30
SLOW_BATCH = 20
BASELINE_MS = 10
STRAGGLER_MS = 80
STRAGGLER_PROB = 0.30


class _DemoEchoServicer(testservice_pb2_grpc.TestServiceServicer):
    """Echo servicer that injects random stragglers to make hedging visible."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.call_count = 0

    async def Echo(  # noqa: N802 — gRPC method name
        self,
        request: testservice_pb2.EchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> testservice_pb2.EchoResponse:
        self.call_count += 1
        delay_ms = request.delay_ms
        if delay_ms == 0:
            delay_ms = STRAGGLER_MS if self._rng.random() < STRAGGLER_PROB else BASELINE_MS
        await asyncio.sleep(delay_ms / 1000.0)
        return testservice_pb2.EchoResponse(
            message=request.message,
            request_number=self.call_count,
        )

    async def StreamEcho(  # pragma: no cover - unused in this example
        self,
        request: testservice_pb2.StreamEchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> None:
        return


async def _start_server(rng: random.Random) -> tuple[grpc.aio.Server, _DemoEchoServicer, int]:
    servicer = _DemoEchoServicer(rng)
    server = grpc.aio.server()
    testservice_pb2_grpc.add_TestServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, servicer, port


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
    interceptor = HedgedUnaryInterceptor(config=config)

    try:
        async with grpc.aio.insecure_channel(
            f"127.0.0.1:{port}", interceptors=[interceptor]
        ) as channel:
            stub = testservice_pb2_grpc.TestServiceStub(channel)

            # Warmup: train the sketch with predictable fast calls.
            for _ in range(WARMUP):
                await stub.Echo(testservice_pb2.EchoRequest(message="warmup", delay_ms=BASELINE_MS))

            # Trigger phase: real traffic with stragglers.
            print(f"firing {SLOW_BATCH} requests with ~{STRAGGLER_PROB:.0%} straggler rate ...")
            for _ in range(SLOW_BATCH):
                await stub.Echo(testservice_pb2.EchoRequest(message="real", delay_ms=0))
    finally:
        await server.stop(grace=0)

    snap = stats.snapshot()
    print(
        f"  total={snap.total_requests}  warmup={snap.warmup_requests}  "
        f"hedged={snap.hedged_requests}  hedge_wins={snap.hedge_wins}  "
        f"primary_wins={snap.primary_wins}  budget_exhausted={snap.budget_exhausted}"
    )
    print(f"  hedge_rate={stats.hedge_rate():.2%}")
    print(f"  server saw {servicer.call_count} RPCs (extra are hedge duplicates)")


if __name__ == "__main__":
    asyncio.run(main())
