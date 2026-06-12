"""OpenAI + hedge-python: adaptive hedging for OpenAI API calls.

Injects :class:`HedgedHttpxTransport` into ``openai.AsyncOpenAI`` via its
``http_client`` parameter. Because the OpenAI Python SDK uses httpx under the
hood, this gives idempotent API calls (GET/HEAD/OPTIONS) automatic
tail-latency hedging — with no changes to business logic.

**Important — hedging and OpenAI billing**:

Almost all OpenAI core APIs use ``POST`` (Chat Completions, Embeddings,
Image generation, etc.). Only query endpoints like ``GET /v1/models`` are
idempotent. By default, ``HedgedHttpxTransport`` only hedges ``GET / HEAD /
OPTIONS``, so POST-based calls pass through without duplication.

If you want to hedge POST calls (e.g. Chat Completions for lower tail
latency), subclass ``HedgedHttpxTransport`` and override the idempotency
check — but be aware that **each hedged request is billed separately**.

Run::

    export OPENAI_API_KEY="sk-..."
    uv run python examples/openai_hedged.py
"""

from __future__ import annotations

import asyncio
import os

import httpx

from hedge import HedgeConfig, Stats
from hedge.transport import HedgedHttpxTransport


async def main() -> None:
    # -- lazy import so the rest of the examples don't need openai installed --
    try:
        from openai import AsyncOpenAI
    except ImportError:
        print("This example requires the openai package:")
        print("  pip install openai")
        return

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("Set OPENAI_API_KEY to run this example.")
        return

    stats = Stats()
    config = HedgeConfig(
        percentile=0.95,
        budget_percent=10.0,
        estimated_rps=20.0,
        warmup_requests=5,
        warmup_delay=0.10,
        stats=stats,
    )

    # 1. Create the hedged transport
    hedged_transport = HedgedHttpxTransport(config=config)

    # 2. Build an httpx.AsyncClient that uses it
    http_client = httpx.AsyncClient(transport=hedged_transport)

    # 3. Inject into AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, http_client=http_client)

    # ------------------------------------------------------------------ #
    #  Example 1: GET /v1/models  — safe to hedge (GET is idempotent)    #
    # ------------------------------------------------------------------ #
    print("--- Listing models (GET — hedgeable) ---")
    models = await client.models.list()
    print(f"  found {len(models.data)} models")

    # ------------------------------------------------------------------ #
    #  Example 2: POST /v1/chat/completions — NOT hedged by default      #
    # ------------------------------------------------------------------ #
    print("\n--- Chat completion (POST — not hedged by default) ---")
    chat = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say hello in one word."}],
        max_tokens=10,
    )
    print(f"  reply: {chat.choices[0].message.content}")

    # ------------------------------------------------------------------ #
    #  Stats                                                              #
    # ------------------------------------------------------------------ #
    snap = stats.snapshot()
    print(f"\n--- Hedge stats ---")
    print(
        f"  total={snap.total_requests}  hedged={snap.hedged_requests}  "
        f"hedge_wins={snap.hedge_wins}  primary_wins={snap.primary_wins}  "
        f"budget_exhausted={snap.budget_exhausted}"
    )
    print(f"  hedge_rate={stats.hedge_rate():.2%}")

    # Close only the client — it will close the transport automatically.
    await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
