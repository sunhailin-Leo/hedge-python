"""Benchmark tests for DDSketch performance."""

import random

import pytest

from hedge.sketch._ddsketch import DDSketch


@pytest.mark.benchmark
class TestDDSketchBenchmark:
    def test_add_throughput(self, benchmark) -> None:
        """Benchmark DDSketch.add() throughput."""
        sketch = DDSketch(0.01)
        values = [random.uniform(0.001, 0.1) for _ in range(10000)]
        idx = 0

        def add_one():
            nonlocal idx
            sketch.add(values[idx % len(values)])
            idx += 1

        benchmark(add_one)

    def test_quantile_small_sketch(self, benchmark) -> None:
        """Benchmark quantile query on a sketch with 1K values."""
        sketch = DDSketch(0.01)
        for _ in range(1000):
            sketch.add(random.uniform(0.001, 0.1))

        benchmark(sketch.quantile, 0.90)

    def test_quantile_large_sketch(self, benchmark) -> None:
        """Benchmark quantile query on a sketch with 100K values."""
        sketch = DDSketch(0.01)
        for _ in range(100000):
            sketch.add(random.uniform(0.001, 0.1))

        benchmark(sketch.quantile, 0.90)

    def test_merge_two_sketches(self, benchmark) -> None:
        """Benchmark merging two sketches with 10K values each."""
        sketch_a = DDSketch(0.01)
        sketch_b = DDSketch(0.01)
        for _ in range(10000):
            sketch_a.add(random.uniform(0.001, 0.1))
            sketch_b.add(random.uniform(0.001, 0.1))

        def do_merge():
            merged = DDSketch(0.01)
            merged.merge(sketch_a)
            merged.merge(sketch_b)

        benchmark(do_merge)

    def test_add_and_query_combined(self, benchmark) -> None:
        """Benchmark the typical hot path: add a value then query p90."""
        sketch = DDSketch(0.01)
        # Pre-fill with some data
        for _ in range(1000):
            sketch.add(random.uniform(0.001, 0.1))

        value = random.uniform(0.001, 0.1)

        def add_and_query():
            sketch.add(value)
            sketch.quantile(0.90)

        benchmark(add_and_query)
