from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException

from edgeos.auth import Principal, _authentication_timestamp
from edgeos.main import _require_recent_auth, delete_supabase_account


def test_account_deletion_revokes_sessions_then_deletes_only_principal() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await delete_supabase_account(
                supabase_url="https://project.supabase.co",
                secret_key="server-secret",
                user_id="authenticated-user",
                access_token="user-access-token",
                client=client,
            )

    asyncio.run(scenario())
    assert [request.method for request in requests] == ["POST", "DELETE"]
    assert requests[0].url.path == "/auth/v1/logout"
    assert requests[0].headers["authorization"] == "Bearer user-access-token"
    assert requests[1].url.path == "/auth/v1/admin/users/authenticated-user"
    assert "other-user" not in str(requests[1].url)


def test_account_deletion_requires_a_recently_issued_token() -> None:
    now = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    current = int(now.timestamp())

    _require_recent_auth(
        Principal(user_id="user", is_demo=False, authenticated_at=current - 899),
        now=now,
    )

    with pytest.raises(HTTPException) as stale:
        _require_recent_auth(
            Principal(user_id="user", is_demo=False, authenticated_at=current - 901),
            now=now,
        )
    assert stale.value.status_code == 403
    assert stale.value.detail["code"] == "recent_authentication_required"

    with pytest.raises(HTTPException):
        _require_recent_auth(Principal(user_id="user", is_demo=False), now=now)

    with pytest.raises(HTTPException):
        _require_recent_auth(
            Principal(user_id="user", is_demo=False, authenticated_at=current + 61),
            now=now,
        )


def test_recent_authentication_uses_amr_not_refreshable_token_iat() -> None:
    assert _authentication_timestamp({"iat": 999, "amr": []}) is None
    assert _authentication_timestamp({
        "iat": 999,
        "amr": [
            {"method": "password", "timestamp": 100},
            {"method": "oauth", "timestamp": 200},
            {"method": "refresh_token", "timestamp": 999},
        ],
    }) == 200
