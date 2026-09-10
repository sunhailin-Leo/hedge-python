"""Adaptive hedged request library for Python.

Learns per-host latency distributions using DDSketch, fires a backup request
when the primary exceeds its estimated p90, and caps hedge rate with a token
bucket to prevent load amplification during outages.
"""

from hedge._options import HedgeConfig
from hedge._stats import Stats, StatsSnapshot

__all__ = [
    "HedgeConfig",
    "Stats",
    "StatsSnapshot",
]

__version__ = "0.3.0"
