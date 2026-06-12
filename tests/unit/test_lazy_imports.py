"""Cover the lazy-import shims in ``hedge.transport`` and ``hedge.interceptor``.

Both packages expose their framework-specific classes via ``__getattr__`` so
that importing :mod:`hedge.transport` doesn't pay for ``httpx`` / ``aiohttp``
unless the user actually accesses one of those names. These tests exercise
both the success path and the unknown-attribute error path.
"""

from __future__ import annotations

import importlib

import pytest


class TestTransportLazyImports:
    def test_lazy_loads_httpx_transport(self) -> None:
        module = importlib.import_module("hedge.transport")
        cls = module.HedgedHttpxTransport
        # Re-access returns the same class object (cached by import system).
        assert module.HedgedHttpxTransport is cls
        assert cls.__name__ == "HedgedHttpxTransport"

    def test_lazy_loads_aiohttp_session(self) -> None:
        module = importlib.import_module("hedge.transport")
        cls = module.HedgedAiohttpSession
        assert cls.__name__ == "HedgedAiohttpSession"

    def test_lazy_loads_niquests_session(self) -> None:
        module = importlib.import_module("hedge.transport")
        cls = module.HedgedNiquestsSession
        assert cls.__name__ == "HedgedNiquestsSession"

    def test_lazy_loads_tornado_client(self) -> None:
        module = importlib.import_module("hedge.transport")
        cls = module.HedgedTornadoClient
        assert cls.__name__ == "HedgedTornadoClient"

    def test_unknown_attribute_raises(self) -> None:
        module = importlib.import_module("hedge.transport")
        with pytest.raises(AttributeError, match="DoesNotExist"):
            _ = module.DoesNotExist


class TestInterceptorLazyImports:
    def test_lazy_loads_unary_interceptor(self) -> None:
        module = importlib.import_module("hedge.interceptor")
        cls = module.HedgedUnaryInterceptor
        assert cls.__name__ == "HedgedUnaryInterceptor"

    def test_lazy_loads_server_stream_interceptor(self) -> None:
        module = importlib.import_module("hedge.interceptor")
        cls = module.HedgedServerStreamInterceptor
        assert cls.__name__ == "HedgedServerStreamInterceptor"

    def test_unknown_attribute_raises(self) -> None:
        module = importlib.import_module("hedge.interceptor")
        with pytest.raises(AttributeError, match="MissingThing"):
            _ = module.MissingThing
