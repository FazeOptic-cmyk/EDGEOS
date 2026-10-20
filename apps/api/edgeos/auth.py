from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, settings


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    is_demo: bool
    access_token: str | None = None
    issued_at: int | None = None
    authenticated_at: int | None = None


_RECENT_AUTH_METHODS = frozenset(
    {"password", "otp", "oauth", "sso/saml", "totp", "webauthn"}
)
_JWKS_CACHE_MAX_ISSUERS = 8
_jwks_clients: OrderedDict[str, jwt.PyJWKClient] = OrderedDict()
_jwks_clients_lock = Lock()


def _supabase_issuer(value: str) -> str:
    """Normalize the configured project origin before using it as a cache key."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Supabase authentication URL is invalid",
        )
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Supabase authentication URL is invalid",
        ) from exc
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = netloc_host if port is None or default_port else f"{netloc_host}:{port}"
    base_path = parsed.path.rstrip("/")
    base = urlunsplit((scheme, netloc, base_path, "", ""))
    return f"{base}/auth/v1"


def _jwks_client(issuer: str) -> jwt.PyJWKClient:
    """Reuse PyJWT's five-minute JWK/key cache across authenticated requests."""
    with _jwks_clients_lock:
        existing = _jwks_clients.get(issuer)
        if existing is not None:
            _jwks_clients.move_to_end(issuer)
            return existing
        client = jwt.PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=300,
        )
        _jwks_clients[issuer] = client
        while len(_jwks_clients) > _JWKS_CACHE_MAX_ISSUERS:
            _jwks_clients.popitem(last=False)
        return client


def _clear_jwks_client_cache() -> None:
    """Test/support hook; normal key rotation is handled inside PyJWKClient."""
    with _jwks_clients_lock:
        _jwks_clients.clear()


def _authentication_timestamp(claims: dict[str, object]) -> int | None:
    """Read the signed Supabase AMR time, never the refreshable JWT iat."""
    amr = claims.get("amr")
    if not isinstance(amr, list):
        return None
    timestamps: list[int] = []
    for entry in amr:
        if not isinstance(entry, dict) or entry.get("method") not in _RECENT_AUTH_METHODS:
            continue
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            timestamps.append(timestamp)
    return max(timestamps, default=None)


def _verify_supabase_token(token: str, config: Settings) -> dict[str, object]:
    if not config.supabase_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Supabase authentication is not configured")
    issuer = _supabase_issuer(config.supabase_url)
    try:
        if config.legacy_jwt_secret:
            return jwt.decode(
                token, config.legacy_jwt_secret, algorithms=["HS256"],
                audience="authenticated", issuer=issuer,
            )
        jwks = _jwks_client(issuer)
        signing_key = jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token, signing_key.key, algorithms=["RS256", "ES256"],
            audience="authenticated", issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired access token") from exc


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    # Keep Settings out of the dependency signature. FastAPI otherwise treats
    # this dataclass as a second request body and silently changes every
    # authenticated POST contract to {"request": ..., "config": ...}.
    config = settings
    if config.demo_mode:
        # A fixed server-side identity prevents caller-supplied user IDs from becoming authorization.
        return Principal(user_id="demo-local-user", is_demo=True)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer access token required")
    # PyJWKClient performs a blocking HTTPS fetch on cache misses. Keep it off
    # the event loop; making this dependency async also avoids an unnecessary
    # AnyIO worker-thread hop for demo requests.
    claims = await asyncio.to_thread(
        _verify_supabase_token, credentials.credentials, config
    )
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token has no subject")
    issued_at_claim = claims.get("iat")
    issued_at = issued_at_claim if isinstance(issued_at_claim, int) and not isinstance(issued_at_claim, bool) else None
    return Principal(
        user_id=subject,
        is_demo=False,
        access_token=credentials.credentials,
        issued_at=issued_at,
        authenticated_at=_authentication_timestamp(claims),
    )

