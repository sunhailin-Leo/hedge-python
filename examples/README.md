# hedge-python examples

Runnable end-to-end examples for each supported framework. Each script is
self-contained: it constructs a `HedgeConfig`, wires up the corresponding
transport / interceptor, drives some traffic, and prints a `Stats` snapshot at
the end so you can see how many hedges fired, won, or were dropped by the
budget.

Install with all extras and run from the repo root:

```bash
uv sync --all-extras
uv run python examples/<file>.py
```

## Files

| Example | Framework | Notes |
|---------|-----------|-------|
| [`httpx_basic.py`](httpx_basic.py) | httpx | Wraps `httpx.AsyncClient` with `HedgedHttpxTransport`. Hits `httpbin.org` so it needs internet access. |
| [`httpx_endpoint_profiles.py`](httpx_endpoint_profiles.py) | httpx | **Self-contained** — per-endpoint latency profiles (`key_level="endpoint"`, issue #2): a fast and a slow endpoint on the same host learn independent p90s. No network needed. |
| [`aiohttp_basic.py`](aiohttp_basic.py) | aiohttp | Drop-in replacement `HedgedAiohttpSession`. Hits `httpbin.org`. |
| [`niquests_basic.py`](niquests_basic.py) | niquests | Drop-in replacement `HedgedNiquestsSession`. Hits `httpbin.org`. |
| [`tornado_basic.py`](tornado_basic.py) | tornado | Wraps `AsyncHTTPClient` with `HedgedTornadoClient`. Hits `httpbin.org`. |
| [`openai_hedged.py`](openai_hedged.py) | OpenAI SDK | Injects `HedgedHttpxTransport` into `AsyncOpenAI` via `http_client`. Requires `OPENAI_API_KEY`. |
| [`grpc_unary.py`](grpc_unary.py) | gRPC unary | **Self-contained** — starts a local gRPC server with random 80ms stragglers, trains the sketch, then fires a batch where hedging is guaranteed to trigger. |
| [`grpc_stream.py`](grpc_stream.py) | gRPC server streaming | **Self-contained** — same idea, but hedge fires on slow time-to-first-message (TTFM). |

The two gRPC examples reuse `tests/integration/proto/testservice.proto` and
its generated stubs, so they don't need any extra `.proto` work.

## Expected output (gRPC unary, abridged)

```
firing 20 requests with ~30% straggler rate ...
  total=50  warmup=30  hedged=6  hedge_wins=2  primary_wins=4  budget_exhausted=0
  hedge_rate=12.00%
  server saw 56 RPCs (extra are hedge duplicates)
```

The HTTP examples produce similar output but real numbers depend on network
conditions when talking to `httpbin.org`.

## Tuning tips

* **`estimated_rps`** — set close to your real RPS so the token bucket
  capacity (`rps × budget_percent / 100`) is meaningful.
* **`warmup_requests`** — give the sketch enough samples (≥20) before it
  drives the hedge timer; until then a fixed `warmup_delay` is used.
* **`budget_percent`** — start at `10.0` (the default). Raise it only if you
  see `hedge_rate` plateauing near the cap and you have spare backend
  capacity.
* **Idempotency** — non-idempotent verbs (`POST` / `PUT` / `DELETE`) are
  **not** hedged automatically. Only `GET` / `HEAD` / `OPTIONS` and
  Unary/ServerStream gRPC calls are.
* **`key_level`** — the default `"host"` pools every endpoint on one host
  into a single sketch. Switch to `"endpoint"` when endpoints on the same
  host have very different latency profiles (see
  [`httpx_endpoint_profiles.py`](httpx_endpoint_profiles.py)). Query
  strings never fork sketches; if your paths embed IDs (`/users/123`),
  expect one sketch per distinct path.
