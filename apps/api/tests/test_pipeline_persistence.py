from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from edgeos.pipeline_persistence import (
    FeeScheduleRecord,
    MatchWrite,
    MarketRuleRecord,
    NormalizedMarketRecord,
    OutcomeQuoteRecord,
    PostgresPipelineRepository,
    to_engine_snapshots,
    _fee_schedule,
)


class FakeCursor:
    def __init__(self, result_sets: list[list[tuple[Any, ...]]] | None = None) -> None:
        self.result_sets = result_sets or [[]]
        self.index = -1
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.index += 1
        self.statements.append((sql, parameters))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result_sets[self.index]

    def fetchone(self) -> tuple[Any, ...] | None:
        rows = self.result_sets[self.index]
        return rows[0] if rows else None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_loader_requires_provider_timestamp_and_includes_exact_freshness_boundary() -> None:
    cursor = FakeCursor()
    repository = PostgresPipelineRepository(lambda: FakeConnection(cursor))
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)

    result = repository.load_market_snapshots(
        now=now, maximum_quote_age_seconds=30, limit=25
    )

    assert result.markets == ()
    sql, parameters = cursor.statements[0]
    assert "q.stale_at >= %s" in sql
    assert "provider_quote_timestamp_known' = 'true'" in sql
    assert "fetch_time_no_provider_timestamp" in sql
    assert "q.feed_class in ('streaming', 'polled')" in sql
    assert "pe.canonical_event_id::text" in sql
    assert parameters[2] == 30


def test_expiration_treats_exact_stale_at_boundary_as_fresh() -> None:
    cursor = FakeCursor()
    repository = PostgresPipelineRepository(lambda: FakeConnection(cursor))

    assert repository.expire_stale_opportunities(
        now=datetime(2026, 10, 20, 12, tzinfo=UTC)
    ) == ()

    sql = cursor.statements[0][0]
    assert "o.expires_at < %s" in sql
    assert "quote.stale_at < %s" in sql
    assert "quote.stale_at <= %s" not in sql


def test_mapper_preserves_provenance_and_blocks_disconnected_indicative_odds() -> None:
    observed = datetime(2026, 10, 20, 12, tzinfo=UTC)
    record = NormalizedMarketRecord(
        market_id="market-a",
        provider_id="provider-a",
        provider_slug="the_odds_api",
        provider_name="The Odds API",
        provider_kind="odds_aggregator",
        provider_status="disconnected",
        external_market_id="event:book:h2h",
        title="Book A h2h",
        market_type="h2h",
        period="full_game",
        line=None,
        currency="USD",
        live=False,
        minimum_increment=None,
        mutually_exclusive=True,
        collectively_exhaustive=True,
        closes_at=None,
        source_url="https://example.invalid/odds",
        metadata={
            "bookmaker_key": "book_a",
            "periodically_refreshed_not_immediately_executable": True,
            "settlement": {"resolution_source": "league"},
        },
        provider_event_id="provider-event-a",
        external_event_id="event-a",
        event_title="Alpha vs Beta",
        scheduled_start=observed + timedelta(hours=2),
        event_status="scheduled",
        event_source_url="https://example.invalid/event",
        event_metadata={
            "normalized_event": {
                "sport": "football",
                "league": "nfl",
                "home_team": "Alpha",
                "away_team": "Beta",
                "resolution_source": "league",
            }
        },
        semantic_event_id="semantic:event-a",
        canonical_event_id="canonical-event-a",
        rule=MarketRuleRecord(
            rule_id="rule-a",
            rule_version=1,
            includes_overtime=True,
            tie_treatment="not_applicable",
            push_treatment="not_applicable",
            void_treatment="refund",
            cancellation_treatment="refund",
            settlement_source="league",
            rules_url="https://example.invalid/rules",
            rules_text="Official rules",
            rules_hash="hash",
            verified_at=observed,
            metadata={
                "shootout_included": False,
                "draw_treatment": "not_applicable",
                "postponement_treatment": "refund",
                "retirement_treatment": "not_applicable",
                "abandonment_treatment": "refund",
                "line_unit": "points",
            },
        ),
        fee_schedule=None,
        outcomes=tuple(
            OutcomeQuoteRecord(
                outcome_id=f"outcome-{key}",
                external_outcome_id=key,
                canonical_key=key,
                name=key.title(),
                outcome_type=key,
                line=None,
                payout_per_unit=price,
                quote_id=f"quote-{key}",
                decimal_odds=price,
                probability_price=None,
                best_bid=None,
                best_ask=None,
                bid_size=None,
                ask_size=None,
                available_size=Decimal("100"),
                currency="USD",
                feed_class="polled",
                observed_at=observed,
                received_at=observed,
                stale_at=observed + timedelta(seconds=30),
                is_in_play=False,
            )
            for key, price in (("home", Decimal("2.1")), ("away", Decimal("2.0")))
        ),
    )

    snapshots, issues = to_engine_snapshots((record,))

    assert issues == ()
    snapshot = snapshots[0]
    assert snapshot.scenario_completeness_verified is True
    assert "provider_health_disconnected" in snapshot.execution_blockers
    assert "provider_marks_quotes_indicative" in snapshot.execution_blockers
    assert "quantity_increment_unknown" in snapshot.execution_blockers
    assert all(offer.provider_timestamp_known for offer in snapshot.offers)
    assert all(offer.execution_basis == "indicative" for offer in snapshot.offers)
    assert all(offer.offer.data_origin == "live_poll" for offer in snapshot.offers)
    assert set(snapshot.scenario_ids) == {
        "home", "away", "void", "cancellation", "postponement", "abandonment",
    }
    assert all(set(offer.payout_per_unit) == set(snapshot.scenario_ids) for offer in snapshot.offers)
    assert all(
        offer.payout_per_unit["postponement"] == offer.payout_per_unit["abandonment"] == Decimal("1")
        for offer in snapshot.offers
    )


@pytest.mark.parametrize(
    ("method", "parameters", "rounding_mode"),
    [
        ("percentage_notional", {"rate": None, "rounding_increment": "0.01"}, "ceiling"),
        ("percentage_notional", {"rate": "invalid", "rounding_increment": "0.01"}, "ceiling"),
        ("percentage_notional", {"rate": "NaN", "rounding_increment": "0.01"}, "ceiling"),
        ("percentage_notional", {"rate": "Infinity", "rounding_increment": "0.01"}, "ceiling"),
        ("percentage_notional", {"rate": "0.05", "rounding_increment": None}, "ceiling"),
        ("percentage_notional", {"rate": "0.05", "rounding_increment": "0"}, "ceiling"),
        ("flat", {"fixed_per_order": "bad", "rounding_increment": "0.01"}, "ceiling"),
        ("flat", {"per_contract": False, "rounding_increment": "0.01"}, "ceiling"),
        ("flat", {"per_contract": "0.01", "rate": "0.05", "rounding_increment": "0.01"}, "ceiling"),
        (
            "percentage_notional",
            {"rate": "0.05", "fixed_per_order": "1", "rounding_increment": "0.01"},
            "ceiling",
        ),
        ("none", {"rate": "0.05"}, "ceiling"),
        ("none", {}, "unknown"),
    ],
)
def test_invalid_fee_evidence_is_not_silently_replaced_with_zero(
    method: str, parameters: dict[str, Any], rounding_mode: str,
) -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    record = FeeScheduleRecord(
        schedule_id="fixture-fee", provider="fixture", effective_from=now,
        effective_to=None, calculation_method=method, parameters=parameters,
        rounding_mode=rounding_mode, currency="USD",
        source_url="https://fixture.example.test/fees", verified_at=now,
    )
    assert _fee_schedule(record) is None


@pytest.mark.parametrize("rounding_mode", ["ceiling", "floor", "half_up", "half_even"])
def test_verified_fee_rounding_mode_reaches_engine(rounding_mode: str) -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    record = FeeScheduleRecord(
        schedule_id="fixture-fee", provider="fixture", effective_from=now,
        effective_to=None, calculation_method="percentage_notional",
        parameters={"rate": "0.05", "rounding_increment": "0.01"},
        rounding_mode=rounding_mode, currency="USD",
        source_url="https://fixture.example.test/fees", verified_at=now,
    )
    fee = _fee_schedule(record)
    assert fee is not None
    assert fee.proportional_rate == Decimal("0.05")
    assert fee.rounding_mode == rounding_mode


def test_alert_evaluator_requires_all_configured_gates() -> None:
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    alert = (
        "alert", "user", "Strict edge", "roi_threshold",
        {
            "threshold": "0.02",
            "minimum_net_profit": "5",
            "minimum_net_roi": "0.03",
            "minimum_liquidity": "100",
            "maximum_quote_age_seconds": 30,
        },
        0, None,
    )
    opportunity = (
        "opportunity", "key", "active", "verified", Decimal("8"),
        Decimal("0.04"), Decimal("150"), now + timedelta(seconds=10),
        now - timedelta(seconds=15), ["kalshi"], ["moneyline"],
        "football", "nfl", "Alpha vs Beta", "USD",
    )

    assert PostgresPipelineRepository._alert_matches(alert, opportunity, now)
    too_stale = (*opportunity[:8], now - timedelta(seconds=31), *opportunity[9:])
    assert not PostgresPipelineRepository._alert_matches(alert, too_stale, now)


def test_provider_health_reads_worker_telemetry_without_network_calls() -> None:
    checked = datetime(2026, 10, 20, 12, tzinfo=UTC)
    row = (
        "kalshi", "Kalshi", "prediction_market", "polling", "connected", False,
        5, 1, True, checked, checked, None, None, None, "ok", 100, 90, None,
        42, 0, {"latency_ms": 25}, checked,
    )
    cursor = FakeCursor([[row]])
    health = PostgresPipelineRepository(lambda: FakeConnection(cursor)).list_provider_health()

    assert health[0]["provider"] == "kalshi"
    assert health[0]["state"] == "connected"
    assert health[0]["active_instruments"] == 42
    assert health[0]["diagnostics"] == {"latency_ms": 25}
    assert "provider_connections" in cursor.statements[0][0]


def test_match_persistence_is_idempotent_for_same_decision_fingerprint() -> None:
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    write = MatchWrite(
        left_market_id="10000000-0000-0000-0000-000000000001",
        right_market_id="10000000-0000-0000-0000-000000000002",
        classification="review_required",
        candidate_score=Decimal("0.9"),
        settlement_confidence=Decimal("0"),
        equivalence_checks={"unknown_fields": ["void_treatment"]},
        explanation="Void treatment is not explicit.",
        engine_version="edgeos.settlement.v2",
        left_rule_fingerprint="left",
        right_rule_fingerprint="right",
        decision_fingerprint="decision",
        source_evidence={"left": "source-a", "right": "source-b"},
    )
    first = FakeCursor([
        [],
        [("20000000-0000-0000-0000-000000000001",)],
        [],
    ])
    created = PostgresPipelineRepository(
        lambda: FakeConnection(first)
    ).persist_evaluation(write, None, observed_at=now)

    existing_row = (
        created.match_id,
        "review_required",
        "left",
        "right",
        "decision",
        1,
        None,
        "system",
    )
    replay = FakeCursor([[existing_row], [], []])
    repeated = PostgresPipelineRepository(
        lambda: FakeConnection(replay)
    ).persist_evaluation(write, None, observed_at=now)

    assert created.match_id == repeated.match_id
    assert any("insert into public.match_reviews" in sql for sql, _ in first.statements)
    assert not any("insert into public.match_reviews" in sql for sql, _ in replay.statements)
    update_parameters = replay.statements[1][1]
    assert update_parameters[10] == 1  # decision_version is stable on replay


def test_provider_disconnect_withdraws_opportunity_and_alert_in_one_transaction() -> None:
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    changed = (
        "opportunity-id", "opportunity-key", "stale", Decimal("100"),
        Decimal("4"), Decimal("3"), Decimal("0.03"), Decimal("100"),
        Decimal("0.8"),
    )
    cursor = FakeCursor([[changed], [], []])
    result = PostgresPipelineRepository(
        lambda: FakeConnection(cursor)
    ).withdraw_provider_opportunities(("kalshi",), now=now)

    assert result[0].status == "stale"
    assert "provider.slug=any(%s)" in cursor.statements[0][0]
    assert "opportunity_snapshots" in cursor.statements[1][0]
    assert "lifecycle_status='withdrawn'" in cursor.statements[2][0]
