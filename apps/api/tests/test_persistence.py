from __future__ import annotations

from datetime import UTC, datetime

import pytest

from edgeos.credentials import CredentialCipher
from edgeos.persistence import (
    InMemoryProviderConnectionRepository,
    ProviderConnectionInput,
    ProviderConnectionService,
    ProviderTestResult,
)


def _service() -> ProviderConnectionService:
    return ProviderConnectionService(
        InMemoryProviderConnectionRepository(),
        CredentialCipher(bytes(range(32)), key_version="test-v1"),
    )


def _request(key: str = "key-one") -> ProviderConnectionInput:
    return ProviderConnectionInput(
        provider_code="the_odds_api",
        credentials={"api_key": key},
        sports=("basketball_nba",),
        regions=("us",),
        markets=("h2h",),
        poll_interval_seconds=60,
        request_budget_per_hour=30,
    )


def test_provider_connection_safe_dto_and_replace() -> None:
    service = _service()
    first = service.save_or_replace("user-a", _request())
    assert first.credential_mask == {"api_key": "••••-one"}
    assert not hasattr(first, "credentials")
    assert service.get_credentials("user-a", "the_odds_api") == {"api_key": "key-one"}

    second = service.save_or_replace("user-a", _request("replacement-key"))
    assert second.id == first.id
    assert service.get_credentials("user-a", "the_odds_api") == {"api_key": "replacement-key"}


def test_public_provider_configuration_requires_no_secret_envelope() -> None:
    service = _service()
    saved = service.save_or_replace(
        "user-a",
        ProviderConnectionInput(provider_code="kalshi", credentials={}, enabled=False),
    )
    assert saved.status == "disabled"
    assert saved.credential_mask == {}
    assert service.get_credentials("user-a", "kalshi") == {}


@pytest.mark.parametrize(
    ("provider", "credentials"),
    [
        ("unknown", {}),
        ("kalshi", {"api_key": "must-not-be-stored"}),
        ("the_odds_api", {"token": "wrong-field"}),
    ],
)
def test_provider_credentials_are_exactly_allowlisted(
    provider: str, credentials: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        ProviderConnectionInput(provider_code=provider, credentials=credentials)


def test_keyed_connection_can_keep_write_only_credential_but_not_create_without_one() -> None:
    service = _service()
    without_secret = ProviderConnectionInput(
        provider_code="the_odds_api",
        credentials={},
        sports=("americanfootball_nfl",),
        poll_interval_seconds=120,
    )
    with pytest.raises(ValueError, match="credential"):
        service.save_or_replace("user-a", without_secret)

    created = service.save_or_replace("user-a", _request())
    updated = service.save_or_replace("user-a", without_secret)
    assert updated.id == created.id
    assert updated.credential_mask == created.credential_mask
    assert service.get_credentials("user-a", "the_odds_api") == {"api_key": "key-one"}


def test_provider_connections_are_owner_isolated_and_delete_is_scoped() -> None:
    service = _service()
    service.save_or_replace("user-a", _request())
    assert len(service.list("user-a")) == 1
    assert service.list("user-b") == ()
    with pytest.raises(KeyError):
        service.get_credentials("user-b", "the_odds_api")
    assert service.delete("user-b", "the_odds_api") is False
    assert service.delete("user-a", "the_odds_api") is True


def test_configuration_and_test_errors_are_persisted_without_secrets() -> None:
    service = _service()
    service.save_or_replace("user-a", _request())
    disabled = service.update("user-a", "the_odds_api", enabled=False, poll_interval_seconds=120)
    assert disabled.status == "disabled"
    assert disabled.poll_interval_seconds == 120

    tested = service.record_test(
        "user-a",
        "the_odds_api",
        ProviderTestResult(
            status="invalid_credentials",
            tested_at=datetime(2026, 10, 20, tzinfo=UTC),
            error_code="401",
            error_summary="provider rejected api_key=key-one",
        ),
    )
    assert tested.last_error_summary is not None
    assert "key-one" not in tested.last_error_summary
    assert "REDACTED" in tested.last_error_summary


@pytest.mark.parametrize("seconds", [0, 4, 14])
def test_connection_rejects_polling_that_can_exhaust_upstream(seconds: int) -> None:
    with pytest.raises(ValueError):
        ProviderConnectionInput(
            provider_code="the_odds_api",
            credentials={"api_key": "secret"},
            poll_interval_seconds=seconds,
        )
