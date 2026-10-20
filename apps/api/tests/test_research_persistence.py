from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from edgeos.research_persistence import OpportunityFilters, PostgresResearchRepository


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: tuple[object, ...] = ()

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.sql = sql
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_scanner_query_binds_search_and_preserves_unknown_market_inputs() -> None:
    detected = datetime(2026, 10, 20, tzinfo=UTC)
    cursor = FakeCursor([(
        "00000000-0000-0000-0000-000000000001", "A vs B", "football", "League",
        "Winner", ["Provider A", "Provider B"], [Decimal("2.2"), Decimal("2")],
        Decimal("0.047619"), Decimal("0.04"), Decimal("210"), Decimal("8.4"),
        None, detected, None, "unknown", detected, False, "verified", Decimal("88"),
        {}, "USD", "active", detected, 1,
    )])
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))
    malicious = "%' OR true; --"
    rows, total = repository.list_opportunities(
        OpportunityFilters(search=malicious), now=detected
    )

    assert total == 1
    assert rows[0]["available_liquidity"] is None
    assert rows[0]["quote_age_seconds"] is None
    assert rows[0]["data_origin"] == "unknown"
    assert rows[0]["currency"] == "USD"
    assert rows[0]["status"] == "active"
    assert rows[0]["expires_at"] == detected
    assert "unsupported.action not in ('back', 'buy_yes', 'buy_no')" in cursor.sql
    assert "o.status = 'active'" in cursor.sql
    assert malicious not in cursor.sql
    assert f"%{malicious}%" in cursor.parameters


def test_unallowlisted_sort_is_rejected_before_database_access() -> None:
    repository = PostgresResearchRepository(lambda: pytest.fail("database must not be touched"))
    with pytest.raises(ValueError, match="sort"):
        repository.list_opportunities(OpportunityFilters(sort="id; drop table public.quotes"))


def test_explicit_non_strict_scanner_can_retain_stale_research_rows() -> None:
    cursor = FakeCursor([])
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))
    repository.list_opportunities(OpportunityFilters(verified_only=False))
    assert "o.status in ('active', 'stale')" in cursor.sql


class OpportunityDetailCursor:
    def __init__(self, result_sets: list[object]) -> None:
        self._result_sets = result_sets
        self._index = -1
        self.statements: list[str] = []

    def __enter__(self) -> OpportunityDetailCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _sql: str, _parameters: tuple[object, ...]) -> None:
        self.statements.append(_sql)
        self._index += 1

    def fetchone(self) -> tuple[object, ...] | None:
        value = self._result_sets[self._index]
        return value if isinstance(value, tuple) else None

    def fetchall(self) -> list[tuple[object, ...]]:
        value = self._result_sets[self._index]
        assert isinstance(value, list)
        return value


def test_live_opportunity_detail_preserves_leg_price_contract() -> None:
    observed = datetime(2026, 10, 20, tzinfo=UTC)
    opportunity = (
        "00000000-0000-0000-0000-000000000001", "A vs B", "football", "League",
        "sportsbook_prediction", Decimal("210"), Decimal("10"), Decimal("1.25"),
        Decimal("8.75"), Decimal("8.75"), Decimal("0.047619"), Decimal("0.041667"),
        Decimal("210"), observed, observed, "verified", Decimal("90"), [],
        {"data_origin": "live_poll", "calculation_version": "edgeos.cashflow.v2"},
        "Rules agree", {"period": True}, "USD", "active", observed,
    )
    legs = [
        (
            "Same Platform", "Yes", Decimal("0.45"), Decimal("100"), Decimal("45"),
            Decimal("0.75"), observed, Decimal("100"), False, "binary", "Yes contract",
            "https://example.test/yes", "buy_yes",
        ),
        (
            "Same Platform", "Away", Decimal("2.00"), Decimal("110"), Decimal("110"),
            Decimal("0.50"), observed, Decimal("110"), False, "moneyline", "Away winner",
            "https://example.test/away", "back",
        ),
    ]
    outcomes = [
        ("yes", "Yes", Decimal("220"), Decimal("0"), Decimal("1.25"), Decimal("8.75")),
    ]
    history = [(observed, Decimal("0.041667"), Decimal("8.75"), Decimal("210"), "active")]
    cursor = OpportunityDetailCursor([opportunity, legs, outcomes, history])
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))

    detail = repository.get_opportunity(str(opportunity[0]))

    assert detail is not None
    assert "unsupported.action not in ('back', 'buy_yes', 'buy_no')" in cursor.statements[0]
    assert detail["prices"] == [Decimal("2.222222222222222222222222222"), Decimal("2.00")]
    assert detail["simulation"]["legs"] == [
        {
            "provider": "Same Platform", "outcome_id": "Yes",
            "requested_quantity": Decimal("100"), "filled_quantity": Decimal("100"),
            "cash_cost": Decimal("45"), "fees": Decimal("0.75"),
            "price_kind": "contract_ask", "unit_cost": Decimal("0.45"),
        },
        {
            "provider": "Same Platform", "outcome_id": "Away",
            "requested_quantity": Decimal("110"), "filled_quantity": Decimal("110"),
            "cash_cost": Decimal("110"), "fees": Decimal("0.50"),
            "price_kind": "decimal_odds", "unit_cost": Decimal("1"),
        },
    ]
    assert detail["status"] == "active"
    assert detail["expires_at"] == observed


def test_live_opportunity_detail_rejects_short_contract_legs() -> None:
    observed = datetime(2026, 10, 20, tzinfo=UTC)
    opportunity = (
        "00000000-0000-0000-0000-000000000001", "A vs B", "football", "League",
        "prediction_prediction", Decimal("50"), Decimal("2"), Decimal("0.25"),
        Decimal("1.75"), Decimal("1.75"), Decimal("0.04"), Decimal("0.035"),
        Decimal("50"), observed, observed, "verified", Decimal("80"), [],
        {"data_origin": "live_poll"}, "Rules agree", {"period": True}, "USD",
        "active", observed,
    )
    short_leg = [
        (
            "Prediction Venue", "Yes", Decimal("0.55"), Decimal("50"), Decimal("27.50"),
            Decimal("0.25"), observed, Decimal("50"), False, "binary", "Yes contract",
            "https://example.test/yes", "sell_yes",
        ),
    ]
    cursor = OpportunityDetailCursor([opportunity, short_leg])
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))

    assert repository.get_opportunity(str(opportunity[0])) is None
    assert len(cursor.statements) == 2


def test_live_scanner_filters_accept_display_names_and_sort_execution_risk_by_age() -> None:
    cursor = FakeCursor([])
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))
    repository.list_opportunities(
        OpportunityFilters(
            sport="Football",
            league="NFL",
            provider="The Odds API",
            sort="execution_risk",
            descending=False,
        )
    )

    assert "lower(s.slug) = lower(%s) or lower(s.name) = lower(%s)" in cursor.sql
    assert "lower(l.slug) = lower(%s) or lower(l.name) = lower(%s)" in cursor.sql
    assert "unnest(markets.provider_slugs || markets.provider_names)" in cursor.sql
    assert "statement_timestamp() - latest.latest_quote_at" in cursor.sql
    assert "asc nulls last" in cursor.sql
    assert cursor.parameters[:5] == (
        "Football", "Football", "NFL", "NFL", "The Odds API",
    )


def test_verified_review_requires_complete_explicit_source_evidence() -> None:
    repository = PostgresResearchRepository(lambda: pytest.fail("database must not be touched"))
    with pytest.raises(ValueError, match="complete rule"):
        repository.decide_match(
            match_id="match", reviewer_id="reviewer", decision="verified",
            rationale="Looks similar, but evidence is absent", checks={},
            source_evidence={}, expected_version=1,
        )


class ReviewCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self._rows = [
            ("verified", 3, "left-fingerprint", "right-fingerprint", "decision-fingerprint"),
            ("review-id", datetime(2026, 10, 20, tzinfo=UTC)),
        ]
        self.rowcount = 1

    def __enter__(self) -> ReviewCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((sql, parameters))

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows.pop(0)


def test_nonverified_review_invalidates_linked_opportunities_transactionally() -> None:
    cursor = ReviewCursor()
    repository = PostgresResearchRepository(lambda: FakeConnection(cursor))

    result = repository.decide_match(
        match_id="match-id",
        reviewer_id="11111111-1111-1111-1111-111111111111",
        decision="rejected",
        rationale="Regulation-only and overtime-inclusive rules differ.",
        checks={"period": False},
        source_evidence={"left": "https://example.test/left"},
        expected_version=3,
    )

    propagation = next(
        (sql, parameters)
        for sql, parameters in cursor.statements
        if "update public.opportunities" in sql.lower()
    )
    assert "then 'invalidated'" in propagation[0]
    assert propagation[1] == (
        "rejected", "rejected", "rejected", "rejected", "rejected", "rejected",
        "match-id",
    )
    assert result["decision_version"] == 4
