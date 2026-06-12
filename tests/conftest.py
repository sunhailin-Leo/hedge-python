"""Shared test fixtures."""

from __future__ import annotations

import platform

import pytest


@pytest.fixture
def short_window_duration() -> float:
    """A very short window duration for tests (100ms)."""
    return 0.1


def pytest_configure(config: pytest.Config) -> None:
    """Force SelectorEventLoop on Windows to avoid grpc.aio hang.

    Windows defaults to ProactorEventLoop, which causes grpc.aio internal
    tasks to hang on event loop shutdown. SelectorEventLoop does not have
    this issue.
    """
    if platform.system() == "Windows":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
