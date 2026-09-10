"""Configuration options for hedge transports and interceptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from hedge._stats import Stats

#: Supported latency-profile granularities for ``HedgeConfig.key_level``.
KEY_LEVELS = ("host", "endpoint")


@dataclass
class HedgeConfig:
    """Configuration for hedge behavior.

    Attributes:
        percentile: Sketch quantile used as hedge trigger (default: 0.90).
        max_hedges: Maximum concurrent hedge requests per call (default: 1).
        budget_percent: Max hedge rate as percent of total traffic (default: 10.0).
        estimated_rps: Expected requests per second; sets token bucket capacity (default: 100.0).
        min_delay: Floor on the hedge delay in seconds (default: 0.001).
        warmup_requests: Number of initial requests using fixed delay (default: 20).
        warmup_delay: Fixed hedge delay during warmup in seconds (default: 0.01).
        window_duration: Sketch window rotation interval in seconds (default: 30.0).
        key_level: Granularity of latency tracking (default: ``"host"``).
            ``"host"`` pools all requests to the same host into a single
            sketch (the original behavior). ``"endpoint"`` tracks
            ``host + path`` separately, so endpoints with different latency
            profiles on the same host do not skew each other. The hedge
            budget and the underlying connection pool are shared regardless.
            Query strings are ignored when deriving the endpoint key.
    """

    percentile: float = 0.90
    max_hedges: int = 1
    budget_percent: float = 10.0
    estimated_rps: float = 100.0
    min_delay: float = 0.001
    warmup_requests: int = 20
    warmup_delay: float = 0.01
    window_duration: float = 30.0
    key_level: Literal["host", "endpoint"] = "host"
    stats: Stats | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate field values that are easy to mistype."""
        if self.key_level not in KEY_LEVELS:
            raise ValueError(f"key_level must be one of {KEY_LEVELS}, got {self.key_level!r}")
