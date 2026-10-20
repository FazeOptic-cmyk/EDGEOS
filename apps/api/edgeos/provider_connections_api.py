"""Authenticated provider-connection management API.

Only safe operational metadata crosses this router. Credential values are
accepted as write-only inputs, encrypted by :mod:`edgeos.credentials`, and are
decrypted only long enough to construct one read-only health-check adapter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import Principal, get_principal
from .config import settings
from .credentials import CredentialCipher, CredentialConfigurationError
from .database import connection_factory
from .persistence import (
    PROVIDER_CREDENTIAL_FIELDS,
    PostgresProviderConnectionRepository,
    ProviderConnectionInput,
    ProviderConnectionSafe,
    ProviderConnectionService,
    ProviderTestResult,
)
from .providers import (
    KalshiAdapter,
    MarketDataAdapter,
    PolymarketInternationalAdapter,
    PolymarketUSAdapter,
    ProviderHealth,
    ProviderState,
    RetryPolicy,
    TheOddsAPIAdapter,
)


ProviderCodeValue = Literal[
    "the_odds_api", "kalshi", "polymarket_international", "polymarket_us"
]
ConnectionStatusValue = Literal[
    "not_configured",
    "ready",
    "connected",
    "disabled",
    "testing",
    "missing_credentials",
    "invalid_credentials",
    "insufficient_plan",
    "rate_limited",
    "provider_unavailable",
    "storage_unavailable",
    "error",
]

_ADAPTER_TYPES: Mapping[str, type[MarketDataAdapter]] = {
    "kalshi": KalshiAdapter,
    "polymarket_international": PolymarketInternationalAdapter,
    "polymarket_us": PolymarketUSAdapter,
    "the_odds_api": TheOddsAPIAdapter,
}
_DESCRIPTORS = {code: adapter.descriptor for code, adapter in _ADAPTER_TYPES.items()}
_TOKEN_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$"


class SaveProviderConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_code: ProviderCodeValue
    credentials: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    sports: list[str] = Field(default_factory=list, max_length=40)
    regions: list[str] = Field(default_factory=list, max_length=20)
    markets: list[str] = Field(default_factory=list, max_length=40)
    poll_interval_seconds: int = Field(ge=15, le=86_400)
    request_budget_per_hour: int = Field(ge=1, le=100_000)

    @field_validator("sports", "regions", "markets")
    @classmethod
    def validate_identifiers(cls, values: list[str]) -> list[str]:
        import re

        if any(len(value) > 80 or re.fullmatch(_TOKEN_PATTERN, value) is None for value in values):
            raise ValueError("selection values must be provider identifiers")
        return values

    @model_validator(mode="after")
    def validate_credentials(self) -> SaveProviderConnectionRequest:
        expected = PROVIDER_CREDENTIAL_FIELDS[self.provider_code]
        actual = frozenset(self.credentials)
        if actual and actual != expected:
            raise ValueError("credential fields do not match this provider")
        if any(len(value) < 8 or len(value) > 2_048 for value in self.credentials.values()):
            raise ValueError("credential values must be between 8 and 2048 characters")
        return self


class UpdateProviderConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool | None = None
    sports: list[str] | None = Field(default=None, max_length=40)
    regions: list[str] | None = Field(default=None, max_length=20)
    markets: list[str] | None = Field(default=None, max_length=40)
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=86_400)
    request_budget_per_hour: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("sports", "regions", "markets")
    @classmethod
    def validate_identifiers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        import re

        if any(len(value) > 80 or re.fullmatch(_TOKEN_PATTERN, value) is None for value in values):
            raise ValueError("selection values must be provider identifiers")
        return values

    @model_validator(mode="after")
    def require_change(self) -> UpdateProviderConnectionRequest:
        if not self.model_fields_set:
            raise ValueError("at least one setting is required")
        return self


class ProviderConnectionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_code: ProviderCodeValue
    display_name: str
    product_scope: str
    enabled: bool
    credential_configured: bool
    credential_mask: str | None
    status: ConnectionStatusValue
    capabilities: list[str]
    coverage: list[str]
    sports: list[str]
    regions: list[str]
    markets: list[str]
    poll_interval_seconds: int
    request_budget_per_hour: int
    estimated_requests_per_hour: int | None
    quota_limit: int | None
    quota_remaining: int | None
    quota_resets_at: datetime | None
    last_tested_at: datetime | None
    last_success_at: datetime | None
    last_market_update_at: datetime | None
    error_code: str | None


class ProviderConnectionListResponse(BaseModel):
    items: list[ProviderConnectionDTO]


class ProviderConnectionResponse(BaseModel):
    item: ProviderConnectionDTO


async def get_live_provider_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Reject demo mode before resolving any credential-storage dependency."""
    return _require_live(principal)


async def get_provider_connection_service(
    _principal: Annotated[Principal, Depends(get_live_provider_principal)],
) -> ProviderConnectionService:
    """Build the server-only durable service or fail closed."""
    if not settings.database_url or not settings.credential_master_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_storage_unavailable",
                "message": "Encrypted provider credential storage is not configured.",
            },
        )
    try:
        cipher = CredentialCipher.from_base64(
            settings.credential_master_key,
            key_version=settings.credential_key_version,
        )
    except CredentialConfigurationError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_storage_unavailable",
                "message": "Encrypted provider credential storage is misconfigured.",
            },
        ) from exc
    repository = PostgresProviderConnectionRepository(
        lambda: connection_factory(application_name="edgeos-api-provider-connections")
    )
    return ProviderConnectionService(repository, cipher)


async def get_adapter_factory() -> Callable[[str, Mapping[str, str]], MarketDataAdapter]:
    def build(provider_code: str, credentials: Mapping[str, str]) -> MarketDataAdapter:
        if provider_code == "the_odds_api":
            return TheOddsAPIAdapter(
                api_key=credentials.get("api_key"),
                retry_policy=RetryPolicy(max_attempts=1),
                timeout_seconds=8.0,
            )
        try:
            adapter_type = _ADAPTER_TYPES[provider_code]
        except KeyError as exc:  # Defensive; route validation rejects this first.
            raise ValueError("provider is not configurable") from exc
        return adapter_type(
            retry_policy=RetryPolicy(max_attempts=1),
            timeout_seconds=8.0,
        )

    return build


def _require_live(principal: Principal) -> Principal:
    if principal.is_demo:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "live_mode_required",
                "message": "Provider connections are disabled in demo mode.",
            },
        )
    return principal


def _coverage(row: ProviderConnectionSafe) -> list[str]:
    value = row.coverage.get("items")
    if isinstance(value, list):
        return [item[:120] for item in value if isinstance(item, str) and item][:64]
    return list(dict.fromkeys((*row.sports, *row.regions, *row.markets)))[:64]


def _integer(mapping: Mapping[str, object], key: str) -> int | None:
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _datetime(mapping: Mapping[str, object], key: str) -> datetime | None:
    value = mapping.get(key)
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def _dto(row: ProviderConnectionSafe) -> ProviderConnectionDTO:
    descriptor = _DESCRIPTORS[row.provider_code]
    credential_mask = row.credential_mask.get("api_key")
    if not isinstance(credential_mask, str):
        credential_mask = None
    capabilities = sorted(capability.value for capability in descriptor.capabilities)
    budget = row.request_budget_per_hour or 1
    polls = ceil(3600 / row.poll_interval_seconds)
    multiplier = max(1, len(row.sports)) if row.provider_code == "the_odds_api" else 1
    # The current polling cycle requests both an event page and a market page
    # for each filter set. Keep this estimate aligned with worker.py so the UI
    # cannot understate expected quota consumption by half.
    estimated = min(100_000, polls * multiplier * 2)
    stored_status: ConnectionStatusValue
    if row.status == "requires_configuration":
        stored_status = "ready" if not PROVIDER_CREDENTIAL_FIELDS[row.provider_code] or credential_mask else "missing_credentials"
    else:
        stored_status = cast(ConnectionStatusValue, row.status)
    return ProviderConnectionDTO(
        provider_code=row.provider_code,
        display_name=row.display_name,
        product_scope=descriptor.product_scope,
        enabled=row.enabled,
        credential_configured=bool(credential_mask),
        credential_mask=credential_mask,
        status="disabled" if not row.enabled else stored_status,
        capabilities=capabilities,
        coverage=_coverage(row),
        sports=list(row.sports),
        regions=list(row.regions),
        markets=list(row.markets),
        poll_interval_seconds=row.poll_interval_seconds,
        request_budget_per_hour=budget,
        estimated_requests_per_hour=estimated,
        quota_limit=_integer(row.quota, "limit"),
        quota_remaining=_integer(row.quota, "remaining"),
        quota_resets_at=_datetime(row.quota, "reset_at"),
        last_tested_at=row.last_tested_at,
        last_success_at=row.last_successful_request_at,
        last_market_update_at=row.last_market_update_at,
        error_code=row.last_error_code,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "message": "Provider connection was not found."},
    )


def _test_result(health: ProviderHealth) -> ProviderTestResult:
    http_status = health.last_http_status
    if health.state is ProviderState.CONNECTED:
        connection_status = "connected"
        error_code = None
    elif http_status == 429:
        connection_status, error_code = "rate_limited", "rate_limited"
    elif http_status in {401, 403} or health.state is ProviderState.REQUIRES_API_KEY:
        connection_status, error_code = "invalid_credentials", "invalid_credentials"
    elif http_status == 402:
        connection_status, error_code = "insufficient_plan", "insufficient_plan"
    elif health.state in {ProviderState.DISCONNECTED, ProviderState.DELAYED}:
        connection_status, error_code = "provider_unavailable", "provider_unavailable"
    else:
        connection_status, error_code = "error", "provider_test_failed"
    quota = {}
    if health.rate_limit is not None:
        quota = {
            "limit": health.rate_limit.limit,
            "remaining": health.rate_limit.remaining,
            "reset_at": health.rate_limit.reset_at.isoformat() if health.rate_limit.reset_at else None,
        }
    return ProviderTestResult(
        status=connection_status,
        # Cooldown enforcement is based on our server clock, not a provider
        # payload or adapter timestamp that could be stale or malformed.
        tested_at=datetime.now(UTC),
        successful_request_at=health.last_success_at if connection_status == "connected" else None,
        quota=quota,
        error_code=error_code,
        error_summary=None if connection_status == "connected" else (health.message or health.last_error),
    )


router = APIRouter(prefix="/api/v1/provider-connections", tags=["provider-connections"])


@router.get("")
async def list_provider_connections(
    principal: Annotated[Principal, Depends(get_live_provider_principal)],
    service: Annotated[ProviderConnectionService, Depends(get_provider_connection_service)],
) -> ProviderConnectionListResponse:
    return ProviderConnectionListResponse(items=[_dto(row) for row in service.list(principal.user_id)])


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_provider_connection(
    request: SaveProviderConnectionRequest,
    principal: Annotated[Principal, Depends(get_live_provider_principal)],
    service: Annotated[ProviderConnectionService, Depends(get_provider_connection_service)],
) -> ProviderConnectionResponse:
    try:
        row = service.save_or_replace(
            principal.user_id,
            ProviderConnectionInput(
                provider_code=request.provider_code,
                credentials=request.credentials,
                enabled=request.enabled,
                sports=tuple(request.sports),
                regions=tuple(request.regions),
                markets=tuple(request.markets),
                poll_interval_seconds=request.poll_interval_seconds,
                request_budget_per_hour=request.request_budget_per_hour,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    except KeyError as exc:
        raise _not_found() from exc
    return ProviderConnectionResponse(item=_dto(row))


@router.patch("/{provider_code}")
async def update_provider_connection(
    provider_code: ProviderCodeValue,
    request: UpdateProviderConnectionRequest,
    principal: Annotated[Principal, Depends(get_live_provider_principal)],
    service: Annotated[ProviderConnectionService, Depends(get_provider_connection_service)],
) -> ProviderConnectionResponse:
    changes = request.model_dump(exclude_unset=True)
    for field in ("sports", "regions", "markets"):
        if field in changes:
            changes[field] = tuple(changes[field])
    try:
        row = service.update(principal.user_id, provider_code, **changes)
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "invalid_request", "message": str(exc)}) from exc
    return ProviderConnectionResponse(item=_dto(row))


@router.delete("/{provider_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_connection(
    provider_code: ProviderCodeValue,
    principal: Annotated[Principal, Depends(get_live_provider_principal)],
    service: Annotated[ProviderConnectionService, Depends(get_provider_connection_service)],
) -> Response:
    if not service.delete(principal.user_id, provider_code):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_code}/test")
async def test_provider_connection(
    provider_code: ProviderCodeValue,
    principal: Annotated[Principal, Depends(get_live_provider_principal)],
    service: Annotated[ProviderConnectionService, Depends(get_provider_connection_service)],
    adapter_factory: Annotated[
        Callable[[str, Mapping[str, str]], MarketDataAdapter], Depends(get_adapter_factory)
    ],
) -> ProviderConnectionResponse:
    row = next((item for item in service.list(principal.user_id) if item.provider_code == provider_code), None)
    if row is None:
        raise _not_found()
    if not row.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "provider_connection_disabled",
                "message": "Enable this provider connection before testing it.",
            },
        )
    now = datetime.now(UTC)
    # Health tests consume the same provider allowance as ingestion. Space
    # them according to the configured hourly ceiling, with a hard 15-second
    # floor, so repeated button/API calls cannot exhaust a free plan.
    budget = row.request_budget_per_hour or 1
    cooldown_seconds = max(15, ceil(3600 / budget))
    if row.last_tested_at is not None:
        elapsed = (now - row.last_tested_at.astimezone(UTC)).total_seconds()
        if elapsed < cooldown_seconds:
            retry_after = max(1, ceil(cooldown_seconds - elapsed))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "provider_test_cooldown",
                    "message": "Wait before running another provider health test.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
    try:
        credentials = service.get_credentials(principal.user_id, provider_code)
        adapter = adapter_factory(provider_code, credentials)
        async with adapter:
            async with asyncio.timeout(10):
                health = await adapter.check_health()
    except KeyError as exc:
        raise _not_found() from exc
    except Exception:
        # Credential/decryption/provider details remain server-side; the stored
        # code is deliberately generic and no exception text crosses the API.
        result = ProviderTestResult(
            status="provider_unavailable",
            tested_at=datetime.now(UTC),
            error_code="provider_unavailable",
            error_summary="Provider health check did not complete.",
        )
    else:
        result = _test_result(health)
    try:
        updated = service.record_test(principal.user_id, provider_code, result)
    except KeyError as exc:
        raise _not_found() from exc
    return ProviderConnectionResponse(item=_dto(updated))
