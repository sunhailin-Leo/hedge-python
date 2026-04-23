"""Framework-specific hedge transports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hedge.transport._aiohttp import HedgedAiohttpSession
    from hedge.transport._httpx import HedgedHttpxTransport


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "HedgedHttpxTransport":
        from hedge.transport._httpx import HedgedHttpxTransport

        return HedgedHttpxTransport
    if name == "HedgedAiohttpSession":
        from hedge.transport._aiohttp import HedgedAiohttpSession

        return HedgedAiohttpSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["HedgedHttpxTransport", "HedgedAiohttpSession"]
