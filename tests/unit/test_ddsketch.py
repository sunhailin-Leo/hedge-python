"""Unit tests for DDSketch quantile estimator."""

import math

import pytest

from hedge.sketch._ddsketch import DDSketch


class TestDDSketchInit:
    def test_default_accuracy(self) -> None:
        sketch = DDSketch()
        assert sketch.count == 0

    def test_custom_accuracy(self) -> None:
        sketch = DDSketch(0.05)
        assert sketch.count == 0

    def test_invalid_accuracy_zero(self) -> None:
        with pytest.raises(ValueError, match="relative_accuracy must be in"):
            DDSketch(0.0)

    def test_invalid_accuracy_one(self) -> None:
        with pytest.raises(ValueError, match="relative_accuracy must be in"):
            DDSketch(1.0)

    def test_invalid_accuracy_negative(self) -> None:
        with pytest.raises(ValueError, match="relative_accuracy must be in"):
            DDSketch(-0.5)


class TestDDSketchAdd:
    def test_add_positive(self) -> None:
        sketch = DDSketch()
        sketch.add(10.0)
        assert sketch.count == 1

    def test_add_negative(self) -> None:
        sketch = DDSketch()
        sketch.add(-5.0)
        assert sketch.count == 1

    def test_add_zero(self) -> None:
        sketch = DDSketch()
        sketch.add(0.0)
        assert sketch.count == 1

    def test_add_nan_ignored(self) -> None:
        sketch = DDSketch()
        sketch.add(float("nan"))
        assert sketch.count == 0

    def test_add_inf_ignored(self) -> None:
        sketch = DDSketch()
        sketch.add(float("inf"))
        sketch.add(float("-inf"))
        assert sketch.count == 0

    def test_min_max_tracking(self) -> None:
        sketch = DDSketch()
        sketch.add(5.0)
        sketch.add(1.0)
        sketch.add(10.0)
        assert sketch.quantile(0.0) == 1.0
        assert sketch.quantile(1.0) == 10.0


class TestDDSketchQuantile:
    def test_empty_returns_nan(self) -> None:
        sketch = DDSketch()
        assert math.isnan(sketch.quantile(0.5))

    def test_single_value(self) -> None:
        sketch = DDSketch(0.01)
        sketch.add(42.0)
        estimate = sketch.quantile(0.5)
        assert abs(estimate - 42.0) / 42.0 <= 0.01

    def test_quantile_zero_returns_min(self) -> None:
        sketch = DDSketch()
        for value in [3.0, 1.0, 5.0, 2.0, 4.0]:
            sketch.add(value)
        assert sketch.quantile(0.0) == 1.0

    def test_quantile_one_returns_max(self) -> None:
        sketch = DDSketch()
        for value in [3.0, 1.0, 5.0, 2.0, 4.0]:
            sketch.add(value)
        assert sketch.quantile(1.0) == 5.0

    def test_relative_error_guarantee(self) -> None:
        """Verify that quantile estimates satisfy the relative error guarantee."""
        accuracy = 0.01
        sketch = DDSketch(accuracy)

        # Add values representing typical latencies (1ms to 100ms)
        values = sorted([float(i) for i in range(1, 101)])
        for value in values:
            sketch.add(value)

        for quantile in [0.5, 0.75, 0.90, 0.95, 0.99]:
            estimate = sketch.quantile(quantile)
            index = int(math.ceil(quantile * len(values))) - 1
            true_value = values[index]
            if true_value != 0:
                relative_error = abs(estimate - true_value) / abs(true_value)
                assert relative_error <= accuracy, (
                    f"q={quantile}: estimate={estimate}, true={true_value}, "
                    f"error={relative_error:.4f} > {accuracy}"
                )

    def test_uniform_distribution_median(self) -> None:
        sketch = DDSketch(0.01)
        for i in range(1, 1001):
            sketch.add(float(i))
        median = sketch.quantile(0.5)
        assert abs(median - 500.0) / 500.0 <= 0.01

    def test_negative_values(self) -> None:
        sketch = DDSketch(0.01)
        for i in range(-100, 0):
            sketch.add(float(i))
        estimate = sketch.quantile(0.5)
        assert estimate < 0

    def test_mixed_positive_negative(self) -> None:
        sketch = DDSketch(0.01)
        for i in range(-50, 51):
            sketch.add(float(i))
        # Median should be close to 0
        median = sketch.quantile(0.5)
        assert abs(median) <= 1.0

    def test_p90_latency_scenario(self) -> None:
        """Simulate realistic latency data and verify p90 accuracy."""
        accuracy = 0.01
        sketch = DDSketch(accuracy)
        # 90% of requests at ~5ms, 10% at ~50ms (stragglers)
        for _ in range(900):
            sketch.add(5.0)
        for _ in range(100):
            sketch.add(50.0)
        p90 = sketch.quantile(0.90)
        # p90 should be around 5ms (the 900th value out of 1000)
        assert abs(p90 - 5.0) / 5.0 <= accuracy


class TestDDSketchMerge:
    def test_merge_empty(self) -> None:
        sketch_a = DDSketch()
        sketch_b = DDSketch()
        sketch_a.add(10.0)
        sketch_a.merge(sketch_b)
        assert sketch_a.count == 1

    def test_merge_preserves_count(self) -> None:
        sketch_a = DDSketch()
        sketch_b = DDSketch()
        for i in range(50):
            sketch_a.add(float(i))
        for i in range(50, 100):
            sketch_b.add(float(i))
        sketch_a.merge(sketch_b)
        assert sketch_a.count == 100

    def test_merge_preserves_min_max(self) -> None:
        sketch_a = DDSketch()
        sketch_b = DDSketch()
        sketch_a.add(10.0)
        sketch_a.add(20.0)
        sketch_b.add(5.0)
        sketch_b.add(30.0)
        sketch_a.merge(sketch_b)
        assert sketch_a.quantile(0.0) == 5.0
        assert sketch_a.quantile(1.0) == 30.0

    def test_merge_accuracy(self) -> None:
        """Merge should not accumulate additional error."""
        accuracy = 0.01
        sketch_a = DDSketch(accuracy)
        sketch_b = DDSketch(accuracy)
        for i in range(1, 501):
            sketch_a.add(float(i))
        for i in range(501, 1001):
            sketch_b.add(float(i))
        sketch_a.merge(sketch_b)
        median = sketch_a.quantile(0.5)
        assert abs(median - 500.0) / 500.0 <= accuracy


class TestDDSketchReset:
    def test_reset_clears_state(self) -> None:
        sketch = DDSketch()
        for i in range(100):
            sketch.add(float(i))
        sketch.reset()
        assert sketch.count == 0
        assert math.isnan(sketch.quantile(0.5))

    def test_reset_then_add(self) -> None:
        sketch = DDSketch(0.01)
        sketch.add(100.0)
        sketch.reset()
        sketch.add(42.0)
        assert sketch.count == 1
        estimate = sketch.quantile(0.5)
        assert abs(estimate - 42.0) / 42.0 <= 0.01
