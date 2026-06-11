# hedge-python

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

[![CI](https://github.com/sunhailin-Leo/hedge-python/actions/workflows/ci.yml/badge.svg)](https://github.com/sunhailin-Leo/hedge-python/actions)
[![Coverage](https://img.shields.io/badge/coverage-97%25-brightgreen.svg)](#testing)
[![Python](https://img.shields.io/badge/python-3.9%E2%80%933.14-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Python port of [bhope/hedge](https://github.com/bhope/hedge) — **adaptive hedged
requests for tail-latency optimisation**.

`hedge-python` learns per-host latency distributions with
[DDSketch](https://arxiv.org/abs/2004.08604), races a backup request when the
primary exceeds its estimated p90, and caps the hedge rate with a token bucket
to prevent load amplification during outages. Zero configuration required.
First-class support for **httpx**, **aiohttp**, **niquests**, **tornado**, and **gRPC** (unary +
server-streaming). Works out of the box with **OpenAI's Python SDK**.

Inspired by Dean & Barroso, [_The Tail at Scale_](https://research.google/pubs/the-tail-at-scale/) (CACM 2013).

---

## Why hedging?

A small fraction of slow responses dominates user-perceived latency. Hedging
fires a duplicate request after the primary blows past its expected deadline —
whichever finishes first wins, the other is cancelled.

**Result on a benchmark with 5% straggler requests (10× slower):**

![Multi-framework benchmark](eval_multi_framework.png)

| Framework | Configuration       |  p50  |  p90  |   p95  |   p99  |  p999   | Overhead |
|-----------|---------------------|-------|-------|--------|--------|---------|----------|
| httpx     | No hedging          |  5.8  | 10.3  |  12.2  |  51.3  |   78.3  |   0.0%   |
| httpx     | **Adaptive (hedge)**|  6.2  | 10.5  |  12.1  | **18.8** | **22.2** |   7.0%   |
| aiohttp   | No hedging          |  6.3  | 10.7  |  13.0  |  52.4  |   79.0  |   0.0%   |
| aiohttp   | **Adaptive (hedge)**|  6.5  | 11.3  |  13.8  | **20.5** | **25.1** |   4.6%   |
| grpc      | No hedging          |  6.5  | 10.8  |  12.7  |  59.9  |   82.0  |   0.0%   |
| grpc      | **Adaptive (hedge)**|  6.9  | 11.6  |  13.7  | **20.4** | **23.5** |   5.6%   |

Across all three frameworks, p99 latency drops by **60–66%** at the cost of
~5–7% extra backend traffic. Reproduce with `make bench-multi && make bench-plot`.

---

## Quick Start

```bash
# Install with your preferred framework
pip install hedge-python[httpx]
pip install hedge-python[aiohttp]
pip install hedge-python[niquests]
pip install hedge-python[tornado]
pip install hedge-python[grpc]
pip install hedge-python[all]   # all frameworks
```

### httpx

```python
import asyncio
import httpx
from hedge import HedgeConfig
from hedge.transport import HedgedHttpxTransport

async def main():
    transport = HedgedHttpxTransport(config=HedgeConfig())
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://api.example.com/data")
        print(resp.status_code)

asyncio.run(main())
```

### aiohttp

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedAiohttpSession

async def main():
    async with HedgedAiohttpSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        data = await resp.json()
        print(data)

asyncio.run(main())
```

### gRPC (Unary)

```python
import grpc.aio
from hedge import HedgeConfig
from hedge.interceptor import HedgedUnaryInterceptor

async def make_channel():
    return grpc.aio.insecure_channel(
        "localhost:50051",
        interceptors=[HedgedUnaryInterceptor(config=HedgeConfig(estimated_rps=500))],
    )
```

### gRPC (Server Streaming — LLM inference, log tailing, …)

```python
import grpc.aio
from hedge import HedgeConfig
from hedge.interceptor import HedgedServerStreamInterceptor

async def make_channel():
    return grpc.aio.insecure_channel(
        "localhost:50051",
        interceptors=[HedgedServerStreamInterceptor(config=HedgeConfig())],
    )
```

### niquests

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedNiquestsSession

async def main():
    async with HedgedNiquestsSession(config=HedgeConfig()) as session:
        resp = await session.get("https://api.example.com/data")
        print(resp.status_code)

asyncio.run(main())
```

### tornado

```python
import asyncio
from hedge import HedgeConfig
from hedge.transport import HedgedTornadoClient

async def main():
    async with HedgedTornadoClient(config=HedgeConfig()) as client:
        resp = await client.fetch("https://api.example.com/data")
        print(resp.code)

asyncio.run(main())
```

### OpenAI SDK

Since the OpenAI Python SDK uses httpx under the hood, you can inject
`HedgedHttpxTransport` directly via the `http_client` parameter:

```python
import httpx
from openai import AsyncOpenAI
from hedge import HedgeConfig
from hedge.transport import HedgedHttpxTransport

transport = HedgedHttpxTransport(config=HedgeConfig(percentile=0.95))
client = AsyncOpenAI(
    api_key="sk-...",
    http_client=httpx.AsyncClient(transport=transport),
)
```

> **Note**: OpenAI's core APIs (Chat Completions, Embeddings, etc.) use POST,
> so they are **not** hedged by default — avoiding double billing. Only GET
> endpoints (e.g. model listing) are hedged. See
> [`examples/openai_hedged.py`](examples/openai_hedged.py) for a full example.

For server streaming, the hedge signal is **time-to-first-message (TTFM)**: if
the primary stream doesn't yield its first chunk within the estimated p90,
a backup stream is started. Whichever yields first wins and continues
streaming; the loser is cancelled at the wire level.

> **Runnable examples** for each framework live in [`examples/`](examples/) —
> the gRPC ones are fully self-contained (they spin up a local server with
> simulated stragglers so you can see hedging in action without any external
> dependency). See [`examples/README.md`](examples/README.md) for the index.

---

## How It Works

### 1. DDSketch quantile estimator

Each target host gets a `WindowedSketch` — a pair of DDSketches that rotate
every 30 seconds. DDSketch uses logarithmic bucket mapping to provide
**relative-error guarantees**: any quantile estimate is within ±1% of the
true value, regardless of the underlying distribution.

### 2. Adaptive trigger

On each request, the transport queries the sketch for the configured
percentile (default p90). If the primary hasn't responded by that deadline,
a backup request is fired. Whichever response arrives first is returned;
the loser is cancelled (including the underlying gRPC `Call` for streams).

```
              ┌─ primary  ─────────── ✓ (fast) ──→ return
request ──────┤
              └─ hedge fires after p90 ─── ✗ (cancelled)
```

### 3. Token bucket budget

Hedges are rate-limited by a token bucket that refills at
`estimated_rps × budget_percent / 100` tokens per second. During genuine
outages the bucket drains and hedging stops automatically — preventing the
load-doubling spiral that would deepen the incident.

### gRPC implementation note

The gRPC `intercept_unary_unary` continuation returns a `Call` object almost
immediately; the real RTT is spent in the subsequent `await call`. We wrap
**both steps** in a single asyncio task so the hedge timer reflects true
end-to-end RPC latency. Cancelling a loser invokes `call.cancel()` first
(notifying the server) then `task.cancel()` (cleaning up the coroutine).

---

## Configuration

All knobs live on `HedgeConfig`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `percentile` | `float` | `0.90` | Sketch quantile used as hedge trigger |
| `max_hedges` | `int` | `1` | Maximum concurrent hedge requests per call |
| `budget_percent` | `float` | `10.0` | Max hedge rate as percent of total traffic |
| `estimated_rps` | `float` | `100.0` | Expected requests per second; sets token bucket capacity |
| `min_delay` | `float` | `0.001` | Floor on the hedge delay in seconds |
| `warmup_requests` | `int` | `20` | Number of initial requests using fixed delay |
| `warmup_delay` | `float` | `0.01` | Fixed hedge delay during warmup in seconds |
| `window_duration` | `float` | `30.0` | Sketch window rotation interval in seconds |
| `stats` | `Stats \| None` | `None` | Inject a custom `Stats` for observability |

> **Tip — `estimated_rps`**: pick a value close to your real RPS so the token
> bucket capacity (`rps × budget_percent / 100`) is meaningful. If unsure,
> start at the default `100.0` and watch `hedge_rate` / `budget_exhausted` in
> the stats snapshot.

---

## Observability

```python
from hedge import HedgeConfig, Stats
from hedge.transport import HedgedHttpxTransport

stats = Stats()
transport = HedgedHttpxTransport(config=HedgeConfig(stats=stats))

# ... after running some traffic ...
snap = stats.snapshot()
print(f"total={snap.total_requests} hedged={snap.hedged_requests}")
print(f"hedge_wins={snap.hedge_wins} primary_wins={snap.primary_wins}")
print(f"budget_exhausted={snap.budget_exhausted}")
print(f"hedge_rate={stats.hedge_rate():.2%}")
```

`Stats` is fully thread-safe and can be shared across multiple
transports/interceptors to aggregate metrics.

---

## Benchmarks & charts

Two benchmark suites ship with the project:

| Command            | What it does                                                           | Output                                |
|--------------------|------------------------------------------------------------------------|---------------------------------------|
| `make bench-compare` | httpx only: No hedging vs Static 10ms vs Static 50ms vs Adaptive    | `benchmark/results.csv`               |
| `make bench-multi`   | httpx vs aiohttp vs gRPC, No hedging vs Adaptive                    | `benchmark/results_multi.csv`         |
| `make bench-plot`    | Render both CSVs into charts                                        | `eval.png`, `eval_multi_framework.png` |

Each suite runs 500 requests against a simulated lognormal latency
(`mean=5ms, stddev=2ms`) with 5% straggler probability (10× spike).

---

## Development

```bash
# Install uv (if not already)
curl -LsSf https://astral.sh/uv/install.sh | sh

make install            # install all extras with uv
make lint               # ruff check
make typecheck          # mypy
make test               # all tests
make test-unit          # unit tests only
make test-integration   # integration tests (requires httpx / aiohttp / grpcio)
make coverage           # coverage report (current: 96%)
make bench-multi        # multi-framework benchmark
make bench-plot         # render charts
make ci                 # lint + typecheck + test + coverage
```

### Testing

* **Unit tests** (`tests/unit/`): DDSketch, token bucket, scheduler, stats,
  options, lazy import shims, gRPC interceptor branches (with fake
  continuations).
* **Integration tests** (`tests/integration/`): real httpx transport, real
  aiohttp session, **real local gRPC server** with `.proto` + generated pb2.
* **Benchmarks** (`tests/benchmark/`): DDSketch microbench, token bucket
  microbench, four-config comparison, three-framework comparison.

Current coverage: **97%** (150 tests, ~7 seconds).

---

## Project Structure

```
hedge-python/
├── src/hedge/
│   ├── __init__.py          # Public API
│   ├── _options.py          # HedgeConfig dataclass
│   ├── _stats.py            # Thread-safe Stats + StatsSnapshot
│   ├── sketch/
│   │   ├── _ddsketch.py     # DDSketch quantile estimator
│   │   └── _windowed.py     # Sliding-window DDSketch pair
│   ├── budget/
│   │   └── _token_bucket.py # Token bucket rate limiter
│   ├── transport/
│   │   ├── _base.py         # Shared HedgeScheduler logic
│   │   ├── _httpx.py        # httpx AsyncBaseTransport adapter
│   │   ├── _aiohttp.py      # aiohttp session wrapper
│   │   ├── _niquests.py     # niquests session wrapper
│   │   └── _tornado.py      # tornado AsyncHTTPClient wrapper
│   └── interceptor/
│       └── _grpc.py         # gRPC unary + server-stream interceptors
├── tests/
│   ├── unit/                # 7 unit-test files
│   ├── integration/
│   │   ├── proto/           # .proto + generated pb2 / pb2_grpc
│   │   ├── test_httpx_transport.py
│   │   ├── test_aiohttp_session.py
│   │   └── test_grpc_interceptor.py
│   └── benchmark/
│       ├── test_bench_ddsketch.py
│       ├── test_bench_token_bucket.py
│       ├── test_bench_hedge_comparison.py    # httpx 4-config
│       └── test_bench_multi_framework.py     # 3-framework comparison
├── benchmark/
│   ├── plot.py              # CSV → matplotlib charts
│   ├── results.csv          # produced by bench-compare
│   └── results_multi.csv    # produced by bench-multi
├── eval.png                 # single-framework chart
├── eval_multi_framework.png # cross-framework chart
├── pyproject.toml
├── Makefile
└── .github/workflows/ci.yml
```

---

## References

- Jeffrey Dean and Luiz André Barroso. ["The Tail at Scale."](https://research.google/pubs/the-tail-at-scale/) *Communications of the ACM*, 56(2):74–80, February 2013.
- Charles Masson, Jee E. Rim, and Homin K. Lee. ["DDSketch: A Fast and Fully-Mergeable Quantile Sketch with Relative-Error Guarantees."](https://arxiv.org/abs/2004.08604) *Proceedings of the VLDB Endowment*, 12(12):2195–2205, 2019.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

## License

`hedge-python` is released under the [MIT License](LICENSE).
