from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from edgeos.domain import CanonicalEvent, CanonicalMarket, MatchStatus, SettlementRules
from edgeos.matching import (
    material_fingerprints_still_match,
    material_market_fingerprint,
    verify_settlement_equivalence,
)
from edgeos.normalization import candidate_event_score, identity_key, normalize_name


def event(
    event_id: str,
    home: str | None = "New York Rangers",
    away: str | None = "Boston Bruins",
    *,
    start_delta: timedelta = timedelta(0),
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id, sport="ice hockey", league="NHL", home_team=home, away_team=away,
        scheduled_start=datetime(2026, 10, 18, 0, 0, tzinfo=UTC) + start_delta,
    )


def rules(**updates) -> SettlementRules:
    values = {
        "period": "full game", "overtime_included": True, "shootout_included": True,
        "tie_treatment": "no tie", "draw_treatment": "not_applicable",
        "push_treatment": "refund", "void_treatment": "refund",
        "cancellation_treatment": "refund", "postponement_treatment": "reschedule",
        "retirement_treatment": "not_applicable", "abandonment_treatment": "official_result",
        "outcome_structure": "two way winner", "threshold": Decimal("0"),
        "line_unit": "goals", "resolution_source": "official NHL result",
        "rules_text": "Full game including overtime and shootout.",
    }
    values.update(updates)
    return SettlementRules(**values)


def market(
    market_id: str,
    settlement: SettlementRules,
    *,
    canonical_event: CanonicalEvent | None = None,
    outcomes: tuple[str, ...] = ("Rangers", "Bruins"),
) -> CanonicalMarket:
    return CanonicalMarket(
        market_id=market_id, event=canonical_event or event(market_id), provider=market_id,
        provider_market_id=market_id, market_type="moneyline", outcome_ids=outcomes,
        rules=settlement,
    )


def test_normalization_only_generates_candidates() -> None:
    assert normalize_name("N.Y. Rangers FC") == "n y rangers"
    assert candidate_event_score(event("a"), event("b")) > 0.95


def test_explicitly_compatible_rules_are_verified() -> None:
    decision = verify_settlement_equivalence(market("a", rules()), market("b", rules()))
    assert decision.status is MatchStatus.VERIFIED
    assert not decision.mismatches
    assert not decision.unknown_fields


def test_unknown_rules_require_review_never_auto_verify() -> None:
    decision = verify_settlement_equivalence(
        market("a", rules()), market("b", rules(cancellation_treatment=None))
    )
    assert decision.status is MatchStatus.REVIEW_REQUIRED
    assert "cancellation_treatment" in decision.unknown_fields


def test_overtime_and_period_mismatch_is_rejected() -> None:
    decision = verify_settlement_equivalence(
        market("a", rules()),
        market("b", rules(period="regulation", overtime_included=False, tie_treatment="three way tie")),
    )
    assert decision.status is MatchStatus.REJECTED
    assert any("overtime" in reason for reason in decision.mismatches)


def test_reversed_participants_are_candidate_only_and_rejected_for_equivalence() -> None:
    left = market("a", rules())
    reversed_event = event("b", home="Boston Bruins", away="New York Rangers")
    right = market("b", rules(), canonical_event=reversed_event)

    assert candidate_event_score(left.event, right.event) > 0.8
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REJECTED
    assert "home/away participant orientation is reversed" in decision.mismatches


def test_draw_push_void_and_line_units_must_match_explicitly() -> None:
    left = market("a", rules(draw_treatment="separate_outcome", threshold=Decimal("2.5")))
    right = market(
        "b",
        rules(
            draw_treatment="loss",
            push_treatment="loss",
            void_treatment="regrade",
            threshold=Decimal("2.5"),
            line_unit="points",
        ),
    )
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REJECTED
    assert {reason.split()[0] for reason in decision.mismatches} >= {
        "draw_treatment",
        "push_treatment",
        "void_treatment",
        "line_unit",
    }


def test_unknown_string_evidence_requires_review() -> None:
    decision = verify_settlement_equivalence(
        market("a", rules()),
        market("b", rules(retirement_treatment="unknown")),
    )
    assert decision.status is MatchStatus.REVIEW_REQUIRED
    assert "retirement_treatment" in decision.unknown_fields


def test_start_time_outside_strict_window_is_rejected() -> None:
    decision = verify_settlement_equivalence(
        market("a", rules()),
        market("b", rules(), canonical_event=event("b", start_delta=timedelta(minutes=16))),
    )
    assert decision.status is MatchStatus.REJECTED
    assert any("scheduled start" in reason for reason in decision.mismatches)


def test_material_fingerprint_is_stable_but_invalidates_on_rule_change() -> None:
    left = market("a", rules())
    same_semantics = market("renamed-record", rules(rules_text=" Full game  including overtime and shootout. "))
    changed = market("a", rules(void_treatment="loss"))

    reviewed = material_market_fingerprint(left)
    assert material_market_fingerprint(same_semantics) == reviewed
    assert material_market_fingerprint(changed) != reviewed
    assert material_fingerprints_still_match(
        left,
        market("b", rules()),
        reviewed_left_fingerprint=reviewed,
        reviewed_right_fingerprint=material_market_fingerprint(market("b", rules())),
    )


@pytest.mark.parametrize(
    ("left_name", "right_name"),
    [
        ("New York FC", "New York CF"),
        ("LA Galaxy", "Los Angeles Galaxy"),
        ("United", "Utd"),
        ("José", "Jose"),
    ],
)
def test_candidate_aliases_and_token_deletion_never_prove_participant_identity(
    left_name: str, right_name: str
) -> None:
    assert normalize_name(left_name) == normalize_name(right_name)
    left = market("a", rules(), canonical_event=event("a", home=left_name))
    right = market("b", rules(), canonical_event=event("b", home=right_name))
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REJECTED
    assert "participant identities are not explicitly equivalent" in decision.mismatches
    assert material_market_fingerprint(left) != material_market_fingerprint(right)


def test_identity_key_preserves_names_and_only_folds_case_whitespace_and_unicode_composition() -> None:
    assert identity_key("  CAFÉ  FC ") == identity_key("cafe\u0301 fc")
    assert identity_key("東京 FC") != identity_key("大阪 FC")
    assert identity_key("N.Y.") != identity_key("NY")


def test_outcome_aliases_do_not_establish_complete_equivalent_outcomes() -> None:
    left = market("a", rules(), outcomes=("New York FC", "Boston"))
    right = market("b", rules(), outcomes=("New York CF", "Boston"))
    assert verify_settlement_equivalence(left, right).status is MatchStatus.REJECTED
    assert material_market_fingerprint(left) != material_market_fingerprint(right)


def test_cross_market_yes_no_requires_explicit_proposition_identity() -> None:
    left = market("a", rules(), outcomes=("yes", "no"))
    right = market("b", rules(), outcomes=("yes", "no"))
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REVIEW_REQUIRED
    assert "proposition_id" in decision.unknown_fields

    left = left.model_copy(update={"proposition_id": "canonical:home-team-wins"})
    right = right.model_copy(update={"proposition_id": "canonical:away-team-wins"})
    assert verify_settlement_equivalence(left, right).status is MatchStatus.REJECTED

    same_proposition = right.model_copy(update={"proposition_id": left.proposition_id})
    assert verify_settlement_equivalence(left, same_proposition).status is MatchStatus.VERIFIED
    assert material_market_fingerprint(right) != material_market_fingerprint(same_proposition)


def test_same_provider_native_market_establishes_binary_proposition_identity() -> None:
    binary = market("binary", rules(threshold=None, line_unit=None), outcomes=("yes", "no")).model_copy(
        update={"market_type": "binary contract"}
    )
    assert verify_settlement_equivalence(binary, binary).status is MatchStatus.VERIFIED
    different_native_market = binary.model_copy(update={"provider_market_id": "different-proposition"})
    assert material_market_fingerprint(binary) != material_market_fingerprint(different_native_market)


def test_generic_non_winner_markets_require_subject_and_predicate_evidence() -> None:
    left = market("a", rules(threshold=Decimal("2.5")), outcomes=("over", "under")).model_copy(
        update={"market_type": "total"}
    )
    right = market("b", rules(threshold=Decimal("2.5")), outcomes=("over", "under")).model_copy(
        update={"market_type": "total"}
    )
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REVIEW_REQUIRED
    assert "proposition_id" in decision.unknown_fields


def test_moneyline_has_no_numeric_threshold_or_line_unit() -> None:
    decision = verify_settlement_equivalence(
        market("a", rules(threshold=None, line_unit=None)),
        market("b", rules(threshold=None, line_unit=None)),
    )
    assert decision.status is MatchStatus.VERIFIED


def test_numeric_markets_still_require_explicit_threshold() -> None:
    left = market("a", rules(threshold=None)).model_copy(
        update={"market_type": "spread", "proposition_id": "canonical:home-points-spread"}
    )
    right = market("b", rules(threshold=None)).model_copy(
        update={"market_type": "spread", "proposition_id": left.proposition_id}
    )
    decision = verify_settlement_equivalence(left, right)
    assert decision.status is MatchStatus.REVIEW_REQUIRED
    assert "threshold" in decision.unknown_fields


def test_matching_fingerprint_and_engine_version_invalidate_previous_identity_rules() -> None:
    decision = verify_settlement_equivalence(market("a", rules()), market("b", rules()))
    assert decision.engine_version == "edgeos.settlement.v3"


def test_equally_incomplete_outcome_sets_cannot_verify_against_three_way_rules() -> None:
    settlement = rules(outcome_structure="three way winner", draw_treatment="separate_outcome")
    decision = verify_settlement_equivalence(market("a", settlement), market("b", settlement))
    assert decision.status is MatchStatus.REJECTED
    assert "outcome count contradicts explicit outcome structure" in decision.mismatches
    assert "separate draw settlement has no explicit draw outcome" in decision.mismatches

