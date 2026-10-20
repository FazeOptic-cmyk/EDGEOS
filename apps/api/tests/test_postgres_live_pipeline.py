"""Opt-in real PostgreSQL acceptance test with synthetic fixture evidence only.

Apply all migrations to a dedicated loopback database named ``edgeos_test`` and
set EDGEOS_TEST_DATABASE_URL. Every mutation uses one rollback-only transaction;
the test never truncates tables, deletes existing data, or contacts a provider.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

import edgeos.main as main_module
from edgeos.ingestion import PostgresIngestionRepository
from edgeos.opportunity_pipeline import PipelineConfig
from edgeos.pipeline_persistence import PostgresPipelineRepository
from edgeos.post_ingestion import run_post_ingestion_cycle
from edgeos.providers.models import (
    DataOrigin,
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOutcome,
    PriceType,
    ProviderCode,
    ProviderHealth,
    ProviderState,
)
from edgeos.research_persistence import OpportunityFilters, PostgresResearchRepository


def _health(provider: ProviderCode, now: datetime, *, connected: bool = True) -> ProviderHealth:
    return ProviderHealth(
        provider=provider,
        state=ProviderState.CONNECTED if connected else ProviderState.DISCONNECTED,
        checked_at=now,
        last_success_at=now if connected else None,
        last_error_at=None if connected else now,
        last_error=None if connected else "synthetic fixture disconnect",
        last_http_status=200 if connected else 503,
        last_latency_ms=12,
        requests_attempted=2,
        errors=0 if connected else 1,
        consecutive_failures=0 if connected else 1,
        rate_limit_events=0,
        rate_limit=None,
        message="Synthetic integration fixture; not observed provider telemetry.",
    )


def _event(provider: ProviderCode, key: str, now: datetime) -> NormalizedEvent:
    return NormalizedEvent(
        provider=provider,
        provider_event_id=f"{key}-event",
        title=f"Synthetic fixture {key}: Away at Home",
        subtitle="Synthetic fixture, not a real provider capture",
        sport="basketball",
        league=f"Fixture League {key}",
        home_team=f"Fixture Home {key}",
        away_team=f"Fixture Away {key}",
        scheduled_start=now + timedelta(hours=4),
        status="scheduled",
        resolution_source="synthetic fixture official result",
        source_url=f"https://fixture.example.test/{key}/{provider}/event",
        provider_timestamp=now,
        retrieved_at=now,
        data_origin=DataOrigin.LIVE_POLL,
        metadata={"evidence_kind": "synthetic_fixture"},
    )


def _market(
    provider: ProviderCode, key: str, now: datetime, home: str, away: str,
) -> NormalizedMarket:
    # LIVE_POLL exercises the operational path; URLs and metadata explicitly
    # identify every fabricated value as fixture evidence.
    from edgeos.providers.models import SettlementMetadata

    outcomes = tuple(
        NormalizedOutcome(
            provider_outcome_id=f"{key}-{provider}-{side}",
            name=side,
            description="Synthetic fixture outcome",
            side=side,
            line=None,
            offered_price=Decimal(ask),
            best_bid=Decimal(ask) - Decimal("0.01"),
            best_ask=Decimal(ask),
            bid_size=Decimal("500"),
            ask_size=Decimal("500"),
            last_price=None,
            price_type=PriceType.CONTRACT_DOLLARS,
            currency="USD",
        )
        for side, ask in (("home", home), ("away", away))
    )
    return NormalizedMarket(
        provider=provider,
        provider_market_id=f"{key}-{provider}-market",
        provider_market_slug=None,
        provider_event_id=f"{key}-event",
        title=f"Synthetic fixture {key}: game winner",
        market_type="moneyline",
        period="full_game",
        status="open",
        event_start_time=now + timedelta(hours=4),
        close_time=now + timedelta(hours=4),
        outcomes=outcomes,
        settlement=SettlementMetadata(
            primary_rules="Synthetic fixture: full game including overtime; every exceptional treatment is explicit.",
            resolution_source="synthetic fixture official result",
            void_treatment="not_applicable",
            push_treatment="not_applicable",
        ),
        liquidity=Decimal("1000"),
        quote_timestamp=now,
        retrieved_at=now,
        data_origin=DataOrigin.LIVE_POLL,
        source_url=f"https://fixture.example.test/{key}/{provider}/rules",
        metadata={
            "evidence_kind": "synthetic_fixture",
            "mutually_exclusive": True,
            "collectively_exhaustive": True,
            "includes_overtime": True,
            "shootout_included": False,
            "tie_treatment": "not_applicable",
            "draw_treatment": "not_applicable",
            "cancellation_treatment": "not_applicable",
            "postponement_treatment": "not_applicable",
            "retirement_treatment": "not_applicable",
            "abandonment_treatment": "not_applicable",
            "line_unit": "not_applicable",
            "quantity_increment": "0.01",
            "minimum_trade_quantity": "0.01",
        },
    )


def test_real_postgres_ingestion_opportunity_alert_withdrawal_and_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = os.getenv("EDGEOS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set EDGEOS_TEST_DATABASE_URL to a migrated loopback edgeos_test database")
    connection_options = conninfo_to_dict(dsn)
    assert connection_options.get("dbname") == "edgeos_test", "dedicated edgeos_test database required"
    host = connection_options.get("host")
    assert host in {"127.0.0.1", "localhost", "::1"}, "loopback required"
    assert connection_options.get("hostaddr") in {None, "127.0.0.1", "::1"}, "loopback required"
    key = f"fixture-{uuid4().hex}"
    now = datetime.now(UTC)
    providers = (ProviderCode.KALSHI, ProviderCode.POLYMARKET_INTERNATIONAL)
    with psycopg.connect(
        dsn, hostaddr="127.0.0.1" if host == "localhost" else host, connect_timeout=5,
    ) as connection:
        # Repository contexts normally commit their own connections. nullcontext
        # borrows this real connection, leaving all SQL in the outer transaction.
        with connection.transaction(force_rollback=True):
            factory = lambda: nullcontext(connection)  # noqa: E731
            ingestion = PostgresIngestionRepository(factory)
            pipeline = PostgresPipelineRepository(factory)
            research = PostgresResearchRepository(factory)
            cursor = connection.cursor()
            cursor.execute(
                "select current_database(), count(*) from public.opportunities"
            )
            initial = cursor.fetchone()
            assert initial is not None
            database, existing = initial
            assert database == "edgeos_test"
            assert existing == 0, "integration database must have no existing opportunities"
            user_id = uuid4()
            cursor.execute(
                "insert into auth.users (id,email) values (%s,%s)",
                (user_id, f"{key}@example.test"),
            )
            cursor.execute(
                """insert into public.user_alerts
                   (user_id,name,alert_type,conditions,cooldown_seconds)
                   values (%s,%s,'new_verified_opportunity','{}'::jsonb,0) returning id""",
                (user_id, f"Synthetic fixture {key}"),
            )
            alert_row = cursor.fetchone()
            assert alert_row is not None
            alert_id = alert_row[0]
            for provider, prices in zip(providers, (("0.45", "0.62"), ("0.61", "0.45")), strict=True):
                before_budget = ingestion.remaining_request_budget(provider, 100)
                run_id = ingestion.start_run(
                    provider, run_key=f"{key}-{provider}", adapter_version="synthetic-fixture-v1",
                    worker_id=key, reserved_requests=2,
                )
                assert ingestion.remaining_request_budget(provider, 100) == before_budget - 2
                events = ingestion.persist_events(
                    provider, run_id, (_event(provider, key, now),), adapter_version="synthetic-fixture-v1",
                )
                markets = ingestion.persist_markets(
                    provider, run_id, (_market(provider, key, now, *prices),),
                    adapter_version="synthetic-fixture-v1", stale_after_seconds=120,
                )
                assert events.created == markets.created == 1
                assert markets.rejected == 0
                ingestion.record_health(_health(provider, now), active_instruments=1)
                cursor.execute(
                    """insert into public.fee_schedules
                       (provider_id,name,version,effective_from,calculation_method,parameters,
                        official_source_url,verified_at)
                       select id,%s,%s,%s,'none','{}'::jsonb,%s,%s
                       from public.providers where slug=%s""",
                    (
                        f"Synthetic zero-fee fixture {key}", key, now - timedelta(days=1),
                        f"https://fixture.example.test/{key}/zero-fees", now, provider.value,
                    ),
                )
            loaded = pipeline.load_market_snapshots(now=now, maximum_quote_age_seconds=120)
            assert not loaded.issues
            assert len(loaded.markets) == 2
            assert len({market.canonical_event_id for market in loaded.markets}) == 1
            assert all(market.canonical_event_id and market.rule.rule_id for market in loaded.markets)
            assert all(market.metadata["evidence_kind"] == "synthetic_fixture" for market in loaded.markets)
            cursor.execute("select count(*) from public.raw_ingestion_records where ingestion_run_id in "
                           "(select id from public.ingestion_runs where worker_id=%s)", (key,))
            assert cursor.fetchone() == (4,)
            config = PipelineConfig(
                total_allocation=Decimal("100"), maximum_quote_age_seconds=120,
                minimum_liquidity=Decimal("10"),
            )
            first_market = loaded.markets[0]
            cursor.execute(
                "update public.provider_events set canonical_event_id=null where id=%s",
                (first_market.provider_event_id,),
            )
            unlinked = run_post_ingestion_cycle(pipeline, config, now=now)
            assert unlinked.strict_opportunities == unlinked.persisted_opportunities == 0
            assert unlinked.alert_events_created == 0
            cursor.execute(
                "update public.provider_events set canonical_event_id=%s where id=%s",
                (first_market.canonical_event_id, first_market.provider_event_id),
            )
            report = run_post_ingestion_cycle(pipeline, config, now=now)
            assert report.strict_opportunities == report.persisted_opportunities == 1, report
            assert report.alert_events_created == 1, report
            rows, total = research.list_opportunities(OpportunityFilters(search=key), now=now)
            assert total == 1
            opportunity_id = rows[0]["id"]
            assert rows[0]["potential_net_profit"] > 0
            assert rows[0]["settlement_status"] == "verified"
            detail = research.get_opportunity(opportunity_id)
            assert detail is not None
            assert len(detail["simulation"]["legs"]) == 2
            assert all(leg["price_kind"] == "contract_ask" for leg in detail["simulation"]["legs"])
            assert pipeline.evaluate_alerts((opportunity_id,), now=now).deduplicated == 1

            monkeypatch.delenv("EDGEOS_DEMO_MODE", raising=False)
            monkeypatch.setattr(main_module, "settings", replace(main_module.settings, mode="live"))
            monkeypatch.setattr(main_module, "research_repository", research)

            async def scanner() -> httpx.Response:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main_module.app), base_url="http://testserver",
                ) as client:
                    return await client.get("/api/v1/opportunities", params={"search": key})

            response = asyncio.run(scanner())
            assert response.status_code == 200, response.text
            dto = response.json()["items"][0]
            assert dto["id"] == opportunity_id
            assert Decimal(dto["potential_net_profit"]) > 0
            assert dto["currency"] == "USD"

            disconnected_at = now + timedelta(seconds=1)
            ingestion.record_health(_health(providers[0], disconnected_at, connected=False), 0)
            withdrawn = pipeline.withdraw_provider_opportunities((providers[0].value,), now=disconnected_at)
            assert len(withdrawn) == 1
            assert research.list_opportunities(OpportunityFilters(search=key), now=disconnected_at)[1] == 0
            stale_detail = research.get_opportunity(opportunity_id)
            assert stale_detail is not None and stale_detail["status"] == "stale"
            cursor.execute(
                "select lifecycle_status,withdrawn_at from public.user_alert_events where alert_id=%s",
                (alert_id,),
            )
            assert cursor.fetchone() == ("withdrawn", disconnected_at)
            blocked = run_post_ingestion_cycle(pipeline, config, now=disconnected_at)
            assert blocked.strict_opportunities == blocked.persisted_opportunities == 0
            assert research.list_opportunities(OpportunityFilters(search=key), now=disconnected_at)[1] == 0
            restored_at = now + timedelta(seconds=2)
            ingestion.record_health(_health(providers[0], restored_at), 1)
            restored = run_post_ingestion_cycle(pipeline, config, now=restored_at)
            assert restored.persisted_opportunities == restored.alert_events_created == 1, restored
            restored_rows, restored_total = research.list_opportunities(
                OpportunityFilters(search=key), now=restored_at,
            )
            assert restored_total == 1
            assert restored_rows[0]["id"] == opportunity_id
            assert restored_rows[0]["status"] == "active"
            cursor.execute(
                "select lifecycle_status,withdrawn_at,count(*) over() "
                "from public.user_alert_events where alert_id=%s", (alert_id,),
            )
            assert cursor.fetchone() == ("active", None, 1)
            cursor.execute(
                "select distinct status from public.opportunity_snapshots where opportunity_id=%s",
                (opportunity_id,),
            )
            assert {"active", "stale", "invalidated"} <= {row[0] for row in cursor.fetchall()}
            cursor.execute(
                """select status,snapshot_data->>'lifecycle_reason'
                   from public.opportunity_snapshots where opportunity_id=%s
                   order by observed_at desc limit 1""", (opportunity_id,),
            )
            assert cursor.fetchone() == ("active", "strict_evidence_restored")
        remaining = connection.execute(
            "select count(*) from public.ingestion_runs where worker_id=%s", (key,),
        ).fetchone()
        assert remaining == (0,), "fixture writes must roll back"
