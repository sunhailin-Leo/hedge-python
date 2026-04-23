"""gRPC interceptors with adaptive hedging."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hedge.interceptor._grpc import HedgedServerStreamInterceptor, HedgedUnaryInterceptor


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "HedgedUnaryInterceptor":
        from hedge.interceptor._grpc import HedgedUnaryInterceptor

        return HedgedUnaryInterceptor
    if name == "HedgedServerStreamInterceptor":
        from hedge.interceptor._grpc import HedgedServerStreamInterceptor

        return HedgedServerStreamInterceptor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["HedgedUnaryInterceptor", "HedgedServerStreamInterceptor"]
