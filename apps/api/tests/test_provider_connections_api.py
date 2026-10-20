from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI

from edgeos.auth import Principal, get_principal
from edgeos.credentials import CredentialCipher
from edgeos.persistence import InMemoryProviderConnectionRepository, ProviderConnectionService
from edgeos.provider_connections_api import (
    get_adapter_factory,
    get_provider_connection_service,
    router,
)
from edgeos.providers import MarketDataAdapter, ProviderCode, ProviderHealth, ProviderState


class FakeAdapter(MarketDataAdapter):
    descriptor = object()  # type: ignore[assignment]

    async def list_events(self, query: Any = None) -> Any:
        raise AssertionError("health tests must not fetch events")

    async def list_markets(self, query: Any = None) -> Any:
        raise AssertionError("health tests must not fetch markets")

    async def get_order_book(self, query: Any) -> Any:
        raise AssertionError("health tests must not fetch a book")

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=ProviderCode.THE_ODDS_API,
            state=ProviderState.DISCONNECTED,
            checked_at=datetime(2026, 10, 20, tzinfo=UTC),
            last_success_at=None,
            last_error_at=datetime(2026, 10, 20, tzinfo=UTC),
            last_error="apiKey=abcdefghSECRET",
            last_http_status=503,
            last_latency_ms=12.0,
            requests_attempted=1,
            errors=1,
            consecutive_failures=1,
            rate_limit_events=0,
            rate_limit=None,
        )


def build_app(*, demo: bool = False) -> tuple[FastAPI, ProviderConnectionService]:
    repository = InMemoryProviderConnectionRepository()
    service = ProviderConnectionService(repository, CredentialCipher(b"k" * 32))
    app = FastAPI()
    app.include_router(router)
    async def principal() -> Principal:
        return Principal("user-a", demo)

    async def provider_service() -> ProviderConnectionService:
        return service

    app.dependency_overrides[get_principal] = principal
    app.dependency_overrides[get_provider_connection_service] = provider_service
    return app, service


def call(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(request())


def odds_payload() -> dict[str, object]:
    return {
        "provider_code": "the_odds_api",
        "credentials": {"api_key": "abcdefghSECRET"},
        "enabled": True,
        "sports": ["basketball_nba", "americanfootball_nfl"],
        "regions": ["us"],
        "markets": ["h2h"],
        "poll_interval_seconds": 60,
        "request_budget_per_hour": 120,
    }


def test_crud_returns_frontend_safe_shape_without_credentials() -> None:
    app, _ = build_app()

    created = call(app, "POST", "/api/v1/provider-connections", json=odds_payload())
    assert created.status_code == 201
    item = created.json()["item"]
    assert item["credential_configured"] is True
    assert item["credential_mask"] == "••••CRET"
    assert item["status"] == "missing_credentials" or item["status"] == "ready"
    assert "abcdefghSECRET" not in created.text
    assert "credentials" not in item

    listed = call(app, "GET", "/api/v1/provider-connections")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["estimated_requests_per_hour"] == 240

    updated = call(
        app,
        "PATCH",
        "/api/v1/provider-connections/the_odds_api",
        json={"enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["status"] == "disabled"
    disabled_test = call(
        app, "POST", "/api/v1/provider-connections/the_odds_api/test"
    )
    assert disabled_test.status_code == 409
    assert disabled_test.json()["detail"]["code"] == "provider_connection_disabled"

    deleted = call(app, "DELETE", "/api/v1/provider-connections/the_odds_api")
    assert deleted.status_code == 204
    assert call(app, "GET", "/api/v1/provider-connections").json() == {"items": []}


def test_public_provider_rejects_credentials_and_requires_no_secret() -> None:
    app, _ = build_app()
    payload = odds_payload() | {
        "provider_code": "kalshi",
        "credentials": {"api_key": "abcdefgh"},
    }
    assert call(app, "POST", "/api/v1/provider-connections", json=payload).status_code == 422
    payload["credentials"] = {}
    response = call(app, "POST", "/api/v1/provider-connections", json=payload)
    assert response.status_code == 201
    assert response.json()["item"]["status"] == "ready"
    assert response.json()["item"]["credential_configured"] is False


def test_demo_mode_never_reads_or_saves_connections() -> None:
    app, _ = build_app(demo=True)
    response = call(app, "POST", "/api/v1/provider-connections", json=odds_payload())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "live_mode_required"


def test_health_check_receives_decrypted_key_but_redacts_persisted_failure() -> None:
    app, service = build_app()
    assert call(app, "POST", "/api/v1/provider-connections", json=odds_payload()).status_code == 201
    seen: dict[str, str] = {}

    def factory(_provider: str, credentials: dict[str, str]) -> MarketDataAdapter:
        seen.update(credentials)
        return FakeAdapter()

    async def adapter_factory() -> Any:
        return factory

    app.dependency_overrides[get_adapter_factory] = adapter_factory
    response = call(app, "POST", "/api/v1/provider-connections/the_odds_api/test")
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "provider_unavailable"
    assert "SECRET" not in response.text
    assert seen == {"api_key": "abcdefghSECRET"}
    stored = service.list("user-a")[0]
    assert stored.last_error_summary == "apiKey=[REDACTED]"

    repeated = call(app, "POST", "/api/v1/provider-connections/the_odds_api/test")
    assert repeated.status_code == 429
    assert repeated.json()["detail"]["code"] == "provider_test_cooldown"
    assert int(repeated.headers["retry-after"]) >= 1


def test_unknown_connection_and_empty_patch_are_rejected() -> None:
    app, _ = build_app()
    assert call(app, "PATCH", "/api/v1/provider-connections/kalshi", json={}).status_code == 422
    assert call(app, "DELETE", "/api/v1/provider-connections/kalshi").status_code == 404
