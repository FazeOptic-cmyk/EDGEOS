from decimal import Decimal

from edgeos.domain import MatchStatus, RankInputs
from edgeos.ranking import rank_opportunity


def values(**updates):
    base = {
        "conservative_profit": Decimal("12"), "net_roi": Decimal("0.03"),
        "executable_liquidity": Decimal("500"), "quote_age_seconds": Decimal("2"),
        "settlement_status": MatchStatus.VERIFIED, "expected_slippage_bps": Decimal("5"),
        "partial_fill_probability": Decimal("0.1"), "stability": Decimal("0.8"),
    }
    base.update(updates)
    return RankInputs(**base)


def test_ranking_is_transparent_and_deterministic() -> None:
    result = rank_opportunity(values())
    assert result.eligible
    assert result.score is not None
    assert set(result.components) >= {"net_roi", "liquidity", "freshness", "settlement_confidence"}


def test_hard_gates_prevent_attractive_but_invalid_rank() -> None:
    result = rank_opportunity(values(
        conservative_profit=Decimal("1000"), net_roi=Decimal("1"),
        settlement_status=MatchStatus.REVIEW_REQUIRED, quote_age_seconds=Decimal("120"),
        fee_schedule_known=False,
    ))
    assert not result.eligible
    assert result.score is None
    assert set(result.hard_rejections) >= {"settlement_not_verified", "stale_quote", "fee_schedule_unknown"}


def test_future_quote_timestamp_is_hard_rejected() -> None:
    result = rank_opportunity(values(quote_age_seconds=Decimal("-0.01")))
    assert not result.eligible
    assert "quote_timestamp_in_future" in result.hard_rejections


def test_cross_provider_timestamp_skew_is_a_transparent_hard_gate() -> None:
    result = rank_opportunity(values(
        quote_timestamp_skew_seconds=Decimal("7"),
        maximum_quote_timestamp_skew_seconds=Decimal("5"),
    ))
    assert not result.eligible
    assert "quote_timestamp_skew_exceeded" in result.hard_rejections

    aligned = rank_opportunity(values(
        quote_timestamp_skew_seconds=Decimal("1"),
        maximum_quote_timestamp_skew_seconds=Decimal("5"),
    ))
    assert aligned.eligible
    assert aligned.components["timestamp_alignment"] == Decimal("0.8")


def test_required_timestamp_skew_evidence_fails_closed_when_missing() -> None:
    result = rank_opportunity(values(maximum_quote_timestamp_skew_seconds=Decimal("5")))
    assert not result.eligible
    assert "quote_timestamp_skew_unknown" in result.hard_rejections

