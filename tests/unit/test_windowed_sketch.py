"""Unit tests for WindowedSketch sliding-window quantile estimation."""

import math
import time

import pytest

from hedge.sketch._windowed import WindowedSketch


class TestWindowedSketchInit:
    def test_default_creation(self) -> None:
        sketch = WindowedSketch()
        assert math.isnan(sketch.quantile(0.5))

    def test_custom_window_duration(self) -> None:
        sketch = WindowedSketch(window_duration=10.0)
        assert math.isnan(sketch.quantile(0.5))

    def test_zero_window_uses_default(self) -> None:
        sketch = WindowedSketch(window_duration=0.0)
        assert math.isnan(sketch.quantile(0.5))


class TestWindowedSketchAddAndQuantile:
    def test_add_and_query(self) -> None:
        sketch = WindowedSketch(0.01)
        for i in range(1, 101):
            sketch.add(float(i))
        median = sketch.quantile(0.5)
        assert abs(median - 50.0) / 50.0 <= 0.01

    def test_empty_returns_nan(self) -> None:
        sketch = WindowedSketch()
        assert math.isnan(sketch.quantile(0.5))


class TestWindowedSketchRotation:
    def test_manual_rotate(self) -> None:
        sketch = WindowedSketch(0.01)
        # Add to current
        for i in range(1, 51):
            sketch.add(float(i))
        # Rotate: current becomes previous
        sketch.rotate()
        # Add new data to current
        for i in range(51, 101):
            sketch.add(float(i))
        # Quantile merges both
        assert sketch.quantile(0.5) is not None
        assert not math.isnan(sketch.quantile(0.5))

    def test_double_rotate_drops_old(self) -> None:
        sketch = WindowedSketch(0.01)
        sketch.add(1000.0)
        sketch.rotate()
        sketch.rotate()
        # After two rotations, the original data is gone
        assert math.isnan(sketch.quantile(0.5))

    def test_data_survives_one_rotation(self) -> None:
        sketch = WindowedSketch(0.01)
        sketch.add(42.0)
        sketch.rotate()
        # Data should still be in previous
        estimate = sketch.quantile(0.5)
        assert abs(estimate - 42.0) / 42.0 <= 0.01


class TestWindowedSketchBackgroundRotation:
    def test_sync_start_stop(self) -> None:
        sketch = WindowedSketch(0.01, window_duration=0.05)
        sketch.start()
        for i in range(10):
            sketch.add(float(i + 1))
        time.sleep(0.12)  # Allow at least one rotation
        sketch.stop()
        # Should still be functional after stop
        sketch.add(5.0)
        assert not math.isnan(sketch.quantile(0.5))

    @pytest.mark.asyncio
    async def test_async_start_stop(self) -> None:
        import asyncio

        sketch = WindowedSketch(0.01, window_duration=0.05)
        sketch.start_async()
        for i in range(10):
            sketch.add(float(i + 1))
        await asyncio.sleep(0.12)
        sketch.stop()
        sketch.add(5.0)
        assert not math.isnan(sketch.quantile(0.5))

    def test_stop_without_start(self) -> None:
        sketch = WindowedSketch()
        sketch.stop()  # Should not raise

    def test_sync_start_twice_is_idempotent(self) -> None:
        sketch = WindowedSketch(0.01, window_duration=0.05)
        sketch.start()
        first_thread = sketch._thread
        sketch.start()  # second call should be a no-op
        assert sketch._thread is first_thread
        sketch.stop()

    @pytest.mark.asyncio
    async def test_async_start_twice_is_idempotent(self) -> None:

        sketch = WindowedSketch(0.01, window_duration=0.05)
        sketch.start_async()
        first_task = sketch._async_task
        sketch.start_async()  # second call should be a no-op
        assert sketch._async_task is first_task
        sketch.stop()
