from __future__ import annotations

import hashlib
import json
from datetime import UTC
from decimal import Decimal
from typing import Any

from .domain import CanonicalEvent, CanonicalMarket, MatchDecision, MatchStatus
from .normalization import candidate_event_score, identity_key, plausible_event_candidate


MATCHING_ENGINE_VERSION = "edgeos.settlement.v3"
DEFAULT_MAXIMUM_VERIFIED_START_DELTA_SECONDS = 15 * 60
_EVENT_WINNER_MARKET_TYPES = frozenset({"moneyline", "h2h", "match winner", "match_winner"})

# A VERIFIED decision requires every field below to contain explicit evidence.
# Providers should use an explicit value such as "not_applicable" when a rule
# genuinely cannot affect settlement; None, blank, or "unknown" fails closed.
_RULE_FIELDS = (
    "period",
    "overtime_included",
    "shootout_included",
    "tie_treatment",
    "draw_treatment",
    "push_treatment",
    "void_treatment",
    "cancellation_treatment",
    "postponement_treatment",
    "retirement_treatment",
    "abandonment_treatment",
    "outcome_structure",
    "threshold",
    "line_unit",
    "resolution_source",
)


def _canonical_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _is_unknown(value: object) -> bool:
    return value is None or (isinstance(value, str) and _canonical_text(value) in {"", "unknown", "unspecified"})


def _requires_proposition_id(market: CanonicalMarket) -> bool:
    return (
        identity_key(market.market_type) not in _EVENT_WINNER_MARKET_TYPES
        or bool(
            {identity_key(outcome) for outcome in market.outcome_ids}
            & {"yes", "no", "true", "false", "long", "short"}
        )
    )


def _wire_fingerprint_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return _canonical_text(value)
    if isinstance(value, tuple):
        return [_wire_fingerprint_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire_fingerprint_value(item) for key, item in sorted(value.items())}
    return value


def material_market_fingerprint(market: CanonicalMarket) -> str:
    """Return a stable hash of everything material to an equivalence review.

    Where no shared proposition is resolved, the native market identifier is
    material evidence for binary/prop settlement identity. Normalized rule text
    is included so changes invalidate review even if structured fields are stale.
    """
    event = market.event
    payload = {
        "matching_engine_version": MATCHING_ENGINE_VERSION,
        "sport": identity_key(event.sport),
        "league": identity_key(event.league),
        "home_team": identity_key(event.home_team or ""),
        "away_team": identity_key(event.away_team or ""),
        "scheduled_start": event.scheduled_start.astimezone(UTC).isoformat(),
        "market_type": identity_key(market.market_type),
        "outcome_ids": sorted(identity_key(outcome) for outcome in market.outcome_ids),
        # Proposition IDs are opaque; case and punctuation may be meaningful.
        "proposition_id": market.proposition_id,
        "native_proposition_identity": (
            {"provider": market.provider, "provider_market_id": market.provider_market_id}
            if _requires_proposition_id(market) and _is_unknown(market.proposition_id)
            else None
        ),
        "rules": {field: _wire_fingerprint_value(getattr(market.rules, field)) for field in _RULE_FIELDS},
        "rules_text": _wire_fingerprint_value(market.rules.rules_text),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def material_fingerprints_still_match(
    left: CanonicalMarket,
    right: CanonicalMarket,
    *,
    reviewed_left_fingerprint: str,
    reviewed_right_fingerprint: str,
) -> bool:
    """Return whether a stored decision still describes the current rule pair."""
    return (
        material_market_fingerprint(left) == reviewed_left_fingerprint
        and material_market_fingerprint(right) == reviewed_right_fingerprint
    )


def _compare_event_identity(
    left: CanonicalEvent,
    right: CanonicalEvent,
    *,
    maximum_verified_start_delta_seconds: int,
) -> tuple[list[str], list[str], list[str]]:
    reasons: list[str] = []
    mismatches: list[str] = []
    unknown: list[str] = []

    if not plausible_event_candidate(left, right):
        mismatches.append("canonical events are not plausible counterparts")

    for field in ("sport", "league"):
        a = getattr(left, field)
        b = getattr(right, field)
        if _is_unknown(a) or _is_unknown(b):
            unknown.append(f"event.{field}")
        elif identity_key(a) != identity_key(b):
            mismatches.append(f"event {field} differs: {a!r} vs {b!r}")
        else:
            reasons.append(f"event {field} is explicitly compatible")

    participant_values = (left.home_team, left.away_team, right.home_team, right.away_team)
    if any(_is_unknown(value) for value in participant_values):
        unknown.append("event.participant_orientation")
    else:
        left_home = identity_key(left.home_team or "")
        left_away = identity_key(left.away_team or "")
        right_home = identity_key(right.home_team or "")
        right_away = identity_key(right.away_team or "")
        if left_home == left_away or right_home == right_away:
            mismatches.append("home and away participant identities are not distinct")
        elif left_home == right_away and left_away == right_home:
            mismatches.append("home/away participant orientation is reversed")
        elif left_home != right_home or left_away != right_away:
            # Similar titles may nominate this pair, but fuzzy identity is never
            # enough to prove actual settlement participants are identical.
            mismatches.append("participant identities are not explicitly equivalent")
        else:
            reasons.append("home and away participant orientation is compatible")

    start_delta = abs((left.scheduled_start - right.scheduled_start).total_seconds())
    if start_delta > maximum_verified_start_delta_seconds:
        mismatches.append(
            f"scheduled start differs by {int(start_delta)} seconds "
            f"(verified limit {maximum_verified_start_delta_seconds})"
        )
    else:
        reasons.append(f"scheduled start is compatible within {int(start_delta)} seconds")
    return reasons, mismatches, unknown


def verify_settlement_equivalence(
    left: CanonicalMarket,
    right: CanonicalMarket,
    *,
    maximum_verified_start_delta_seconds: int = DEFAULT_MAXIMUM_VERIFIED_START_DELTA_SECONDS,
) -> MatchDecision:
    """Make an auditable, conservative settlement-equivalence decision.

    Fuzzy similarity only nominates a pair. VERIFIED additionally requires
    explicit identity, orientation, complete outcome coverage, and every
    material settlement field to be known and compatible.
    """
    if maximum_verified_start_delta_seconds < 0:
        raise ValueError("maximum_verified_start_delta_seconds cannot be negative")

    score = Decimal(str(candidate_event_score(left.event, right.event)))
    reasons, mismatches, unknown = _compare_event_identity(
        left.event,
        right.event,
        maximum_verified_start_delta_seconds=maximum_verified_start_delta_seconds,
    )
    left_fingerprint = material_market_fingerprint(left)
    right_fingerprint = material_market_fingerprint(right)

    if _is_unknown(left.market_type) or _is_unknown(right.market_type):
        unknown.append("market_type")
    elif identity_key(left.market_type) != identity_key(right.market_type):
        mismatches.append(f"market_type differs: {left.market_type!r} vs {right.market_type!r}")
    else:
        reasons.append("market type is explicitly compatible")

    left_outcomes = [identity_key(outcome) for outcome in left.outcome_ids]
    right_outcomes = [identity_key(outcome) for outcome in right.outcome_ids]
    if (
        len(left_outcomes) < 2
        or len(right_outcomes) < 2
        or any(_is_unknown(outcome) for outcome in (*left_outcomes, *right_outcomes))
        or len(set(left_outcomes)) != len(left_outcomes)
        or len(set(right_outcomes)) != len(right_outcomes)
    ):
        mismatches.append("outcome coverage is not a complete distinct mutually exclusive set")
    elif set(left_outcomes) != set(right_outcomes):
        mismatches.append("outcome sets are not complete equivalents")
    else:
        reasons.append("complete outcome identities are compatible")

    for market, outcomes in ((left, left_outcomes), (right, right_outcomes)):
        structure = _canonical_text(market.rules.outcome_structure or "").replace("_", " ").replace("-", " ")
        expected_count = {
            "two way": 2,
            "two way winner": 2,
            "binary": 2,
            "yes no": 2,
            "three way": 3,
            "three way winner": 3,
        }.get(structure)
        if expected_count is not None and len(outcomes) != expected_count:
            mismatches.append("outcome count contradicts explicit outcome structure")
        draw_treatment = _canonical_text(market.rules.draw_treatment or "").replace("_", " ")
        if draw_treatment == "separate outcome" and not {"draw", "tie"} & set(outcomes):
            mismatches.append("separate draw settlement has no explicit draw outcome")

    same_native_market = (
        not _is_unknown(left.provider)
        and not _is_unknown(left.provider_market_id)
        and left.provider == right.provider
        and left.provider_market_id == right.provider_market_id
    )
    event_winner_market = (
        identity_key(left.market_type) in _EVENT_WINNER_MARKET_TYPES
        and identity_key(right.market_type) in _EVENT_WINNER_MARKET_TYPES
    )
    proposition_required = _requires_proposition_id(left) or _requires_proposition_id(right)
    if not _is_unknown(left.proposition_id) and not _is_unknown(right.proposition_id):
        if left.proposition_id != right.proposition_id:
            mismatches.append("proposition identities differ")
        else:
            reasons.append("proposition identity is explicitly compatible")
    elif proposition_required and not same_native_market:
        unknown.append("proposition_id")
    elif proposition_required:
        reasons.append("identical provider market establishes the proposition identity")

    for field in _RULE_FIELDS:
        a = getattr(left.rules, field)
        b = getattr(right.rules, field)
        if (
            (event_winner_market or same_native_market)
            and field in {"threshold", "line_unit"}
            and a is None and b is None
        ):
            reasons.append(
                f"{field} is not needed for an event winner or identical provider market"
            )
            continue
        if _is_unknown(a) or _is_unknown(b):
            unknown.append(field)
        elif isinstance(a, str) and isinstance(b, str):
            if _canonical_text(a) != _canonical_text(b):
                mismatches.append(f"{field} differs: {a!r} vs {b!r}")
            else:
                reasons.append(f"{field} is compatible")
        elif a != b:
            mismatches.append(f"{field} differs: {a!r} vs {b!r}")
        else:
            reasons.append(f"{field} is compatible")

    common: dict[str, Any] = {
        "candidate_score": score,
        "reasons": tuple(reasons),
        "mismatches": tuple(dict.fromkeys(mismatches)),
        "unknown_fields": tuple(dict.fromkeys(unknown)),
        "engine_version": MATCHING_ENGINE_VERSION,
        "left_material_fingerprint": left_fingerprint,
        "right_material_fingerprint": right_fingerprint,
    }
    if mismatches:
        return MatchDecision(status=MatchStatus.REJECTED, **common)
    if unknown:
        common["reasons"] = tuple(reasons) + ("material settlement evidence is unknown",)
        return MatchDecision(status=MatchStatus.REVIEW_REQUIRED, **common)
    common["reasons"] = tuple(reasons) + ("all material settlement fields are explicitly compatible",)
    return MatchDecision(status=MatchStatus.VERIFIED, **common)

