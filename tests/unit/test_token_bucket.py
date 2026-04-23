"""Unit tests for TokenBucket rate limiter."""

import time

from hedge.budget._token_bucket import TokenBucket


class TestTokenBucketInit:
    def test_default_creation(self) -> None:
        bucket = TokenBucket()
        # Default: 100 RPS * 10% = 10 tokens/s, max_burst = 20
        assert bucket.try_acquire()

    def test_custom_creation(self) -> None:
        bucket = TokenBucket(budget_percent=5.0, estimated_rps=200.0)
        assert bucket.try_acquire()


class TestTokenBucketAcquire:
    def test_acquire_until_exhausted(self) -> None:
        # 10 RPS * 10% = 1 token/s, max_burst = 2
        bucket = TokenBucket(budget_percent=10.0, estimated_rps=10.0)
        # Starts with max_burst = 2 tokens
        assert bucket.try_acquire()
        assert bucket.try_acquire()
        # Should be exhausted now
        assert not bucket.try_acquire()

    def test_refill_over_time(self) -> None:
        # 100 RPS * 10% = 10 tokens/s
        bucket = TokenBucket(budget_percent=10.0, estimated_rps=100.0)
        # Drain all tokens (max_burst = 20)
        for _ in range(20):
            bucket.try_acquire()
        assert not bucket.try_acquire()
        # Wait for refill
        time.sleep(0.15)  # Should refill ~1.5 tokens
        assert bucket.try_acquire()

    def test_very_low_rate(self) -> None:
        # 1 RPS * 1% = 0.01 tokens/s, max_burst = 1.0 (minimum)
        bucket = TokenBucket(budget_percent=1.0, estimated_rps=1.0)
        assert bucket.try_acquire()
        assert not bucket.try_acquire()

    def test_tokens_capped_at_max_burst(self) -> None:
        bucket = TokenBucket(budget_percent=10.0, estimated_rps=100.0)
        # Wait a long time (tokens should cap at max_burst=20)
        time.sleep(0.3)
        acquired_count = 0
        for _ in range(100):
            if bucket.try_acquire():
                acquired_count += 1
        # Should not exceed max_burst (20)
        assert acquired_count <= 20


class TestTokenBucketSetRPS:
    def test_set_rps_increases_rate(self) -> None:
        bucket = TokenBucket(budget_percent=10.0, estimated_rps=10.0)
        # Drain tokens
        while bucket.try_acquire():
            pass
        # Increase RPS
        bucket.set_rps(1000.0)
        time.sleep(0.05)  # 1000 * 0.1 * 0.05 = 5 tokens
        assert bucket.try_acquire()

    def test_set_rps_preserves_budget_percent(self) -> None:
        bucket = TokenBucket(budget_percent=20.0, estimated_rps=50.0)
        bucket.set_rps(100.0)
        # rate should be 100 * 20% = 20 tokens/s
        # Drain and refill
        while bucket.try_acquire():
            pass
        time.sleep(0.1)  # 20 * 0.1 = 2 tokens
        assert bucket.try_acquire()
