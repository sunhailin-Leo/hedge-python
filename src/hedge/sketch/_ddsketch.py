"""DDSketch streaming quantile estimator.

Based on Masson et al., "DDSketch: A fast and fully-mergeable quantile sketch
with relative-error guarantees", VLDB 2019.
"""

from __future__ import annotations

import math
from collections import defaultdict


class _LogMapping:
    """Maps values to bucket indices using logarithmic scaling.

    For a given relative accuracy alpha, gamma = (1+alpha)/(1-alpha).
    A positive value x maps to bucket index ceil(ln(x) / ln(gamma)).

    The guarantee: any value in bucket i is within a factor of gamma^0.5 of
    the bucket's representative value, giving relative error <= alpha.
    """

    __slots__ = ("gamma", "multiplier")

    def __init__(self, relative_accuracy: float) -> None:
        self.gamma = (1 + relative_accuracy) / (1 - relative_accuracy)
        self.multiplier = 1.0 / math.log(self.gamma)

    def index(self, value: float) -> int:
        """Return the bucket index for a strictly positive value."""
        return math.ceil(math.log(value) * self.multiplier)

    def value(self, index: int) -> float:
        """Return the representative value (geometric midpoint) for a bucket."""
        return math.exp((index - 0.5) / self.multiplier)


class _Store:
    """Sparse map of bucket indices to cumulative counts with lazy sorted key cache."""

    __slots__ = ("bins", "count", "_sorted_keys_cache")

    def __init__(self) -> None:
        self.bins: dict[int, float] = defaultdict(float)
        self.count: float = 0.0
        self._sorted_keys_cache: list[int] | None = None

    @property
    def sorted_keys(self) -> list[int]:
        """Return sorted bin keys, building from cache when possible."""
        if self._sorted_keys_cache is None:
            self._sorted_keys_cache = sorted(self.bins.keys())
        return self._sorted_keys_cache

    def add(self, index: int) -> None:
        if index not in self.bins:
            self._sorted_keys_cache = None
        self.bins[index] += 1.0
        self.count += 1.0

    def merge(self, other: _Store) -> None:
        for idx, cnt in other.bins.items():
            self.bins[idx] += cnt
        self.count += other.count
        self._sorted_keys_cache = None

    def reset(self) -> None:
        self.bins = defaultdict(float)
        self.count = 0.0
        self._sorted_keys_cache = None


class DDSketch:
    """Streaming quantile sketch with relative-error guarantees.

    Positive and negative values are stored in separate sparse bucket maps.
    Zero values are counted separately. Min and max are tracked exactly.

    Property: for any quantile q, the returned estimate satisfies
        |estimate - true_value| / |true_value| <= relative_accuracy

    Args:
        relative_accuracy: Target relative accuracy. 0.01 means estimates
            are within +/-1% of the true value. Must be in (0, 1).
    """

    def __init__(self, relative_accuracy: float = 0.01) -> None:
        if not (0 < relative_accuracy < 1):
            raise ValueError("relative_accuracy must be in (0, 1)")
        self._mapping = _LogMapping(relative_accuracy)
        self._positive = _Store()
        self._negative = _Store()
        self._zero_count: float = 0.0
        self._count: int = 0
        self._min: float = math.inf
        self._max: float = -math.inf

    @property
    def count(self) -> int:
        """Total number of values added."""
        return self._count

    def add(self, value: float) -> None:
        """Record a single value. O(1) per insert.

        NaN and infinite values are silently ignored.
        """
        if math.isnan(value) or math.isinf(value):
            return

        if value > 0:
            self._positive.add(self._mapping.index(value))
        elif value < 0:
            self._negative.add(self._mapping.index(-value))
        else:
            self._zero_count += 1.0

        self._count += 1
        if value < self._min:
            self._min = value
        if value > self._max:
            self._max = value

    def quantile(self, q: float) -> float:
        """Return the estimated value at quantile q in [0, 1].

        Returns math.nan if the sketch is empty.

        The estimate satisfies the relative-error guarantee:
            |estimate - true_value| / |true_value| <= relative_accuracy
        """
        if self._count == 0:
            return math.nan
        if q <= 0:
            return self._min
        if q >= 1:
            return self._max

        rank: float = float(math.ceil(q * self._count))

        # Negative values: iterate descending (most negative -> least negative)
        if self._negative.count > 0:
            cumulative = 0.0
            neg_bins = self._negative.bins
            for idx in reversed(self._negative.sorted_keys):
                cumulative += neg_bins[idx]
                if cumulative >= rank:
                    return -self._mapping.value(idx)
            rank -= self._negative.count

        # Zero values
        if self._zero_count > 0:
            rank -= self._zero_count
            if rank <= 0:
                return 0.0

        # Positive values: iterate ascending (least positive -> most positive)
        if self._positive.count > 0:
            cumulative = 0.0
            pos_bins = self._positive.bins
            for idx in self._positive.sorted_keys:
                cumulative += pos_bins[idx]
                if cumulative >= rank:
                    return self._mapping.value(idx)

        return self._max

    def merge(self, other: DDSketch) -> None:
        """Combine other into self. The merge is exact: no additional error accumulates."""
        self._positive.merge(other._positive)
        self._negative.merge(other._negative)
        self._zero_count += other._zero_count
        self._count += other._count
        if other._count > 0:
            if other._min < self._min:
                self._min = other._min
            if other._max > self._max:
                self._max = other._max

    def reset(self) -> None:
        """Clear all state. Used for windowed / tumbling-window decay."""
        self._positive.reset()
        self._negative.reset()
        self._zero_count = 0.0
        self._count = 0
        self._min = math.inf
        self._max = -math.inf
