"""Shared test fixtures."""

import pytest


@pytest.fixture
def short_window_duration() -> float:
    """A very short window duration for tests (100ms)."""
    return 0.1
