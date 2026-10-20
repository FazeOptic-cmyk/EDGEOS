from __future__ import annotations

from decimal import Decimal

from .domain import MatchStatus, RankInputs, RankResult


def _clamp(value: Decimal, low: Decimal = Decimal("0"), high: Decimal = Decimal("1")) -> Decimal:
    return min(high, max(low, value))


def rank_opportunity(values: RankInputs) -> RankResult:
    rejects: list[str] = []
    if values.settlement_status is not MatchStatus.VERIFIED:
        rejects.append("settlement_not_verified")
    if values.quote_age_seconds is None:
        rejects.append("quote_age_unknown")
    elif values.quote_age_seconds < 0:
        rejects.append("quote_timestamp_in_future")
    elif values.quote_age_seconds > values.maximum_quote_age_seconds:
        rejects.append("stale_quote")
    if values.maximum_quote_timestamp_skew_seconds is not None:
        if values.quote_timestamp_skew_seconds is None:
            rejects.append("quote_timestamp_skew_unknown")
        elif values.quote_timestamp_skew_seconds < 0:
            rejects.append("quote_timestamp_skew_invalid")
        elif values.quote_timestamp_skew_seconds > values.maximum_quote_timestamp_skew_seconds:
            rejects.append("quote_timestamp_skew_exceeded")
    if values.executable_liquidity < values.minimum_liquidity:
        rejects.append("insufficient_depth")
    if not values.fee_schedule_known:
        rejects.append("fee_schedule_unknown")
    if values.conservative_profit <= 0 or values.net_roi <= 0:
        rejects.append("non_positive_conservative_return")

    if rejects:
        return RankResult(eligible=False, score=None, hard_rejections=tuple(rejects), components={})

    roi = _clamp(values.net_roi / Decimal("0.05"))
    profit = _clamp(values.conservative_profit / Decimal("100"))
    liquidity = _clamp(values.executable_liquidity / Decimal("1000"))
    freshness = _clamp(Decimal("1") - values.quote_age_seconds / values.maximum_quote_age_seconds)  # type: ignore[operator]
    timestamp_alignment = Decimal("1")
    if values.maximum_quote_timestamp_skew_seconds is not None:
        timestamp_alignment = _clamp(
            Decimal("1")
            - values.quote_timestamp_skew_seconds / values.maximum_quote_timestamp_skew_seconds  # type: ignore[operator]
        )
    slippage = _clamp(Decimal("1") - values.expected_slippage_bps / Decimal("100"))
    fill = _clamp(Decimal("1") - values.partial_fill_probability)
    stability = values.stability if values.stability is not None else Decimal("0.5")
    components = {
        "net_roi": roi,
        "conservative_profit": profit,
        "liquidity": liquidity,
        "freshness": freshness,
        "timestamp_alignment": timestamp_alignment,
        "slippage": slippage,
        "fill_reliability": fill,
        "stability": stability,
        "settlement_confidence": Decimal("1"),
    }
    weights = {
        "net_roi": Decimal("0.20"),
        "conservative_profit": Decimal("0.15"),
        "liquidity": Decimal("0.15"),
        "freshness": Decimal("0.10"),
        "timestamp_alignment": Decimal("0.05"),
        "slippage": Decimal("0.10"),
        "fill_reliability": Decimal("0.10"),
        "stability": Decimal("0.05"),
        "settlement_confidence": Decimal("0.10"),
    }
    score = sum((components[key] * weight for key, weight in weights.items()), Decimal("0")) * Decimal("100")
    return RankResult(eligible=True, score=score.quantize(Decimal("0.01")), hard_rejections=(), components=components)

