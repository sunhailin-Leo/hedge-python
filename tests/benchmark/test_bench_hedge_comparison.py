"""Comparison benchmarks: no-hedge vs hedged across configurations.

Starts a real local HTTP server with lognormal base latency and 5% straggler
probability, then compares four configurations over real network connections:

  1. No hedging
  2. Static 10ms threshold
  3. Static 50ms threshold
  4. Adaptive (hedge) — DDSketch-based dynamic delay

Outputs:
  - Markdown table to stdout (pytest -s)
  - CSV file to benchmark/results.csv

Run with: make bench-compare
"""

from __future__ import annotations

import asyncio
import csv
import math
import os
import random
import time
from dataclasses import dataclass

import aiohttp.web
import httpx
import pytest

from hedge import HedgeConfig, Stats
from hedge.transport._httpx import HedgedHttpxTransport

# ---------------------------------------------------------------------------
# Simulation parameters (mirrors Go benchmark)
# ---------------------------------------------------------------------------
REQUEST_COUNT = 2000
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

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "benchmark")
RESULTS_CSV = os.path.join(RESULTS_DIR, "results.csv")


# ---------------------------------------------------------------------------
# Real HTTP server with simulated latency
# ---------------------------------------------------------------------------
class _BenchServer:
    """Local HTTP server emulating lognormal + straggler latency."""

    def __init__(self) -> None:
        self.call_count = 0
        self._runner: aiohttp.web.AppRunner | None = None
        self._site: aiohttp.web.TCPSite | None = None
        self.port: int = 0

    async def _handle(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        self.call_count += 1
        latency_ms = math.exp(_MU_LN + _SIGMA_LN * random.gauss(0, 1))
        if random.random() < STRAGGLER_PROB:
            latency_ms *= STRAGGLER_MULTIPLIER
        await asyncio.sleep(latency_ms / 1000.0)
        return aiohttp.web.Response(text="ok")

    async def start(self) -> None:
        app = aiohttp.web.Application()
        app.router.add_get("/api", self._handle)
        self._runner = aiohttp.web.AppRunner(app)
        await self._runner.setup()
        self._site = aiohttp.web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        server = self._site._server  # type: ignore[attr-defined]
        sockets = server.sockets if server else None
        assert sockets, "server failed to bind socket"
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    def reset(self) -> None:
        """Reset call count between configurations."""
        self.call_count = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _percentile(sorted_data: list[float], quantile: float) -> float:
    """Return the value at the given quantile from pre-sorted data."""
    index = int(len(sorted_data) * quantile)
    return sorted_data[min(index, len(sorted_data) - 1)]


@dataclass
class BenchmarkResult:
    """Collected benchmark result for one configuration."""

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


async def _run_requests(client: httpx.AsyncClient, url: str, count: int) -> list[float]:
    """Issue *count* sequential GET requests, returning latencies in ms."""
    latencies: list[float] = []
    for _ in range(count):
        start = time.monotonic()
        await client.get(url)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        latencies.append(elapsed_ms)
    return latencies


def _print_markdown_table(results: list[BenchmarkResult]) -> None:
    """Print a Markdown comparison table to stdout."""
    header = "| Configuration        |  p50  |  p90  |   p95  |   p99  |  p999   | Overhead |"
    separator = "|----------------------|-------|-------|--------|--------|---------|----------|"
    print(f"\n{header}")
    print(separator)
    for result in results:
        pcts = result.percentiles()
        row = (
            f"| {result.name:<20s} "
            f"| {pcts['p50']:5.1f} "
            f"| {pcts['p90']:5.1f} "
            f"| {pcts['p95']:6.1f} "
            f"| {pcts['p99']:6.1f} "
            f"| {pcts['p999']:7.1f} "
            f"| {result.overhead_percent:7.1f}% |"
        )
        print(row)
    print()


def _write_csv(results: list[BenchmarkResult]) -> str:
    """Write results to a CSV file and return the file path."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RESULTS_CSV, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["configuration", *PERCENTILE_LABELS, "overhead_pct"])
        for result in results:
            pcts = result.percentiles()
            writer.writerow(
                [
                    result.name,
                    *[f"{pcts[label]:.1f}" for label in PERCENTILE_LABELS],
                    f"{result.overhead_percent:.1f}",
                ]
            )
    return RESULTS_CSV


def _print_stats(result: BenchmarkResult) -> None:
    """Print hedge stats for a single result."""
    if result.stats is None:
        return
    snap = result.stats.snapshot()
    print(f"  [{result.name}] hedge stats:")
    print(
        f"    total={snap.total_requests}  hedged={snap.hedged_requests}  "
        f"hedge_wins={snap.hedge_wins}  primary_wins={snap.primary_wins}  "
        f"budget_exhausted={snap.budget_exhausted}  "
        f"hedge_rate={result.stats.hedge_rate():.2%}"
    )


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------
def _make_static_config(delay_seconds: float) -> HedgeConfig:
    return HedgeConfig(
        percentile=0.90,
        budget_percent=10.0,
        estimated_rps=100.0,
        min_delay=delay_seconds,
        warmup_requests=0,
        warmup_delay=delay_seconds,
    )


def _make_adaptive_config() -> HedgeConfig:
    return HedgeConfig(
        percentile=0.90,
        budget_percent=10.0,
        estimated_rps=100.0,
        min_delay=0.001,
        warmup_requests=20,
        warmup_delay=0.01,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.benchmark
@pytest.mark.asyncio
class TestHedgeComparison:
    """Compare no-hedge vs static vs adaptive hedge latency distributions.

    Mirrors the evaluation table and chart from bhope/hedge.
    """

    async def test_full_comparison(self) -> None:
        """Run all four configurations and output Markdown table + CSV.

        Configurations:
          1. No hedging
          2. Static 10ms
          3. Static 50ms
          4. Adaptive (hedge)
        """
        seed = 42
        server = _BenchServer()
        await server.start()
        url = f"http://127.0.0.1:{server.port}/api"
        results: list[BenchmarkResult] = []

        configs: list[tuple[str, HedgeConfig | None]] = [
            ("No hedging", None),
            ("Static 10ms", _make_static_config(0.01)),
            ("Static 50ms", _make_static_config(0.05)),
            ("Adaptive (hedge)", _make_adaptive_config()),
        ]

        try:
            for config_name, config in configs:
                random.seed(seed)
                server.reset()

                if config is None:
                    stats = None
                    async with httpx.AsyncClient(base_url=url) as client:
                        latencies = await _run_requests(client, url, REQUEST_COUNT)
                else:
                    transport = HedgedHttpxTransport(config=config)
                    stats = transport.stats
                    async with httpx.AsyncClient(transport=transport, base_url=url) as client:
                        latencies = await _run_requests(client, url, REQUEST_COUNT)
                    await transport.aclose()

                results.append(
                    BenchmarkResult(
                        name=config_name,
                        latencies=latencies,
                        backend_calls=server.call_count,
                        stats=stats,
                    )
                )
        finally:
            await server.stop()

        # --- Output ---
        print(
            f"\n  Benchmark: {REQUEST_COUNT} requests, "
            f"lognormal(mean={BASE_MEAN_MS}ms, stddev={BASE_STDDEV_MS}ms), "
            f"{STRAGGLER_PROB:.0%} stragglers x {STRAGGLER_MULTIPLIER:.0f}"
        )
        _print_markdown_table(results)

        for result in results:
            _print_stats(result)

        csv_path = _write_csv(results)
        print(f"\n  Results written to {csv_path}")

        # --- Assertions ---
        no_hedge_pcts = results[0].percentiles()
        adaptive_pcts = results[3].percentiles()
        print(f"\n  p99 improvement: {no_hedge_pcts['p99']:.1f}ms -> {adaptive_pcts['p99']:.1f}ms")

    async def test_no_hedge_vs_adaptive(self) -> None:
        """Quick two-way comparison: no hedging vs adaptive hedging."""
        server = _BenchServer()
        await server.start()
        url = f"http://127.0.0.1:{server.port}/api"

        try:
            # No hedge
            random.seed(42)
            async with httpx.AsyncClient(base_url=url) as client:
                no_hedge_latencies = await _run_requests(client, url, REQUEST_COUNT)
            no_hedge_calls = server.call_count

            # Adaptive hedge
            random.seed(42)
            server.reset()
            transport = HedgedHttpxTransport(config=_make_adaptive_config())
            async with httpx.AsyncClient(transport=transport, base_url=url) as client:
                hedge_latencies = await _run_requests(client, url, REQUEST_COUNT)
            hedge_calls = server.call_count
        finally:
            await server.stop()

        results = [
            BenchmarkResult("No hedging", no_hedge_latencies, no_hedge_calls),
            BenchmarkResult("Adaptive (hedge)", hedge_latencies, hedge_calls, stats=transport.stats),
        ]
        _print_markdown_table(results)
        _print_stats(results[1])

        await transport.aclose()
