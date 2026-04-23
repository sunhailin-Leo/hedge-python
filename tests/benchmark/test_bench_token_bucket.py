"""Benchmark tests for TokenBucket performance."""

import pytest

from hedge.budget._token_bucket import TokenBucket


@pytest.mark.benchmark
class TestTokenBucketBenchmark:
    def test_try_acquire_throughput(self, benchmark) -> None:
        """Benchmark try_acquire() when tokens are available."""
        bucket = TokenBucket(budget_percent=100.0, estimated_rps=1000000.0)

        benchmark(bucket.try_acquire)

    def test_try_acquire_exhausted(self, benchmark) -> None:
        """Benchmark try_acquire() when bucket is empty."""
        bucket = TokenBucket(budget_percent=0.01, estimated_rps=0.01)
        # Drain the bucket
        while bucket.try_acquire():
            pass

        benchmark(bucket.try_acquire)
