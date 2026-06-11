"""Multi-framework benchmark: hedge effectiveness across httpx, aiohttp, gRPC.

Spins up real local servers for each framework with identical lognormal latency
and straggler distributions, then measures tail-latency improvement under hedge.

All frameworks use real network connections (TCP over loopback) to ensure
fair, apples-to-apples comparison of hedge effectiveness.

For each framework we run:
  1. No hedging  — baseline
  2. Adaptive (hedge) — DDSketch-based dynamic delay

Outputs:
  - Markdown table to stdout (pytest -s)
  - CSV file to benchmark/results_multi.csv (consumed by benchmark/plot.py)

Run with: ``make bench-multi``
"""

from __future__ import annotations

import asyncio
import csv
import math
import os
import random
import time
from dataclasses import dataclass

import aiohttp
import grpc
import grpc.aio
import httpx
import pytest

from hedge import HedgeConfig, Stats
from hedge.interceptor import HedgedUnaryInterceptor
from hedge.transport import HedgedAiohttpSession, HedgedHttpxTransport
from tests.integration.proto import testservice_pb2, testservice_pb2_grpc

# ---------------------------------------------------------------------------
# Simulation parameters
# ---------------------------------------------------------------------------
REQUEST_COUNT = 500
BASE_MEAN_MS = 5.0
BASE_STDDEV_MS = 2.0
STRAGGLER_PROB = 0.05
STRAGGLER_MULTIPLIER = 10.0

# Lognormal parameters derived from mean / stddev
_CV2 = (BASE_STDDEV_MS / BASE_MEAN_MS) ** 2
_SIGMA_LN = math.sqrt(math.log(1 + _CV2))
_MU_LN = math.log(BASE_MEAN_MS) - _SIGMA_LN**2 / 2

PERCENTILE_LABELS = ["p50", "p90", "p95", "p99", "p999"]
PERCENTILE_VALUES = [0.50, 0.90, 0.95, 0.99, 0.999]

FRAMEWORKS = ["httpx", "aiohttp", "grpc"]
CONFIGS = ["No hedging", "Adaptive (hedge)"]

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "benchmark")
RESULTS_CSV = os.path.join(RESULTS_DIR, "results_multi.csv")


# ---------------------------------------------------------------------------
# Latency distribution (shared across frameworks)
# ---------------------------------------------------------------------------
def _sample_latency_ms(rng: random.Random) -> float:
    """Sample a single backend latency in milliseconds.

    Uses lognormal base latency with ``STRAGGLER_PROB`` chance of
    a ``STRAGGLER_MULTIPLIER``x spike to model tail behaviour.
    """
    latency_ms = math.exp(_MU_LN + _SIGMA_LN * rng.gauss(0, 1))
    if rng.random() < STRAGGLER_PROB:
        latency_ms *= STRAGGLER_MULTIPLIER
    return latency_ms


def _percentile(sorted_data: list[float], quantile: float) -> float:
    """Return the value at the given quantile from pre-sorted data."""
    index = int(len(sorted_data) * quantile)
    return sorted_data[min(index, len(sorted_data) - 1)]


@dataclass
class BenchmarkResult:
    """Collected benchmark result for a (framework, configuration) pair."""

    framework: str
    name: str
    latencies: list[float]
    backend_calls: int
    stats: Stats | None = None

    @property
    def overhead_percent(self) -> float:
        extra = self.backend_calls - len(self.latencies)
        return extra / len(self.latencies) * 100.0 if self.latencies else 0.0

    def percentiles(self) -> dict[str, float]:
        sorted_lat = sorted(self.latencies)
        return {
            label: _percentile(sorted_lat, quantile) for label, quantile in zip(PERCENTILE_LABELS, PERCENTILE_VALUES)
        }


# ---------------------------------------------------------------------------
# httpx: real local server with simulated latency
# ---------------------------------------------------------------------------
class _HttpxServer:
    """Local HTTP server for httpx benchmarks with lognormal+straggler latency."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.call_count = 0
        self._runner: aiohttp.web.AppRunner | None = None
        self._site: aiohttp.web.TCPSite | None = None
        self.port: int = 0

    async def _handle(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        self.call_count += 1
        latency_ms = _sample_latency_ms(self._rng)
        await asyncio.sleep(latency_ms / 1000.0)
        return aiohttp.web.Response(text="ok")

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/api", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        server = self._site._server  # type: ignore[attr-defined]
        sockets = server.sockets if server else None
        assert sockets, "httpx backend server failed to bind socket"
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


# ---------------------------------------------------------------------------
# aiohttp: real local server with simulated latency
# ---------------------------------------------------------------------------
class _AiohttpServer:
    """Local aiohttp server emulating the latency distribution."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.call_count = 0
        self._runner: aiohttp.web.AppRunner | None = None
        self._site: aiohttp.web.TCPSite | None = None
        self.port: int = 0

    async def _handle(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        self.call_count += 1
        latency_ms = _sample_latency_ms(self._rng)
        await asyncio.sleep(latency_ms / 1000.0)
        return aiohttp.web.Response(text="ok")

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/api", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        # Read the actual bound port
        server = self._site._server  # type: ignore[attr-defined]
        sockets = server.sockets if server else None
        assert sockets, "aiohttp server failed to bind socket"
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


# ---------------------------------------------------------------------------
# gRPC: real local server with simulated latency
# ---------------------------------------------------------------------------
class _GrpcSimServicer(testservice_pb2_grpc.TestServiceServicer):
    """gRPC servicer that samples its own latency on each request."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.call_count = 0

    async def Echo(
        self,
        request: testservice_pb2.EchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> testservice_pb2.EchoResponse:
        self.call_count += 1
        latency_ms = _sample_latency_ms(self._rng)
        await asyncio.sleep(latency_ms / 1000.0)
        return testservice_pb2.EchoResponse(
            message=request.message,
            request_number=self.call_count,
        )

    async def StreamEcho(  # pragma: no cover - unused in benchmark
        self,
        request: testservice_pb2.StreamEchoRequest,
        context: grpc.aio.ServicerContext,
    ) -> None:
        return


async def _start_grpc_server(rng: random.Random) -> tuple[grpc.aio.Server, _GrpcSimServicer, int]:
    servicer = _GrpcSimServicer(rng)
    server = grpc.aio.server()
    testservice_pb2_grpc.add_TestServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, servicer, port


# ---------------------------------------------------------------------------
# Hedge config builder
# ---------------------------------------------------------------------------
def _adaptive_config() -> HedgeConfig:
    return HedgeConfig(
        percentile=0.90,
        budget_percent=10.0,
        estimated_rps=100.0,
        min_delay=0.001,
        warmup_requests=20,
        warmup_delay=0.01,
    )


# ---------------------------------------------------------------------------
# Per-framework runners
# ---------------------------------------------------------------------------
async def _run_httpx(
    config_name: str,
    seed: int,
) -> BenchmarkResult:
    rng = random.Random(seed)
    server = _HttpxServer(rng)
    await server.start()
    base_url = f"http://127.0.0.1:{server.port}"

    stats: Stats | None
    latencies: list[float] = []
    try:
        if config_name == "No hedging":
            stats = None
            async with httpx.AsyncClient(base_url=base_url) as client:
                for _ in range(REQUEST_COUNT):
                    start = time.monotonic()
                    await client.get("/api")
                    latencies.append((time.monotonic() - start) * 1000.0)
        else:
            hedged = HedgedHttpxTransport(config=_adaptive_config())
            stats = hedged.stats
            async with httpx.AsyncClient(transport=hedged, base_url=base_url) as client:
                for _ in range(REQUEST_COUNT):
                    start = time.monotonic()
                    await client.get("/api")
                    latencies.append((time.monotonic() - start) * 1000.0)
            await hedged.aclose()
    finally:
        await server.stop()

    return BenchmarkResult(
        framework="httpx",
        name=config_name,
        latencies=latencies,
        backend_calls=server.call_count,
        stats=stats,
    )


async def _run_aiohttp(
    config_name: str,
    seed: int,
) -> BenchmarkResult:
    rng = random.Random(seed)
    server = _AiohttpServer(rng)
    await server.start()
    url = f"http://127.0.0.1:{server.port}/api"

    latencies: list[float] = []
    stats: Stats | None
    try:
        if config_name == "No hedging":
            stats = None
            async with aiohttp.ClientSession() as session:
                for _ in range(REQUEST_COUNT):
                    start = time.monotonic()
                    async with session.get(url) as resp:
                        await resp.read()
                    latencies.append((time.monotonic() - start) * 1000.0)
        else:
            hedged = HedgedAiohttpSession(config=_adaptive_config())
            stats = hedged.stats
            async with hedged as session:
                for _ in range(REQUEST_COUNT):
                    start = time.monotonic()
                    resp = await session.get(url)
                    await resp.read()
                    resp.release()
                    latencies.append((time.monotonic() - start) * 1000.0)
    finally:
        await server.stop()

    return BenchmarkResult(
        framework="aiohttp",
        name=config_name,
        latencies=latencies,
        backend_calls=server.call_count,
        stats=stats,
    )


async def _run_grpc(
    config_name: str,
    seed: int,
) -> BenchmarkResult:
    rng = random.Random(seed)
    server, servicer, port = await _start_grpc_server(rng)
    target = f"127.0.0.1:{port}"

    interceptors: list[grpc.aio.UnaryUnaryClientInterceptor] = []
    stats: Stats | None
    if config_name == "No hedging":
        stats = None
    else:
        interceptor = HedgedUnaryInterceptor(config=_adaptive_config())
        interceptors.append(interceptor)
        stats = interceptor.stats

    latencies: list[float] = []
    try:
        async with grpc.aio.insecure_channel(target, interceptors=interceptors) as channel:
            stub = testservice_pb2_grpc.TestServiceStub(channel)
            for _ in range(REQUEST_COUNT):
                start = time.monotonic()
                await stub.Echo(testservice_pb2.EchoRequest(message="ping", delay_ms=0))
                latencies.append((time.monotonic() - start) * 1000.0)
    finally:
        await server.stop(grace=0)

    return BenchmarkResult(
        framework="grpc",
        name=config_name,
        latencies=latencies,
        backend_calls=servicer.call_count,
        stats=stats,
    )


_RUNNERS = {
    "httpx": _run_httpx,
    "aiohttp": _run_aiohttp,
    "grpc": _run_grpc,
}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def _print_markdown_table(results: list[BenchmarkResult]) -> None:
    header = "| Framework | Configuration       |  p50  |  p90  |   p95  |   p99  |  p999   | Overhead |"
    separator = "|-----------|---------------------|-------|-------|--------|--------|---------|----------|"
    print(f"\n{header}")
    print(separator)
    for result in results:
        pcts = result.percentiles()
        print(
            f"| {result.framework:<9s} "
            f"| {result.name:<19s} "
            f"| {pcts['p50']:5.1f} "
            f"| {pcts['p90']:5.1f} "
            f"| {pcts['p95']:6.1f} "
            f"| {pcts['p99']:6.1f} "
            f"| {pcts['p999']:7.1f} "
            f"| {result.overhead_percent:7.1f}% |"
        )
    print()


def _print_stats(result: BenchmarkResult) -> None:
    if result.stats is None:
        return
    snap = result.stats.snapshot()
    print(
        f"  [{result.framework}/{result.name}] "
        f"total={snap.total_requests}  hedged={snap.hedged_requests}  "
        f"hedge_wins={snap.hedge_wins}  primary_wins={snap.primary_wins}  "
        f"budget_exhausted={snap.budget_exhausted}  "
        f"hedge_rate={result.stats.hedge_rate():.2%}"
    )


def _write_csv(results: list[BenchmarkResult]) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RESULTS_CSV, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["framework", "configuration", *PERCENTILE_LABELS, "overhead_pct"])
        for result in results:
            pcts = result.percentiles()
            writer.writerow(
                [
                    result.framework,
                    result.name,
                    *[f"{pcts[label]:.1f}" for label in PERCENTILE_LABELS],
                    f"{result.overhead_percent:.1f}",
                ]
            )
    return RESULTS_CSV


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
@pytest.mark.benchmark
@pytest.mark.asyncio
class TestMultiFrameworkComparison:
    """Cross-framework hedge benchmark.

    For each framework (httpx, aiohttp, grpc) runs:
      1. No hedging baseline
      2. Adaptive hedging
    Then aggregates a Markdown table + CSV consumed by ``benchmark/plot.py``.
    """

    async def test_multi_framework_comparison(self) -> None:
        seed = 42
        results: list[BenchmarkResult] = []

        for framework in FRAMEWORKS:
            runner = _RUNNERS[framework]
            for config_name in CONFIGS:
                # Re-seed before each run so backends share an identical
                # latency stream — comparisons stay apples-to-apples.
                result = await runner(config_name, seed)
                results.append(result)

        # --- Output ---
        print(
            f"\n  Multi-framework benchmark: {REQUEST_COUNT} requests per cell, "
            f"lognormal(mean={BASE_MEAN_MS}ms, stddev={BASE_STDDEV_MS}ms), "
            f"{STRAGGLER_PROB:.0%} stragglers x {STRAGGLER_MULTIPLIER:.0f}"
        )
        _print_markdown_table(results)
        for result in results:
            _print_stats(result)

        csv_path = _write_csv(results)
        print(f"\n  Results written to {csv_path}")

        # --- Sanity assertion: hedge should not regress p99 dramatically ---
        for framework in FRAMEWORKS:
            no_hedge = next(r for r in results if r.framework == framework and r.name == "No hedging")
            adaptive = next(r for r in results if r.framework == framework and r.name == "Adaptive (hedge)")
            no_hedge_p99 = no_hedge.percentiles()["p99"]
            adaptive_p99 = adaptive.percentiles()["p99"]
            print(f"  [{framework}] p99: {no_hedge_p99:.1f}ms -> {adaptive_p99:.1f}ms")
