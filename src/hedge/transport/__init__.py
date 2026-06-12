"""Framework-specific hedge transports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hedge.transport._aiohttp import HedgedAiohttpSession
    from hedge.transport._httpx import HedgedHttpxTransport
    from hedge.transport._niquests import HedgedNiquestsSession
    from hedge.transport._tornado import HedgedTornadoClient

_LAZY_IMPORTS: dict[str, str] = {
    "HedgedHttpxTransport": "_httpx",
    "HedgedAiohttpSession": "_aiohttp",
    "HedgedNiquestsSession": "_niquests",
    "HedgedTornadoClient": "_tornado",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is not None:
        import importlib

        module = importlib.import_module(f"hedge.transport.{module_name}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "HedgedHttpxTransport",
    "HedgedAiohttpSession",
    "HedgedNiquestsSession",
    "HedgedTornadoClient",
]
