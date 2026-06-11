"""Cover the ``ImportError`` fallbacks in framework-specific transports.

Both :mod:`hedge.transport._httpx` and :mod:`hedge.transport._aiohttp` re-raise
``ImportError`` with a friendly install hint when the underlying framework is
missing. To exercise that branch deterministically we shadow the framework
module with ``None`` in ``sys.modules`` and force a reload — Python treats
``None`` as "import previously failed" and raises :class:`ImportError` for any
subsequent ``import`` of that name.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


def _reload_with_missing(framework_module: str, target_module: str) -> None:
    """Reload ``target_module`` while ``framework_module`` is unavailable."""
    saved_framework = sys.modules.get(framework_module)
    saved_target = sys.modules.get(target_module)

    # Shadow the framework with ``None`` so ``import framework`` raises.
    sys.modules[framework_module] = None  # type: ignore[assignment]
    if target_module in sys.modules:
        del sys.modules[target_module]

    try:
        importlib.import_module(target_module)
    finally:
        # Restore originals so other tests / sessions keep working.
        if saved_framework is not None:
            sys.modules[framework_module] = saved_framework
        else:
            sys.modules.pop(framework_module, None)
        if saved_target is not None:
            sys.modules[target_module] = saved_target
        else:
            sys.modules.pop(target_module, None)
        # Reimport target with framework available so cached references are sane.
        if saved_framework is not None:
            importlib.import_module(target_module)


@pytest.fixture
def isolate_modules() -> Iterator[None]:
    """Snapshot/restore relevant ``sys.modules`` keys around each test."""
    snapshot = {
        name: sys.modules.get(name)
        for name in (
            "httpx",
            "aiohttp",
            "niquests",
            "tornado",
            "tornado.httpclient",
            "grpc",
            "grpc.aio",
            "hedge.transport._httpx",
            "hedge.transport._aiohttp",
            "hedge.transport._niquests",
            "hedge.transport._tornado",
            "hedge.interceptor._grpc",
        )
    }
    yield
    for name, mod in snapshot.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def test_httpx_transport_raises_friendly_import_error(isolate_modules: None) -> None:
    """``HedgedHttpxTransport`` import without httpx must point at the extra."""
    with pytest.raises(ImportError, match=r"hedge-python\[httpx\]"):
        _reload_with_missing("httpx", "hedge.transport._httpx")


def test_aiohttp_session_raises_friendly_import_error(isolate_modules: None) -> None:
    """``HedgedAiohttpSession`` import without aiohttp must point at the extra."""
    with pytest.raises(ImportError, match=r"hedge-python\[aiohttp\]"):
        _reload_with_missing("aiohttp", "hedge.transport._aiohttp")


def test_niquests_session_raises_friendly_import_error(isolate_modules: None) -> None:
    """``HedgedNiquestsSession`` import without niquests must point at the extra."""
    with pytest.raises(ImportError, match=r"hedge-python\[niquests\]"):
        _reload_with_missing("niquests", "hedge.transport._niquests")


def test_tornado_client_raises_friendly_import_error(isolate_modules: None) -> None:
    """``HedgedTornadoClient`` import without tornado must point at the extra."""
    with pytest.raises(ImportError, match=r"hedge-python\[tornado\]"):
        _reload_with_missing("tornado.httpclient", "hedge.transport._tornado")


def test_grpc_interceptor_raises_friendly_import_error(isolate_modules: None) -> None:
    """``HedgedUnaryInterceptor`` import without grpcio must point at the extra."""
    with pytest.raises(ImportError, match=r"hedge-python\[grpc\]"):
        _reload_with_missing("grpc", "hedge.interceptor._grpc")
