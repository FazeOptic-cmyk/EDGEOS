from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from edgeos import worker
from edgeos.ingestion import WriteCounts
from edgeos.providers import DemoAdapter, ProviderCode, ProviderHTTPError, ProviderState


class RecordedAdapter(DemoAdapter):
    """No network; prove worker behavior using explicitly recorded inputs."""

    descriptor = replace(DemoAdapter.descriptor, code=ProviderCode.KALSHI)

    def __init__(self, *, fail_markets: bool = False) -> None:
        super().__init__()
        self.fail_markets = fail_markets
        self.closed = False

    async def list_events(self, query=None):
        self._metrics.requests_attempted += 1
        self._metrics.last_success_at = self._now()
        return await super().list_events(query)

    async def list_markets(self, query=None):
        self._metrics.requests_attempted += 1
        if self.fail_markets:
            raise ProviderHTTPError("Recorded provider", 503, retryable=True)
        return await super().list_markets(query)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def repository() -> Mock:
    result = Mock()
    result.acquire_lease.return_value = "lease"
    result.heartbeat.return_value = True
    result.remaining_request_budget.return_value = 120
    result.start_run.return_value = "run"
    result.persist_events.return_value = WriteCounts(received=1, created=1)
    result.persist_markets.return_value = WriteCounts(received=1, created=1)
    return result


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDGEOS_DEMO_MODE", raising=False)
    monkeypatch.setattr(worker, "settings", replace(worker.settings, mode="live"))
    monkeypatch.setenv("INGESTION_POLL_SECONDS", "60")
    monkeypatch.setenv("INGESTION_REQUEST_BUDGET_PER_HOUR", "120")


def test_live_cycle_failure_withdraws_provider_and_keeps_actual_request_count(monkeypatch) -> None:
    configure(monkeypatch)
    repo, pipeline = repository(), Mock()
    adapter = RecordedAdapter(fail_markets=True)
    cycle = Mock(return_value=SimpleNamespace(diagnostics=lambda: {"status": "completed"}))
    monkeypatch.setattr(worker, "run_post_ingestion_cycle", cycle)

    async def run() -> None:
        try:
            await worker.ingest_once(repo, pipeline, adapters=[adapter])
            # Caller-owned adapter survives this cycle for reconnect/backoff.
            assert not adapter.closed
        finally:
            await adapter.aclose()

    asyncio.run(run())
    health = repo.record_health.call_args.args[0]
    assert health.state is ProviderState.DISCONNECTED
    assert health.last_success_at is not None  # earlier event discovery succeeded
    assert health.last_error == "Recorded provider returned HTTP 503"
    pipeline.withdraw_provider_opportunities.assert_called_once_with(
        ("kalshi",), reason="provider_disconnected"
    )
    assert repo.finish_run.call_args.args[1] == "failed"
    assert repo.finish_run.call_args.args[3]["request_count"] == 2
    repo.persist_markets.assert_not_called()
    cycle.assert_called_once()
    repo.release.assert_called_once()


def test_rolling_budget_blocks_poll_but_runs_expiry_cycle(monkeypatch) -> None:
    configure(monkeypatch)
    repo, pipeline = repository(), Mock()
    repo.remaining_request_budget.return_value = 1
    adapter = RecordedAdapter()
    cycle = Mock(return_value=SimpleNamespace(diagnostics=lambda: {"status": "completed"}))
    monkeypatch.setattr(worker, "run_post_ingestion_cycle", cycle)

    async def run() -> None:
        try:
            await worker.ingest_once(repo, pipeline, adapters=[adapter])
        finally:
            await adapter.aclose()

    asyncio.run(run())
    repo.start_run.assert_not_called()
    assert adapter.health_snapshot().requests_attempted == 0
    assert repo.record_health.call_args.args[0].state is ProviderState.DELAYED
    cycle.assert_called_once()


def test_owned_adapters_close_after_lease_conflict(monkeypatch) -> None:
    configure(monkeypatch)
    repo = repository()
    repo.acquire_lease.return_value = None
    adapter = RecordedAdapter()
    monkeypatch.setattr(worker, "_adapters", lambda: [adapter])
    asyncio.run(worker.ingest_once(repo, Mock()))
    assert adapter.closed
    repo.start_run.assert_not_called()


def test_forever_preserves_adapters_and_closes_on_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(worker, "settings", replace(worker.settings, mode="demo"))
    adapter = Mock()
    adapter.aclose = AsyncMock()
    monkeypatch.setattr(worker, "_adapters", lambda: [adapter])
    poll = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(worker, "ingest_once", poll)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run_forever())
    assert poll.await_count == 2
    assert all(call.kwargs["adapters"] == [adapter] for call in poll.call_args_list)
    adapter.aclose.assert_awaited_once()


def test_worker_assumptions_are_explicit_and_unknown_measurements_stay_unknown(monkeypatch) -> None:
    for name in (
        "EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY", "EDGEOS_MEASURED_QUOTE_UPDATE_HZ",
        "EDGEOS_MEASURED_QUOTE_STABILITY", "EDGEOS_EXPECTED_LOCKUP_SECONDS",
        "EDGEOS_PIPELINE_SLIPPAGE_BPS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = worker._pipeline_config()
    assert config.slippage_bps == Decimal("10")
    assert config.partial_fill_probability is None
    assert config.quote_update_hz is None
    assert config.historical_stability is None
    monkeypatch.setenv("EDGEOS_PIPELINE_SLIPPAGE_BPS", "NaN")
    with pytest.raises(ValueError, match="finite"):
        worker._pipeline_config()
