from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import socket
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .config import settings
from .credentials import redact_secret_text
from .database import DatabaseConfigurationError, connection_factory
from .ingestion import PostgresIngestionRepository, WriteCounts
from .opportunity_pipeline import PipelineConfig
from .pipeline_persistence import PostgresPipelineRepository
from .post_ingestion import PipelineCycleRepository, run_post_ingestion_cycle
from .providers import (
    DemoAdapter,
    EventQuery,
    KalshiAdapter,
    MarketDataAdapter,
    MarketQuery,
    MissingCredentialError,
    PolymarketInternationalAdapter,
    PolymarketUSAdapter,
    ProviderState,
    TheOddsAPIAdapter,
    UnsupportedCapabilityError,
)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
logger = logging.getLogger("edgeos.worker")
LEASE_KEY = "market-ingestion"


def _adapters():
    if settings.demo_mode:
        return [DemoAdapter()]
    return [
        KalshiAdapter(), PolymarketInternationalAdapter(), PolymarketUSAdapter(),
        TheOddsAPIAdapter(api_key=settings.odds_api_key),
    ]


def _filters(adapter: object) -> list[dict[str, str]]:
    if not isinstance(adapter, TheOddsAPIAdapter):
        return [{}]
    sports = [value.strip() for value in os.getenv("THE_ODDS_API_SPORTS", "").split(",") if value.strip()]
    return [{"sport": sport} for sport in sports]


def _budget(provider: str) -> int:
    name = f"INGESTION_{provider.upper()}_REQUEST_BUDGET_PER_HOUR"
    return max(1, int(os.getenv(name, os.getenv("INGESTION_REQUEST_BUDGET_PER_HOUR", "120"))))


def _decimal_setting(name: str, default: str, *, allow_zero: bool = False) -> Decimal:
    raw = os.getenv(name, default).strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not value.is_finite() or value < 0 or (not allow_zero and value == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a finite {qualifier} decimal number")
    return value


def _optional_decimal_setting(name: str) -> Decimal | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return _decimal_setting(name, raw, allow_zero=True)


def _pipeline_config() -> PipelineConfig:
    """Read explicit research assumptions without fabricating fill reliability."""

    return PipelineConfig(
        total_allocation=_decimal_setting("EDGEOS_PIPELINE_TOTAL_ALLOCATION", "1000"),
        maximum_quote_age_seconds=settings.max_quote_age_seconds,
        maximum_quote_timestamp_skew_seconds=max(
            1, int(os.getenv("EDGEOS_MAX_QUOTE_SKEW_SECONDS", "5"))
        ),
        minimum_liquidity=_decimal_setting(
            "EDGEOS_PIPELINE_MINIMUM_LIQUIDITY", "1", allow_zero=True
        ),
        slippage_bps=_decimal_setting("EDGEOS_PIPELINE_SLIPPAGE_BPS", "10", allow_zero=True),
        partial_fill_probability=_optional_decimal_setting(
            "EDGEOS_MEASURED_PARTIAL_FILL_PROBABILITY"
        ),
        quote_update_hz=_optional_decimal_setting("EDGEOS_MEASURED_QUOTE_UPDATE_HZ"),
        historical_stability=_optional_decimal_setting(
            "EDGEOS_MEASURED_QUOTE_STABILITY"
        ),
        expected_lockup_seconds=_optional_decimal_setting(
            "EDGEOS_EXPECTED_LOCKUP_SECONDS"
        ),
    )


def _log(event: str, **values: object) -> None:
    logger.info(json.dumps({"event": event, **values, "timestamp": datetime.now(UTC).isoformat()}))


async def ingest_once(
    repository: PostgresIngestionRepository | None = None,
    pipeline_repository: PipelineCycleRepository | None = None,
    *,
    adapters: Sequence[MarketDataAdapter] | None = None,
) -> None:
    """Run one cycle, closing only the adapters this invocation created."""
    owns_adapters = adapters is None
    active_adapters = list(adapters) if adapters is not None else _adapters()
    try:
        await _ingest_cycle(repository, pipeline_repository, active_adapters)
    finally:
        if owns_adapters:
            await asyncio.gather(
                *(adapter.aclose() for adapter in active_adapters), return_exceptions=True
            )


async def _ingest_cycle(
    repository: PostgresIngestionRepository | None,
    pipeline_repository: PipelineCycleRepository | None,
    adapters: Sequence[MarketDataAdapter],
) -> None:
    """Poll configured deployment adapters and durably persist live data.

    Saved per-user connections are deliberately not consumed here. This worker
    uses public APIs plus the deployment's server-only ``THE_ODDS_API_KEY`` so a
    saved UI connection is never mistaken for an active polling job.
    """
    if settings.demo_mode and repository is None:
        for adapter in adapters:
            page = await adapter.list_markets(MarketQuery(limit=100))
            _log("demo_market_poll_complete", provider=adapter.descriptor.code.value,
                 records=len(page.items), data_origin=page.data_origin.value, is_demo=True,
                 persisted=False)
        return

    if repository is None:
        if not settings.database_url:
            raise DatabaseConfigurationError("DATABASE_URL is required for the live ingestion worker")
        repository = PostgresIngestionRepository(
            lambda: connection_factory(application_name="edgeos-ingestion-worker")
        )
    if pipeline_repository is None:
        pipeline_repository = PostgresPipelineRepository(
            lambda: connection_factory(application_name="edgeos-opportunity-pipeline")
        )

    holder_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    interval = max(15, int(os.getenv("INGESTION_POLL_SECONDS", "60")))
    ttl_seconds = max(300, interval * 3)
    lease = await asyncio.to_thread(repository.acquire_lease, LEASE_KEY, holder_id, ttl_seconds)
    if lease is None:
        _log("ingestion_cycle_skipped", reason="lease_held_by_another_worker")
        return

    try:
        for adapter in adapters:
            provider = adapter.descriptor.code
            filters_list = _filters(adapter)
            if isinstance(adapter, TheOddsAPIAdapter) and not filters_list:
                await asyncio.to_thread(
                    repository.record_health,
                    adapter.health_snapshot(
                        state=ProviderState.REQUIRES_API_KEY if not settings.odds_api_key else ProviderState.DELAYED,
                        message="Configure THE_ODDS_API_SPORTS to enable polling; no quota consumed.",
                    ),
                    0,
                )
                _log("market_poll_skipped", provider=provider.value,
                     reason="THE_ODDS_API_SPORTS has no official sport keys; no quota consumed")
                continue
            estimated_requests = len(filters_list) * 2
            estimated_requests_per_hour = math.ceil(3600 / interval) * estimated_requests
            budget = _budget(provider.value)
            if estimated_requests_per_hour > budget:
                await asyncio.to_thread(
                    repository.record_health,
                    adapter.health_snapshot(
                        state=ProviderState.DELAYED,
                        message="Polling paused: configured request budget is below the requested cadence.",
                    ),
                    0,
                )
                _log("market_poll_skipped", provider=provider.value,
                     reason="configured request budget is below the scheduled hourly estimate",
                     estimated_requests_per_hour=estimated_requests_per_hour,
                     request_budget_per_hour=budget)
                continue
            # Reserve the maximum HTTP attempts for the two discovery calls.
            # An interrupted run retains its reservation so restarts cannot
            # silently reset the rolling hourly request budget.
            reserved_requests = estimated_requests * adapter.maximum_request_attempts
            remaining = await asyncio.to_thread(repository.remaining_request_budget, provider, budget)
            if remaining < reserved_requests:
                await asyncio.to_thread(
                    repository.record_health,
                    adapter.health_snapshot(
                        state=ProviderState.DELAYED,
                        message="Polling paused until the rolling hourly request budget recovers.",
                    ),
                    0,
                )
                _log("market_poll_skipped", provider=provider.value,
                     reason="rolling_hourly_request_budget", remaining=remaining,
                     required_reservation=reserved_requests)
                continue

            adapter_version = f"{adapter.__class__.__module__}.{adapter.__class__.__name__}:1"
            run_id = await asyncio.to_thread(
                repository.start_run, provider, run_key=str(uuid4()),
                adapter_version=adapter_version, worker_id=holder_id,
                reserved_requests=reserved_requests,
            )
            counts = WriteCounts()
            request_count = 0
            status = "completed"
            error_code: str | None = None
            failed_health_state: ProviderState | None = None
            safe_error: str | None = None
            attempts_before = adapter.health_snapshot().requests_attempted
            active_instruments = 0
            try:
                for filters in filters_list:
                    if not await asyncio.to_thread(
                        repository.heartbeat, LEASE_KEY, holder_id, lease, ttl_seconds
                    ):
                        raise RuntimeError("ingestion lease was lost")
                    events = await adapter.list_events(EventQuery(limit=100, filters=filters))
                    request_count += 1
                    counts += await asyncio.to_thread(
                        repository.persist_events, provider, run_id, events.items,
                        adapter_version=adapter_version,
                    )
                    markets = await adapter.list_markets(MarketQuery(limit=100, filters=filters))
                    request_count += 1
                    active_instruments += len(markets.items)
                    counts += await asyncio.to_thread(
                        repository.persist_markets, provider, run_id, markets.items,
                        adapter_version=adapter_version,
                        stale_after_seconds=settings.max_quote_age_seconds,
                    )
                if counts.rejected:
                    status = "partial"
            except Exception as exc:
                status = "failed"
                error_code = type(exc).__name__
                secret = settings.odds_api_key or ""
                safe = redact_secret_text(str(exc), secrets=(secret,) if secret else ())
                safe_error = safe[:240]
                failed_health_state = (
                    ProviderState.REQUIRES_API_KEY if isinstance(exc, MissingCredentialError)
                    else ProviderState.NOT_SUPPORTED if isinstance(exc, UnsupportedCapabilityError)
                    else ProviderState.DISCONNECTED
                )
                logger.error(json.dumps({
                    "event": "market_poll_failed", "provider": provider.value,
                    "error_code": error_code, "safe_summary": safe[:240],
                    "timestamp": datetime.now(UTC).isoformat(),
                }))
            finally:
                health = adapter.health_snapshot(state=failed_health_state)
                if status == "failed":
                    health = replace(
                        health, last_error=safe_error, last_error_at=datetime.now(UTC),
                        consecutive_failures=max(1, health.consecutive_failures),
                    )
                request_count = max(0, health.requests_attempted - attempts_before)
                await asyncio.to_thread(repository.record_health, health, active_instruments)
                if status == "failed":
                    await asyncio.to_thread(
                        pipeline_repository.withdraw_provider_opportunities, (provider.value,),
                        reason=f"provider_{health.state.value}",
                    )
                await asyncio.to_thread(
                    repository.finish_run, run_id, status, counts,
                    {"request_count": request_count, "error_code": error_code,
                     "capture_stage": "normalized_adapter_contract",
                     "post_ingestion": {"status": "shared_cycle_follows_provider_polls"}},
                )
                _log("market_poll_complete", provider=provider.value, status=status,
                     records=counts.received, created=counts.created, updated=counts.updated,
                     rejected=counts.rejected, request_count=request_count,
                     persisted=True, is_demo=provider.value == "demo")

        if not await asyncio.to_thread(
            repository.heartbeat, LEASE_KEY, holder_id, lease, ttl_seconds
        ):
            raise RuntimeError("ingestion lease was lost before opportunity evaluation")
        report = await asyncio.to_thread(
            run_post_ingestion_cycle,
            pipeline_repository,
            _pipeline_config(),
        )
        _log("post_ingestion_cycle_complete", **report.diagnostics())
    finally:
        await asyncio.to_thread(repository.release, LEASE_KEY, holder_id, lease)


async def run_forever() -> None:
    interval = max(15, int(os.getenv("INGESTION_POLL_SECONDS", os.getenv("INGEST_POLL_INTERVAL_SECONDS", "60"))))
    if not settings.demo_mode and not settings.database_url:
        raise DatabaseConfigurationError("DATABASE_URL is required for the live ingestion worker")
    consecutive_failures = 0
    adapters = _adapters()
    try:
        while True:
            try:
                # Preserve provider retry deadlines, diagnostics, and caches
                # between cycles rather than resetting them after every poll.
                await ingest_once(adapters=adapters)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                retry_seconds = min(300, max(interval, 2 ** min(consecutive_failures, 8)))
                _log(
                    "ingestion_cycle_failed",
                    error_code=type(exc).__name__,
                    consecutive_failures=consecutive_failures,
                    retry_seconds=retry_seconds,
                )
                await asyncio.sleep(retry_seconds)
                continue
            consecutive_failures = 0
            await asyncio.sleep(interval)
    finally:
        await asyncio.gather(*(adapter.aclose() for adapter in adapters), return_exceptions=True)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
