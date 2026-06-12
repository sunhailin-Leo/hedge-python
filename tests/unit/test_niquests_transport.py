"""Unit tests for HedgedNiquestsSession."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hedge._options import HedgeConfig
from hedge.transport._niquests import HedgedNiquestsSession


@pytest.fixture
def config() -> HedgeConfig:
    return HedgeConfig(warmup_requests=0, warmup_delay=0.001)


class TestHedgedNiquestsSessionInit:
    def test_default_config(self) -> None:
        session = HedgedNiquestsSession()
        assert session._config.percentile == 0.90

    def test_custom_config(self, config: HedgeConfig) -> None:
        session = HedgedNiquestsSession(config=config)
        assert session._config.warmup_requests == 0

    def test_session_created_lazily(self) -> None:
        session = HedgedNiquestsSession()
        assert session._session is None

    def test_stats_accessible(self) -> None:
        session = HedgedNiquestsSession()
        assert session.stats is not None


class TestHedgedNiquestsSessionRequests:
    @pytest.mark.asyncio
    async def test_get_request(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                response = await session.get("https://example.com/data")
                assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_post_request_no_hedge(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                response = await session.post("https://example.com/data")
                assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_put_request(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                await session.put("https://example.com/data")

    @pytest.mark.asyncio
    async def test_delete_request(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                await session.delete("https://example.com/data")

    @pytest.mark.asyncio
    async def test_head_request(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                await session.head("https://example.com/data")

    @pytest.mark.asyncio
    async def test_options_request(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                await session.options("https://example.com/data")


class TestHedgedNiquestsSessionStats:
    @pytest.mark.asyncio
    async def test_total_incremented(self, config: HedgeConfig) -> None:
        mock_response = MagicMock()

        with patch.object(HedgedNiquestsSession, "_get_session") as mock_get:
            mock_session = AsyncMock()
            mock_session.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_session

            async with HedgedNiquestsSession(config=config) as session:
                await session.get("https://example.com/data")
                snapshot = session.stats.snapshot()
                assert snapshot.total_requests >= 1


class TestHedgedNiquestsSessionLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        async with HedgedNiquestsSession() as session:
            assert isinstance(session, HedgedNiquestsSession)

    @pytest.mark.asyncio
    async def test_close_without_session(self) -> None:
        session = HedgedNiquestsSession()
        await session.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_real_session_creation_and_close(self) -> None:
        """Exercise the real _get_session() path (no mocking) and close()."""
        import niquests

        session = HedgedNiquestsSession()
        # Trigger real session creation
        real = session._get_session()
        assert isinstance(real, niquests.AsyncSession)
        assert session._session is real
        # Second call returns the same instance
        assert session._get_session() is real
        # Close should shut down the real session and clear the reference
        await session.close()
        assert session._session is None
