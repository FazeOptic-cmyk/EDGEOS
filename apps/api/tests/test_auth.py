from __future__ import annotations

from types import SimpleNamespace

import pytest

from edgeos import auth
from edgeos.config import Settings


@pytest.fixture(autouse=True)
def clear_jwks_cache() -> None:
    auth._clear_jwks_client_cache()
    yield
    auth._clear_jwks_client_cache()


def _install_fake_jwks(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    created: list[object] = []

    class FakeJWKClient:
        def __init__(self, uri: str, *, cache_keys: bool, lifespan: int) -> None:
            self.uri = uri
            self.tokens: list[str] = []
            assert cache_keys is True
            assert lifespan == 300
            created.append(self)

        def get_signing_key_from_jwt(self, token: str) -> object:
            self.tokens.append(token)
            return SimpleNamespace(key=f"key-for-{token}")

    monkeypatch.setattr(auth.jwt, "PyJWKClient", FakeJWKClient)
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda token, key, **_kwargs: {"sub": token, "key": key},
    )
    return created


def test_jwks_client_is_reused_and_normalizes_project_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_jwks(monkeypatch)

    first = auth._verify_supabase_token(
        "token-one", Settings(supabase_url="HTTPS://Project.Supabase.CO/"),
    )
    second = auth._verify_supabase_token(
        "token-two", Settings(supabase_url="https://project.supabase.co"),
    )

    assert first["sub"] == "token-one"
    assert second["sub"] == "token-two"
    assert len(created) == 1
    assert created[0].uri == "https://project.supabase.co/auth/v1/.well-known/jwks.json"
    assert created[0].tokens == ["token-one", "token-two"]


def test_jwks_clients_are_isolated_by_issuer_and_lru_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_jwks(monkeypatch)

    for index in range(auth._JWKS_CACHE_MAX_ISSUERS + 1):
        auth._verify_supabase_token(
            f"token-{index}",
            Settings(supabase_url=f"https://project-{index}.supabase.co"),
        )

    assert len(created) == auth._JWKS_CACHE_MAX_ISSUERS + 1
    assert len(auth._jwks_clients) == auth._JWKS_CACHE_MAX_ISSUERS
    assert "https://project-0.supabase.co/auth/v1" not in auth._jwks_clients

    auth._verify_supabase_token(
        "token-return", Settings(supabase_url="https://project-0.supabase.co"),
    )
    assert len(created) == auth._JWKS_CACHE_MAX_ISSUERS + 2


@pytest.mark.parametrize(
    "value",
    ["not-a-url", "ftp://project.supabase.co", "https://user:secret@project.supabase.co", "https://project.supabase.co?x=1"],
)
def test_invalid_supabase_origins_fail_closed(value: str) -> None:
    with pytest.raises(auth.HTTPException) as raised:
        auth._verify_supabase_token("token", Settings(supabase_url=value))

    assert raised.value.status_code == 503
