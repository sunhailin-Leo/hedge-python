"""Unit tests for HedgeConfig."""

import pytest

from hedge._options import HedgeConfig


class TestHedgeConfig:
    def test_defaults(self) -> None:
        config = HedgeConfig()
        assert config.percentile == 0.90
        assert config.max_hedges == 1
        assert config.budget_percent == 10.0
        assert config.estimated_rps == 100.0
        assert config.min_delay == 0.001
        assert config.warmup_requests == 20
        assert config.warmup_delay == 0.01
        assert config.window_duration == 30.0
        assert config.key_level == "host"
        assert config.stats is None

    def test_custom_values(self) -> None:
        config = HedgeConfig(
            percentile=0.95,
            max_hedges=2,
            budget_percent=5.0,
            estimated_rps=500.0,
            min_delay=0.005,
            warmup_requests=50,
            warmup_delay=0.02,
            window_duration=60.0,
        )
        assert config.percentile == 0.95
        assert config.max_hedges == 2
        assert config.budget_percent == 5.0
        assert config.estimated_rps == 500.0
        assert config.min_delay == 0.005
        assert config.warmup_requests == 50
        assert config.warmup_delay == 0.02
        assert config.window_duration == 60.0

    def test_key_level_endpoint(self) -> None:
        config = HedgeConfig(key_level="endpoint")
        assert config.key_level == "endpoint"

    def test_key_level_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="key_level"):
            HedgeConfig(key_level="per-endpoint")  # common typo
