"""Unit tests for Stats and StatsSnapshot."""

from hedge._stats import Stats, StatsSnapshot


class TestStats:
    def test_initial_values(self) -> None:
        stats = Stats()
        snap = stats.snapshot()
        assert snap.total_requests == 0
        assert snap.hedged_requests == 0
        assert snap.hedge_wins == 0
        assert snap.primary_wins == 0
        assert snap.budget_exhausted == 0
        assert snap.warmup_requests == 0

    def test_increment_total(self) -> None:
        stats = Stats()
        stats.increment_total()
        stats.increment_total()
        assert stats.snapshot().total_requests == 2

    def test_increment_hedged(self) -> None:
        stats = Stats()
        stats.increment_hedged()
        assert stats.snapshot().hedged_requests == 1

    def test_increment_hedge_wins(self) -> None:
        stats = Stats()
        stats.increment_hedge_wins()
        assert stats.snapshot().hedge_wins == 1

    def test_increment_primary_wins(self) -> None:
        stats = Stats()
        stats.increment_primary_wins()
        assert stats.snapshot().primary_wins == 1

    def test_increment_budget_exhausted(self) -> None:
        stats = Stats()
        stats.increment_budget_exhausted()
        assert stats.snapshot().budget_exhausted == 1

    def test_increment_warmup(self) -> None:
        stats = Stats()
        stats.increment_warmup()
        assert stats.snapshot().warmup_requests == 1


class TestHedgeRate:
    def test_zero_total(self) -> None:
        stats = Stats()
        assert stats.hedge_rate() == 0.0

    def test_no_hedges(self) -> None:
        stats = Stats()
        stats.increment_total()
        assert stats.hedge_rate() == 0.0

    def test_all_hedged(self) -> None:
        stats = Stats()
        for _ in range(10):
            stats.increment_total()
            stats.increment_hedged()
        assert stats.hedge_rate() == 1.0

    def test_partial_hedge(self) -> None:
        stats = Stats()
        for _ in range(100):
            stats.increment_total()
        for _ in range(10):
            stats.increment_hedged()
        assert abs(stats.hedge_rate() - 0.1) < 1e-9


class TestStatsSnapshot:
    def test_snapshot_is_frozen(self) -> None:
        stats = Stats()
        stats.increment_total()
        snap = stats.snapshot()
        # Modify stats after snapshot
        stats.increment_total()
        # Snapshot should not change
        assert snap.total_requests == 1
        assert stats.snapshot().total_requests == 2

    def test_snapshot_immutable(self) -> None:
        snap = StatsSnapshot(
            total_requests=10,
            hedged_requests=2,
            hedge_wins=1,
            primary_wins=1,
            budget_exhausted=0,
            warmup_requests=5,
        )
        assert snap.total_requests == 10
        assert snap.hedged_requests == 2
