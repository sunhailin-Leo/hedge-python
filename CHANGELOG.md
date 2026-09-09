# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Per-endpoint latency profiles** (`key_level="endpoint"` on `HedgeConfig`):
  key the DDSketch and warmup counters by `host + path` instead of by host,
  so endpoints with different latency profiles on the same host no longer
  skew each other's p90 estimates ([#2](https://github.com/sunhailin-Leo/hedge-python/issues/2)).
  Works across all HTTP transports (httpx, aiohttp, niquests, tornado); the
  client, its connection pool, and the token-bucket budget stay shared.
  Query strings are excluded from the key. `HedgeConfig` now validates
  `key_level` in `__post_init__` (invalid values raise `ValueError`).
- `extract_key(url, key_level)` helper in `hedge.transport._base` computing
  the latency-profile key for a URL.
- New example `examples/httpx_endpoint_profiles.py` (self-contained, no
  network needed) plus per-endpoint tests for every transport and gRPC
  per-method regression tests. Test coverage is now **99%** (190 tests).

### Changed

- `HedgeScheduler` methods now take a `key` argument (host or endpoint key)
  where they previously took `host`. These are internal building blocks;
  the public API (`HedgeConfig`, transports, interceptors) is unchanged and
  the default behavior (`key_level="host"`) is fully backward compatible.

## [0.2.0] - 2026-06-12

### Added

- **niquests adapter**: `HedgedNiquestsSession` — drop-in wrapper for
  `niquests.AsyncSession` with adaptive hedging on `GET`/`HEAD`/`OPTIONS`.
- **tornado adapter**: `HedgedTornadoClient` — wraps
  `tornado.httpclient.AsyncHTTPClient` with adaptive hedging via `fetch()`.
- **OpenAI integration example** (`examples/openai_hedged.py`): demonstrates
  injecting `HedgedHttpxTransport` into `openai.AsyncOpenAI` via `http_client`.
- New examples: `examples/niquests_basic.py`, `examples/tornado_basic.py`.
- Lazy-import registry refactored to a data-driven `_LAZY_IMPORTS` dict
  in `hedge.transport.__init__` for easier extensibility.
- `pyproject.toml` optional extras: `[niquests]`, `[tornado]` (also added
  to `[all]` and `[dev]`).
- Comprehensive unit tests for niquests and tornado adapters.
- Import-error tests for `niquests`, `tornado`, and `grpc` modules.
- Additional coverage tests: `TokenBucket.set_rps` truncation, `WindowedSketch`
  double-start idempotency, `DDSketch` quantile edge cases, gRPC interceptor
  `current_task()` None branch and `_PrependedStream` EOF.

### Changed

- `Makefile`: `test`, `test-unit`, `test-integration`, and `coverage` targets
  now depend on `install` to ensure all extras are available before running.
- `examples/README.md` updated with new framework entries.

### Other

- Test coverage: **97%** (150 tests).
- Supports Python 3.9 → 3.14.

## [0.1.0] - 2026-04-23

### Added

- Core hedge scheduler shared by all transports (`src/hedge/transport/_base.py`)
  with adaptive p90 trigger, token-bucket budget, and warmup phase.
- DDSketch quantile estimator (`src/hedge/sketch/_ddsketch.py`) and a
  windowed pair (`src/hedge/sketch/_windowed.py`) with background rotation.
- Token-bucket rate limiter (`src/hedge/budget/_token_bucket.py`) with
  preserved budget percent on RPS changes.
- Thread-safe `Stats` and immutable `StatsSnapshot` for observability
  (`src/hedge/_stats.py`).
- `HedgeConfig` dataclass exposing all tunables (`src/hedge/_options.py`).
- **httpx adapter**: `HedgedHttpxTransport` (`AsyncBaseTransport` subclass).
- **aiohttp adapter**: `HedgedAiohttpSession` (drop-in `ClientSession` wrapper).
- **gRPC interceptors**:
  - `HedgedUnaryInterceptor` for unary-unary RPCs.
  - `HedgedServerStreamInterceptor` for unary-stream RPCs (TTFM-based).
- Lazy import shims (`hedge.transport`, `hedge.interceptor`) so optional
  framework dependencies are only loaded when actually used.
- **Examples** (`examples/`):
  - `httpx_basic.py`, `aiohttp_basic.py` — public-API demos.
  - `grpc_unary.py`, `grpc_stream.py` — self-contained local-server demos
    that prove hedging actually fires under simulated stragglers.
- **Benchmarks**:
  - `tests/benchmark/test_bench_hedge_comparison.py` — httpx 4-config
    comparison (No hedging / Static 10ms / Static 50ms / Adaptive).
  - `tests/benchmark/test_bench_multi_framework.py` — httpx vs aiohttp vs
    gRPC, No hedging vs Adaptive.
  - `tests/benchmark/test_bench_ddsketch.py`,
    `tests/benchmark/test_bench_token_bucket.py` — microbenchmarks.
  - `benchmark/plot.py` — renders both CSVs into `eval.png` and
    `eval_multi_framework.png`.
- **Integration tests** with real local gRPC server
  (`tests/integration/proto/testservice.proto` + generated stubs).
- Multi-language `README` (English / 简体中文 / 日本語).
- GitHub Actions CI (`.github/workflows/ci.yml`): lint + typecheck +
  unit/integration tests + coverage gate.

### Updated

_Initial release — nothing to update yet._

### Removed

_Initial release — nothing removed._

### Other

- Test coverage: **97.41%** (122 tests, runs in ~7s without benchmarks).
- Lint: `ruff` clean across `src/` and `tests/` (generated protobuf stubs
  excluded via `extend-exclude`).
- `pyproject.toml` declares optional extras: `[httpx]`, `[aiohttp]`,
  `[grpc]`, `[all]`, `[dev]`.
- Supports Python 3.9 → 3.14.

[Unreleased]: https://github.com/sunhailin-Leo/hedge-python/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sunhailin-Leo/hedge-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sunhailin-Leo/hedge-python/releases/tag/v0.1.0
