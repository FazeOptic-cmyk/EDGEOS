from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from edgeos.auth import Principal
import edgeos.main as main_module
from edgeos.local_persistence import LocalPaperLedger, LocalSavedOpportunityStore, LocalStateDatabase, LocalUserStore
from edgeos.main import app
from edgeos.research_persistence import OpportunityFilters


def call(method: str, path: str, **kwargs) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(request())


def test_health_explicitly_reports_demo_and_no_execution() -> None:
    response = call("GET", "/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert body["real_money_execution"] is False
    assert body["mode_contract_version"] == 1
    assert body["desktop_local_storage"] == {
        "backend": "memory",
        "durable": False,
        "status": "ready",
        "schema_version": None,
    }
    assert response.headers["x-edgeos-mode"] == "demo"
    assert response.headers["x-edgeos-mode-contract"] == "1"


def test_health_reports_durable_local_storage_without_exposing_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = LocalStateDatabase(
        tmp_path / "data" / "edgeos-local.sqlite3",
        allowed_root=tmp_path,
    )
    monkeypatch.setattr(main_module, "local_state_database", state)

    response = call("GET", "/api/v1/health")

    assert response.status_code == 200
    assert response.json()["desktop_local_storage"] == {
        "backend": "sqlite",
        "durable": True,
        "status": "ready",
        "schema_version": 1,
    }
    assert str(tmp_path) not in response.text


def test_live_dashboard_uses_durable_paper_and_research_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PaperRepository:
        def portfolio(self, user_id: str) -> dict[str, object]:
            assert user_id == "live-user"
            return {
                "currency": "USD",
                "summary": {
                    "portfolio_value": Decimal("10012.50"),
                    "realized_pnl": Decimal("12.50"),
                    "unrealized_pnl": Decimal("0"),
                    "net_roi": Decimal("0.025"),
                    "active_positions": 2,
                    "settled_positions": 1,
                },
            }

    class ResearchRepository:
        def list_opportunities(
            self, filters: OpportunityFilters
        ) -> tuple[tuple[dict[str, object], ...], int]:
            return (), 3 if filters.verified_only else 7

    monkeypatch.setattr(main_module, "paper_repository", PaperRepository())
    monkeypatch.setattr(main_module, "research_repository", ResearchRepository())

    response = asyncio.run(
        main_module.dashboard(Principal(user_id="live-user", is_demo=False))
    )
    body = json.loads(response.body)

    assert body["is_demo"] is False
    assert body["currency"] == "USD"
    assert body["simulated_portfolio_value"] == "10012.50"
    assert body["opportunities_detected"] == 7
    assert body["verified_opportunities"] == 3


def test_mode_mismatch_fails_before_returning_data() -> None:
    response = call("GET", "/api/v1/opportunities", headers={"X-EDGEOS-Client-Mode": "live"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mode_mismatch"
    assert response.headers["x-edgeos-mode"] == "demo"


def test_scanner_demo_is_labeled_and_decimal_values_are_strings() -> None:
    response = call("GET", "/api/v1/opportunities")
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["is_demo"] is True
    assert row["data_origin"] == "demo"
    assert row["execution_eligible"] is False
    assert isinstance(row["gross_roi"], str)
    assert isinstance(row["available_liquidity"], str)
    assert row["phase"] == "prematch"
    assert row["live"] is False
    assert row["event_start_time"] != row["observed_at"]


def test_scanner_phase_filter_is_applied_by_the_api() -> None:
    assert call("GET", "/api/v1/opportunities", params={"phase": "prematch"}).json()["total"] == 1
    assert call("GET", "/api/v1/opportunities", params={"phase": "live"}).json()["total"] == 0


def test_demo_review_queue_is_empty_and_global_decisions_are_not_allowed() -> None:
    queue = call("GET", "/api/v1/match-reviews")
    assert queue.status_code == 200
    assert queue.json()["items"] == []
    decision = call("PATCH", "/api/v1/match-reviews/example", json={
        "decision": "rejected", "rationale": "The settlement periods do not match.",
        "checks": {"period": False}, "source_evidence": {"left": "demo://left"},
        "expected_version": 1,
    })
    assert decision.status_code == 403
    assert decision.json()["detail"]["code"] == "reviewer_permission_required"


def test_saved_opportunity_round_trip_is_server_backed() -> None:
    saved = call(
        "PUT", "/api/v1/saved-opportunities/demo-opportunity-001",
        json={"favorited": True},
    )
    assert saved.status_code == 200
    assert saved.json()["favorited"] is True
    records = call("GET", "/api/v1/saved-opportunities")
    assert records.status_code == 200
    assert any(
        item["opportunity_id"] == "demo-opportunity-001" and item["favorited"]
        for item in records.json()["items"]
    )
    assert call("DELETE", "/api/v1/saved-opportunities/demo-opportunity-001").status_code == 204


def test_private_demo_routes_never_accept_user_id_from_caller() -> None:
    response = call("GET", "/api/v1/portfolio", params={"user_id": "victim"})
    assert response.status_code == 200
    assert response.json()["is_demo"] is True


def test_demo_preferences_accept_same_validated_fields_as_live_store() -> None:
    updated = call(
        "PUT",
        "/api/v1/settings",
        json={"default_allocation": "375.5", "verified_matches_only": False},
    )
    assert updated.status_code == 200
    assert updated.json()["default_allocation"] == "375.5"
    assert updated.json()["verified_matches_only"] is False


def test_desktop_local_workspace_backup_and_transactional_restore(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = LocalStateDatabase(tmp_path / "data" / "edgeos-local.sqlite3", allowed_root=tmp_path)
    local_users = LocalUserStore(state)
    local_users.update_settings("demo-local-user", {"timezone": "America/Chicago"})
    monkeypatch.setattr(main_module, "local_state_database", state)
    monkeypatch.setattr(main_module, "ledger", LocalPaperLedger(state))
    monkeypatch.setattr(main_module, "user_store", local_users)
    monkeypatch.setattr(main_module, "saved_opportunity_store", LocalSavedOpportunityStore(state))

    exported = call("GET", "/api/v1/local-workspace/backup")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.edgeos.workspace-backup+json"
    )
    assert exported.headers["content-disposition"] == (
        'attachment; filename="edgeos-local-workspace-backup.edgeos-backup"'
    )

    main_module.user_store.update_settings(
        "demo-local-user", {"timezone": "Europe/London"}
    )
    restored = call(
        "POST",
        "/api/v1/local-workspace/restore",
        content=exported.content,
        headers={
            "Content-Type": "application/vnd.edgeos.workspace-backup+json",
            "X-EDGEOS-Confirm-Restore": "REPLACE",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["restart_required"] is False
    assert main_module.user_store.settings("demo-local-user")["timezone"] == "America/Chicago"


def test_local_workspace_restore_requires_desktop_storage_and_confirmation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert call("GET", "/api/v1/local-workspace/backup").status_code == 409
    state = LocalStateDatabase(tmp_path / "data" / "edgeos-local.sqlite3", allowed_root=tmp_path)
    monkeypatch.setattr(main_module, "local_state_database", state)
    response = call(
        "POST",
        "/api/v1/local-workspace/restore",
        content=state.export_portable(),
        headers={"Content-Type": "application/vnd.edgeos.workspace-backup+json"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "restore_confirmation_required"


def test_provider_health_is_nonprobing_by_default() -> None:
    response = call("GET", "/api/v1/providers/health")
    assert response.status_code == 200
    body = response.json()
    assert body["probed"] is False
    assert {provider["id"] for provider in body["providers"]} >= {"kalshi", "demo"}
    by_code = {item["descriptor"]["code"]: item for item in body["items"]}
    assert by_code["demo"]["health"]["state"] == "demo_only"
    assert by_code["the_odds_api"]["health"]["state"] == "unknown"
    assert "disabled in demo mode" in by_code["the_odds_api"]["health"]["message"]


def test_demo_provider_health_probe_never_contacts_external_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_probe(_self):
        raise AssertionError("demo mode must not probe an external provider")

    for adapter_type in (
        main_module.KalshiAdapter,
        main_module.PolymarketInternationalAdapter,
        main_module.PolymarketUSAdapter,
        main_module.TheOddsAPIAdapter,
    ):
        monkeypatch.setattr(adapter_type, "check_health", unexpected_probe)

    response = call("GET", "/api/v1/providers/health", params={"probe": "true"})

    assert response.status_code == 200
    by_code = {item["descriptor"]["code"]: item for item in response.json()["items"]}
    assert by_code["demo"]["health"]["state"] == "demo_only"
    for code in ("kalshi", "polymarket_international", "polymarket_us", "the_odds_api"):
        assert by_code[code]["health"]["state"] == "unknown"
        assert "disabled in demo mode" in by_code[code]["health"]["message"]


@pytest.mark.parametrize("resource", ["events", "markets"])
def test_demo_mode_rejects_external_provider_browser_before_adapter_construction(
    resource: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_adapters():
        raise AssertionError("rejected demo requests must not construct provider adapters")

    monkeypatch.setattr(main_module, "_adapters", unexpected_adapters)

    response = call("GET", f"/api/v1/providers/kalshi/{resource}")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "external_provider_disabled_in_demo_mode"


def test_live_mode_rejects_direct_provider_browser_to_protect_shared_quotas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGEOS_DEMO_MODE", "false")

    with pytest.raises(main_module.HTTPException) as raised:
        main_module._browsable_provider_code("kalshi")

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "direct_provider_browser_disabled"


def test_live_health_probe_is_rejected_before_adapter_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGEOS_DEMO_MODE", "false")

    def unexpected_adapters():
        raise AssertionError("rejected live probes must not construct provider adapters")

    monkeypatch.setattr(main_module, "_adapters", unexpected_adapters)

    response = call("GET", "/api/v1/providers/health", params={"probe": "true"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "direct_provider_probe_disabled"


def test_invalid_sort_is_rejected() -> None:
    response = call("GET", "/api/v1/opportunities", params={"sort": "__sql__"})
    assert response.status_code == 422


def test_simulator_ignores_caller_verified_claim_and_fails_closed() -> None:
    payload = {
        "total_capital": "210",
        "settlement_status": "verified",
        "expected_outcome_ids": ["home", "away"],
        "now": "2026-10-16T12:00:00Z",
        "offers": [
            {
                "offer_id": "a", "provider": "a", "outcome_id": "home",
                "price_kind": "decimal_odds", "price": "2.20",
                "quote_timestamp": "2026-10-16T12:00:00Z",
                "fee_schedule": {
                    "schedule_id": "a-fees", "provider": "a", "effective_from": "2020-01-01",
                    "source_url": "https://example.test/a-fees",
                },
                "maximum_quantity": "1000", "data_origin": "historical",
            },
            {
                "offer_id": "b", "provider": "b", "outcome_id": "away",
                "price_kind": "decimal_odds", "price": "2.00",
                "quote_timestamp": "2026-10-16T12:00:00Z",
                "fee_schedule": {
                    "schedule_id": "b-fees", "provider": "b", "effective_from": "2020-01-01",
                    "source_url": "https://example.test/b-fees",
                },
                "maximum_quantity": "1000", "data_origin": "historical",
            },
        ],
    }
    response = call("POST", "/api/v1/simulate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["execution_eligible"] is False
    assert "settlement_not_verified" in body["rejection_codes"]
    assert body["outcome_coverage_verified"] is True


def test_simulator_rejects_two_legs_of_canonical_three_way_market() -> None:
    payload = {
        "total_capital": "200",
        "settlement_status": "verified",
        "expected_outcome_ids": ["home", "draw", "away"],
        "now": "2026-10-16T12:00:00Z",
        "offers": [
            {
                "offer_id": outcome, "provider": outcome, "outcome_id": outcome,
                "price_kind": "decimal_odds", "price": "2.20",
                "quote_timestamp": "2026-10-16T12:00:00Z",
                "fee_schedule": {
                    "schedule_id": f"{outcome}-fees", "provider": outcome,
                    "effective_from": "2020-01-01", "source_url": "https://example.test/fees",
                },
                "maximum_quantity": "1000", "data_origin": "historical",
            }
            for outcome in ("home", "away")
        ],
    }
    response = call("POST", "/api/v1/simulate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["execution_eligible"] is False
    assert body["is_theoretical_arbitrage"] is False
    assert "incomplete_outcome_coverage" in body["rejection_codes"]
    draw = next(item for item in body["outcomes"] if item["outcome_id"] == "draw")
    assert draw["gross_payout"] == "0"


def test_scenario_simulator_enumerates_push_void_matrix_as_research_only() -> None:
    response = call("POST", "/api/v1/simulate/scenarios", json={
        "outcome_ids": ["home", "away", "push"],
        "legs": [
            {
                "leg_id": "home-leg", "provider": "a", "requested_quantity": "10",
                "filled_quantity": "10", "unit_cost": "1", "fees": "0",
                "payout_per_unit": {"home": "2.1", "push": "1"},
            },
            {
                "leg_id": "away-leg", "provider": "b", "requested_quantity": "10",
                "filled_quantity": "10", "unit_cost": "1", "fees": "0",
                "payout_per_unit": {"away": "2.1", "push": "1"},
            },
        ],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert body["execution_eligible"] is False
    profits = {row["outcome_id"]: row["net_profit"] for row in body["evaluation"]["outcome_results"]}
    assert profits["push"] == "0"


def test_demo_account_deletion_is_explicitly_unavailable() -> None:
    response = call("DELETE", "/api/v1/account")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "demo_account_not_deletable"


def test_scanner_to_paper_settlement_and_analytics_workflow_is_authoritative() -> None:
    create_key = f"create-{uuid4()}"
    created = call(
        "POST",
        "/api/v1/portfolio/positions",
        headers={"X-Idempotency-Key": create_key, "X-EDGEOS-Client-Mode": "demo"},
        json={
            "opportunity_id": "demo-opportunity-001",
            "total_capital": "210",
            "notes": "Known two-way reference",
            "idempotency_key": create_key,
        },
    )
    assert created.status_code == 201
    position = created.json()
    assert position["planned_allocation"] == "210"
    assert position["calculation_version"] == "edgeos.cashflow.v2"
    assert position["realized_pnl"] == "0"
    replay = call(
        "POST",
        "/api/v1/portfolio/positions",
        headers={"X-Idempotency-Key": create_key},
        json={
            "opportunity_id": "demo-opportunity-001", "total_capital": "210",
            "notes": "Known two-way reference", "idempotency_key": create_key,
        },
    )
    assert replay.json()["id"] == position["id"]

    prices = {"Harbor Hawks": "2.20", "Metro Comets": "2.00"}
    for leg in position["legs"]:
        fill_key = f"fill-{uuid4()}"
        filled = call(
            "POST",
            f"/api/v1/portfolio/positions/{position['id']}/fills",
            headers={"X-Idempotency-Key": fill_key},
            json={
                "leg_id": leg["id"], "quantity": leg["planned_quantity"],
                "price": prices[leg["outcome"]], "fee": "0",
                "idempotency_key": fill_key,
            },
        )
        assert filled.status_code == 200
    assert filled.json()["status"] == "filled"

    settle_key = f"settle-{uuid4()}"
    settled = call(
        "POST",
        f"/api/v1/portfolio/positions/{position['id']}/settle",
        headers={"X-Idempotency-Key": settle_key},
        json={"outcome_id": "Harbor Hawks", "idempotency_key": settle_key},
    )
    assert settled.status_code == 200
    assert settled.json()["status"] == "settled"
    assert settled.json()["realized_pnl"] == "10.0000"

    report = call(
        "GET", "/api/v1/analytics",
        params={"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"},
    )
    assert report.status_code == 200
    assert report.json()["summary"]["attempted_opportunities"] >= 1
    assert report.json()["summary"]["realized_pnl"] == "10.0000"


def test_watchlist_and_alert_rules_are_editable_and_deletable() -> None:
    watch_key = f"watch-{uuid4()}"
    watched = call(
        "POST", "/api/v1/watchlists", headers={"X-Idempotency-Key": watch_key},
        json={"kind": "team", "value": f"team-{uuid4()}", "label": "Research Team", "idempotency_key": watch_key},
    )
    assert watched.status_code == 201
    assert "user_id" not in watched.json()
    assert call("DELETE", f"/api/v1/watchlists/{watched.json()['id']}").status_code == 204

    alert_key = f"alert-{uuid4()}"
    alert = call(
        "POST", "/api/v1/alerts", headers={"X-Idempotency-Key": alert_key},
        json={
            "name": "Net edge threshold", "condition": "net_roi_above", "threshold": "0.02",
            "minimum_net_profit": "12.50", "minimum_net_roi": "0.015",
            "minimum_liquidity": "250", "maximum_quote_age_seconds": 45,
            "enabled": True, "cooldown_seconds": 600, "idempotency_key": alert_key,
        },
    )
    assert alert.status_code == 201
    assert alert.json()["minimum_net_profit"] == "12.50"
    assert alert.json()["minimum_net_roi"] == "0.015"
    assert alert.json()["minimum_liquidity"] == "250"
    assert alert.json()["maximum_quote_age_seconds"] == 45
    second_key = f"alert-{uuid4()}"
    different_gate = call(
        "POST", "/api/v1/alerts", headers={"X-Idempotency-Key": second_key},
        json={
            "name": "Different profit gate", "condition": "net_roi_above", "threshold": "0.02",
            "minimum_net_profit": "13.00", "minimum_net_roi": "0.015",
            "minimum_liquidity": "250", "maximum_quote_age_seconds": 45,
            "enabled": True, "cooldown_seconds": 600, "idempotency_key": second_key,
        },
    )
    assert different_gate.status_code == 201
    assert different_gate.json()["id"] != alert.json()["id"]
    changed = call("PATCH", f"/api/v1/alerts/{alert.json()['id']}", json={"threshold": "0.03", "enabled": False})
    assert changed.status_code == 200
    assert changed.json()["threshold"] == "0.03"
    assert changed.json()["enabled"] is False
    assert call("DELETE", f"/api/v1/alerts/{alert.json()['id']}").status_code == 204
    assert call("DELETE", f"/api/v1/alerts/{different_gate.json()['id']}").status_code == 204

