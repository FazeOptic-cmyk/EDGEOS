from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from edgeos.database import DatabaseConfigurationError, connection_factory
from edgeos.ingestion import WriteCounts, _outcome_key, canonical_capture, provider_reference_slug
from edgeos.post_ingestion import (
    CandidateEvidence,
    assess_candidate,
    normalized_ingestion_report,
)
from edgeos.providers import DemoAdapter, ProviderCode


def test_adapter_capture_is_stable_and_explicitly_not_upstream_raw() -> None:
    adapter = DemoAdapter()
    try:
        market = asyncio.run(adapter.list_markets()).items[0]
    finally:
        asyncio.run(adapter.aclose())
    first, first_hash = canonical_capture(market)
    second, second_hash = canonical_capture(market)
    assert first == second
    assert first_hash == second_hash
    payload = json.loads(first)
    assert payload["capture_stage"] == "normalized_adapter_contract"
    assert payload["record"]["data_origin"] == "demo"
    assert payload["record"]["outcomes"][0]["best_ask"] == "0.49"


def test_write_counts_add_without_mutation() -> None:
    left = WriteCounts(received=2, created=1)
    right = WriteCounts(received=3, updated=2, rejected=1)
    assert left + right == WriteCounts(received=5, created=1, updated=2, rejected=1)
    assert left == WriteCounts(received=2, created=1)


def test_binary_sides_remain_distinct_when_provider_subtitles_are_identical() -> None:
    adapter = DemoAdapter()
    try:
        original = asyncio.run(adapter.list_markets()).items[0]
    finally:
        asyncio.run(adapter.aclose())
    market = replace(original, outcomes=(
        replace(original.outcomes[0], name="Identical proposition subtitle", side="yes"),
        replace(original.outcomes[1], name="Identical proposition subtitle", side="no"),
    ))
    assert tuple(_outcome_key(market, item, index) for index, item in enumerate(market.outcomes)) == (
        "yes", "no",
    )


def test_provider_reference_slug_namespaces_the_internal_demo_provider() -> None:
    assert provider_reference_slug(ProviderCode.DEMO) == "edgeos_demo"
    assert provider_reference_slug(ProviderCode.KALSHI) == "kalshi"


def test_database_connection_fails_closed_without_url() -> None:
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        connection_factory("")


def test_database_connection_never_returns_driver_dsn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object, **__: object) -> None:
        raise RuntimeError("postgresql://secret-user:secret-password@db.example/edgeos")

    monkeypatch.setattr("edgeos.database.psycopg.connect", fail)
    with pytest.raises(ConnectionError, match="PostgreSQL connection failed") as captured:
        connection_factory("postgresql://secret-user:secret-password@db.example/edgeos")
    assert "secret-password" not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("canonical_event_equivalent", "canonical_event_not_verified"),
        ("settlement_rules_verified", "settlement_rules_not_verified"),
        ("outcome_coverage_complete", "outcome_coverage_not_verified"),
        ("provider_timestamps_known", "provider_timestamp_unknown"),
        ("quotes_fresh", "quote_stale_or_freshness_unknown"),
        ("executable_prices_known", "executable_price_unknown"),
        ("fees_verified", "fee_schedule_not_verified"),
        ("depth_known", "executable_depth_unknown"),
        ("currencies_compatible", "currency_conversion_not_modeled"),
    ],
)
def test_post_ingestion_gate_rejects_each_unknown_material_input(
    field: str, failure: str
) -> None:
    complete = {name: True for name in CandidateEvidence.__dataclass_fields__}
    complete[field] = None
    result = assess_candidate(CandidateEvidence(**complete))
    assert result.eligible_for_calculation is False
    assert result.state == "blocked"
    assert failure in result.failures


def test_post_ingestion_gate_only_marks_complete_evidence_ready_for_calculation() -> None:
    complete = {name: True for name in CandidateEvidence.__dataclass_fields__}
    result = assess_candidate(CandidateEvidence(**complete))
    assert result.eligible_for_calculation is True
    assert result.failures == ()
    # Passing this gate does not itself create or advertise an opportunity.
    report = normalized_ingestion_report()
    assert report.opportunities_created == 0
    assert report.alert_events_created == 0
    assert report.status == "blocked"
