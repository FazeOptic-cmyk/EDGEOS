"""Durable provider-connection contracts and PostgreSQL implementation.

The public connection row contains only safe operational metadata.  Credential
bytes are written to ``private.provider_credentials`` and are decrypted only for
the owning authenticated principal immediately before a server-side provider
request.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from .credentials import CredentialCipher, EncryptedCredential, mask_credentials, redact_secret_text


CONNECTION_STATUSES = frozenset(
    {
        "requires_configuration",
        "testing",
        "connected",
        "invalid_credentials",
        "insufficient_plan",
        "rate_limited",
        "provider_unavailable",
        "error",
        "disabled",
    }
)
PROVIDER_CREDENTIAL_FIELDS: Mapping[str, frozenset[str]] = {
    "kalshi": frozenset(),
    "polymarket_international": frozenset(),
    "polymarket_us": frozenset(),
    "the_odds_api": frozenset({"api_key"}),
}


@dataclass(frozen=True, slots=True)
class ProviderConnectionInput:
    provider_code: str
    credentials: Mapping[str, str]
    enabled: bool = True
    sports: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    markets: tuple[str, ...] = ()
    poll_interval_seconds: int = 60
    request_budget_per_hour: int | None = None

    def __post_init__(self) -> None:
        provider_code = self.provider_code.strip()
        if provider_code not in PROVIDER_CREDENTIAL_FIELDS:
            raise ValueError("provider is not configurable")
        expected_fields = PROVIDER_CREDENTIAL_FIELDS[provider_code]
        actual_fields = frozenset(self.credentials)
        # An empty credential object on an existing keyed connection means
        # "keep the write-only credential". The service rejects that shape for
        # a first-time keyed connection, while public providers legitimately
        # persist without a credential envelope.
        if actual_fields and actual_fields != expected_fields:
            expected = ", ".join(sorted(expected_fields)) or "none"
            raise ValueError(f"invalid credential fields for provider; expected: {expected}")
        if any(not value or len(value) > 16_384 for value in self.credentials.values()):
            raise ValueError("credential values must be non-empty and at most 16384 characters")
        if self.poll_interval_seconds < 15:
            raise ValueError("poll interval must be at least fifteen seconds")
        if self.request_budget_per_hour is not None and self.request_budget_per_hour < 1:
            raise ValueError("request budget must be positive")
        for values in (self.sports, self.regions, self.markets):
            if any(not value.strip() or len(value) > 100 for value in values):
                raise ValueError("provider selection values must be non-empty and at most 100 characters")


@dataclass(frozen=True, slots=True)
class ProviderConnectionSafe:
    id: str
    provider_code: str
    display_name: str
    enabled: bool
    status: str
    credential_mask: Mapping[str, str]
    sports: tuple[str, ...]
    regions: tuple[str, ...]
    markets: tuple[str, ...]
    poll_interval_seconds: int
    request_budget_per_hour: int | None
    last_tested_at: datetime | None = None
    last_successful_request_at: datetime | None = None
    last_market_update_at: datetime | None = None
    capabilities: Mapping[str, object] = field(default_factory=dict)
    coverage: Mapping[str, object] = field(default_factory=dict)
    quota: Mapping[str, object] = field(default_factory=dict)
    last_error_code: str | None = None
    last_error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CONNECTION_STATUSES:
            raise ValueError("invalid provider connection status")


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    status: str
    tested_at: datetime
    successful_request_at: datetime | None = None
    market_update_at: datetime | None = None
    capabilities: Mapping[str, object] = field(default_factory=dict)
    coverage: Mapping[str, object] = field(default_factory=dict)
    quota: Mapping[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CONNECTION_STATUSES:
            raise ValueError("invalid provider connection status")


class ProviderConnectionRepository(Protocol):
    def list_connections(self, user_id: str) -> tuple[ProviderConnectionSafe, ...]: ...

    def save_connection(
        self,
        user_id: str,
        request: ProviderConnectionInput,
        encrypted: EncryptedCredential | None,
        credential_mask: Mapping[str, str],
    ) -> ProviderConnectionSafe: ...

    def get_encrypted_credentials(self, user_id: str, provider_code: str) -> EncryptedCredential | None: ...

    def update_configuration(
        self,
        user_id: str,
        provider_code: str,
        *,
        enabled: bool | None = None,
        sports: tuple[str, ...] | None = None,
        regions: tuple[str, ...] | None = None,
        markets: tuple[str, ...] | None = None,
        poll_interval_seconds: int | None = None,
        request_budget_per_hour: int | None = None,
    ) -> ProviderConnectionSafe: ...

    def record_test_result(
        self, user_id: str, provider_code: str, result: ProviderTestResult
    ) -> ProviderConnectionSafe: ...

    def delete_connection(self, user_id: str, provider_code: str) -> bool: ...


@dataclass(slots=True)
class _MemoryRecord:
    safe: ProviderConnectionSafe
    envelope: EncryptedCredential | None


class InMemoryProviderConnectionRepository:
    """Deterministic test/demo repository with production ownership semantics."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], _MemoryRecord] = {}
        self._lock = RLock()

    def list_connections(self, user_id: str) -> tuple[ProviderConnectionSafe, ...]:
        with self._lock:
            return tuple(
                row.safe
                for (owner, _), row in sorted(self._rows.items())
                if owner == user_id
            )

    def save_connection(
        self,
        user_id: str,
        request: ProviderConnectionInput,
        encrypted: EncryptedCredential | None,
        credential_mask: Mapping[str, str],
    ) -> ProviderConnectionSafe:
        key = (user_id, request.provider_code)
        with self._lock:
            existing = self._rows.get(key)
            safe = ProviderConnectionSafe(
                id=existing.safe.id if existing else str(uuid4()),
                provider_code=request.provider_code,
                display_name=request.provider_code.replace("_", " ").title(),
                enabled=request.enabled,
                status="requires_configuration" if request.enabled else "disabled",
                credential_mask=dict(credential_mask),
                sports=request.sports,
                regions=request.regions,
                markets=request.markets,
                poll_interval_seconds=request.poll_interval_seconds,
                request_budget_per_hour=request.request_budget_per_hour,
            )
            self._rows[key] = _MemoryRecord(safe=safe, envelope=encrypted)
            return safe

    def get_encrypted_credentials(self, user_id: str, provider_code: str) -> EncryptedCredential | None:
        with self._lock:
            try:
                return self._rows[(user_id, provider_code)].envelope
            except KeyError as exc:
                raise KeyError(provider_code) from exc

    def update_configuration(
        self,
        user_id: str,
        provider_code: str,
        *,
        enabled: bool | None = None,
        sports: tuple[str, ...] | None = None,
        regions: tuple[str, ...] | None = None,
        markets: tuple[str, ...] | None = None,
        poll_interval_seconds: int | None = None,
        request_budget_per_hour: int | None = None,
    ) -> ProviderConnectionSafe:
        if poll_interval_seconds is not None and poll_interval_seconds < 15:
            raise ValueError("poll interval must be at least fifteen seconds")
        if request_budget_per_hour is not None and request_budget_per_hour < 1:
            raise ValueError("request budget must be positive")
        key = (user_id, provider_code)
        with self._lock:
            try:
                row = self._rows[key]
            except KeyError as exc:
                raise KeyError(provider_code) from exc
            next_enabled = row.safe.enabled if enabled is None else enabled
            status = row.safe.status
            if enabled is False:
                status = "disabled"
            elif enabled is True and status == "disabled":
                status = "requires_configuration"
            row.safe = replace(
                row.safe,
                enabled=next_enabled,
                status=status,
                sports=row.safe.sports if sports is None else sports,
                regions=row.safe.regions if regions is None else regions,
                markets=row.safe.markets if markets is None else markets,
                poll_interval_seconds=(
                    row.safe.poll_interval_seconds if poll_interval_seconds is None else poll_interval_seconds
                ),
                request_budget_per_hour=(
                    row.safe.request_budget_per_hour
                    if request_budget_per_hour is None
                    else request_budget_per_hour
                ),
            )
            return row.safe

    def record_test_result(
        self, user_id: str, provider_code: str, result: ProviderTestResult
    ) -> ProviderConnectionSafe:
        key = (user_id, provider_code)
        with self._lock:
            try:
                row = self._rows[key]
            except KeyError as exc:
                raise KeyError(provider_code) from exc
            row.safe = replace(
                row.safe,
                status=result.status,
                last_tested_at=result.tested_at,
                last_successful_request_at=result.successful_request_at,
                last_market_update_at=result.market_update_at,
                capabilities=dict(result.capabilities),
                coverage=dict(result.coverage),
                quota=dict(result.quota),
                last_error_code=result.error_code,
                last_error_summary=redact_secret_text(result.error_summary or "") or None,
            )
            return row.safe

    def delete_connection(self, user_id: str, provider_code: str) -> bool:
        with self._lock:
            return self._rows.pop((user_id, provider_code), None) is not None


class ProviderConnectionService:
    """Ownership-preserving application service; safe DTOs never carry secrets."""

    def __init__(self, repository: ProviderConnectionRepository, cipher: CredentialCipher) -> None:
        self._repository = repository
        self._cipher = cipher

    def list(self, user_id: str) -> tuple[ProviderConnectionSafe, ...]:
        return self._repository.list_connections(user_id)

    def save_or_replace(self, user_id: str, request: ProviderConnectionInput) -> ProviderConnectionSafe:
        existing = next(
            (item for item in self._repository.list_connections(user_id)
             if item.provider_code == request.provider_code),
            None,
        )
        expects_credentials = bool(PROVIDER_CREDENTIAL_FIELDS[request.provider_code])
        if expects_credentials and not request.credentials:
            if existing is None:
                raise ValueError("required provider credential is missing")
            envelope = self._repository.get_encrypted_credentials(user_id, request.provider_code)
            if envelope is None:
                raise ValueError("required provider credential is missing")
            return self._repository.save_connection(
                user_id, request, envelope, existing.credential_mask,
            )
        envelope = (
            self._cipher.encrypt(
                user_id=user_id,
                provider_code=request.provider_code,
                credentials=request.credentials,
            )
            if request.credentials
            else None
        )
        return self._repository.save_connection(
            user_id,
            request,
            envelope,
            mask_credentials(request.credentials),
        )

    def get_credentials(self, user_id: str, provider_code: str) -> dict[str, str]:
        envelope = self._repository.get_encrypted_credentials(user_id, provider_code)
        if envelope is None:
            return {}
        return self._cipher.decrypt(
            user_id=user_id,
            provider_code=provider_code,
            envelope=envelope,
        )

    def update(self, user_id: str, provider_code: str, **changes: object) -> ProviderConnectionSafe:
        return self._repository.update_configuration(user_id, provider_code, **changes)  # type: ignore[arg-type]

    def record_test(
        self, user_id: str, provider_code: str, result: ProviderTestResult
    ) -> ProviderConnectionSafe:
        secrets = tuple(self.get_credentials(user_id, provider_code).values())
        safe_result = replace(
            result,
            error_summary=redact_secret_text(result.error_summary or "", secrets=secrets) or None,
        )
        return self._repository.record_test_result(user_id, provider_code, safe_result)

    def delete(self, user_id: str, provider_code: str) -> bool:
        return self._repository.delete_connection(user_id, provider_code)


class PostgresProviderConnectionRepository:
    """Transactionally stores safe metadata and encrypted bytes in PostgreSQL.

    ``connection_factory`` must return a DB-API compatible context manager.  A
    deployment can supply ``psycopg.connect`` without coupling core tests to a
    live database or putting database credentials into this module.
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    @staticmethod
    def _safe_from_row(row: tuple[Any, ...]) -> ProviderConnectionSafe:
        return ProviderConnectionSafe(
            id=str(row[0]), provider_code=row[1], display_name=row[2], enabled=row[3], status=row[4],
            credential_mask=row[5] or {}, sports=tuple(row[6] or ()), regions=tuple(row[7] or ()),
            markets=tuple(row[8] or ()), poll_interval_seconds=row[9], request_budget_per_hour=row[10],
            last_tested_at=row[11], last_successful_request_at=row[12], last_market_update_at=row[13],
            capabilities=row[14] or {}, coverage=row[15] or {}, quota=row[16] or {},
            last_error_code=row[17], last_error_summary=row[18],
        )

    @staticmethod
    def _select_sql(where: str) -> str:
        return f"""
            select c.id, p.slug, p.display_name, c.enabled, c.status, c.credential_mask,
                   c.selected_sports, c.selected_regions, c.selected_markets,
                   c.poll_interval_seconds, c.request_budget_per_hour, c.last_tested_at,
                   c.last_successful_request_at, c.last_market_update_at,
                   c.capabilities, c.coverage, c.quota, c.last_error_code, c.last_error_summary
              from public.user_provider_connections c
              join public.providers p on p.id = c.provider_id
             where {where}
        """

    def list_connections(self, user_id: str) -> tuple[ProviderConnectionSafe, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(self._select_sql("c.user_id = %s order by p.display_name"), (user_id,))
            return tuple(self._safe_from_row(row) for row in cursor.fetchall())

    def save_connection(
        self,
        user_id: str,
        request: ProviderConnectionInput,
        encrypted: EncryptedCredential | None,
        credential_mask: Mapping[str, str],
    ) -> ProviderConnectionSafe:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.user_provider_connections
                    (user_id, provider_id, enabled, status, selected_sports, selected_regions,
                     selected_markets, poll_interval_seconds, request_budget_per_hour,
                     credential_mask, disabled_at)
                select %s, p.id, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s,
                       %s::jsonb, case when %s then null else now() end
                  from public.providers p where p.slug = %s
                on conflict (user_id, provider_id) do update set
                    enabled = excluded.enabled, status = excluded.status,
                    selected_sports = excluded.selected_sports,
                    selected_regions = excluded.selected_regions,
                    selected_markets = excluded.selected_markets,
                    poll_interval_seconds = excluded.poll_interval_seconds,
                    request_budget_per_hour = excluded.request_budget_per_hour,
                    credential_mask = excluded.credential_mask,
                    disabled_at = case when excluded.enabled then null else now() end,
                    updated_at = now()
                returning id
                """,
                (
                    user_id, request.enabled, "requires_configuration" if request.enabled else "disabled",
                    _json_array(request.sports), _json_array(request.regions), _json_array(request.markets),
                    request.poll_interval_seconds, request.request_budget_per_hour,
                    _json_object(credential_mask), request.enabled, request.provider_code,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                raise KeyError(request.provider_code)
            connection_id = inserted[0]
            if encrypted is None:
                cursor.execute(
                    "delete from private.provider_credentials where connection_id = %s and user_id = %s",
                    (connection_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    insert into private.provider_credentials
                        (connection_id, user_id, algorithm, key_version, nonce, ciphertext, credential_fields)
                    values (%s, %s, 'AES-256-GCM', %s, %s, %s, %s)
                    on conflict (connection_id) do update set
                        key_version = excluded.key_version, nonce = excluded.nonce,
                        ciphertext = excluded.ciphertext, credential_fields = excluded.credential_fields,
                        rotated_at = now()
                    """,
                    (
                        connection_id, user_id, encrypted.key_version, encrypted.nonce,
                        encrypted.ciphertext, list(encrypted.credential_fields),
                    ),
                )
            cursor.execute(self._select_sql("c.id = %s and c.user_id = %s"), (connection_id, user_id))
            return self._safe_from_row(cursor.fetchone())

    def get_encrypted_credentials(self, user_id: str, provider_code: str) -> EncryptedCredential | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select s.ciphertext, s.nonce, s.key_version, s.credential_fields
                  from private.provider_credentials s
                  join public.user_provider_connections c
                    on c.id = s.connection_id and c.user_id = s.user_id
                  join public.providers p on p.id = c.provider_id
                 where s.user_id = %s and p.slug = %s
                """,
                (user_id, provider_code),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    select 1 from public.user_provider_connections c
                    join public.providers p on p.id = c.provider_id
                    where c.user_id = %s and p.slug = %s
                    """,
                    (user_id, provider_code),
                )
                if cursor.fetchone() is not None and not PROVIDER_CREDENTIAL_FIELDS.get(provider_code):
                    return None
                raise KeyError(provider_code)
            return EncryptedCredential(bytes(row[0]), bytes(row[1]), row[2], tuple(row[3]))

    def update_configuration(
        self,
        user_id: str,
        provider_code: str,
        *,
        enabled: bool | None = None,
        sports: tuple[str, ...] | None = None,
        regions: tuple[str, ...] | None = None,
        markets: tuple[str, ...] | None = None,
        poll_interval_seconds: int | None = None,
        request_budget_per_hour: int | None = None,
    ) -> ProviderConnectionSafe:
        if poll_interval_seconds is not None and poll_interval_seconds < 15:
            raise ValueError("poll interval must be at least fifteen seconds")
        if request_budget_per_hour is not None and request_budget_per_hour < 1:
            raise ValueError("request budget must be positive")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.user_provider_connections c set
                    enabled = coalesce(%s, c.enabled),
                    status = case when %s = false then 'disabled'
                                  when %s = true and c.status = 'disabled' then 'requires_configuration'
                                  else c.status end,
                    selected_sports = coalesce(%s::jsonb, c.selected_sports),
                    selected_regions = coalesce(%s::jsonb, c.selected_regions),
                    selected_markets = coalesce(%s::jsonb, c.selected_markets),
                    poll_interval_seconds = coalesce(%s, c.poll_interval_seconds),
                    request_budget_per_hour = coalesce(%s, c.request_budget_per_hour),
                    disabled_at = case when %s = false then now() when %s = true then null else c.disabled_at end,
                    updated_at = now()
                  from public.providers p
                 where c.provider_id = p.id and c.user_id = %s and p.slug = %s
                returning c.id
                """,
                (
                    enabled, enabled, enabled, _json_array(sports), _json_array(regions), _json_array(markets),
                    poll_interval_seconds, request_budget_per_hour, enabled, enabled, user_id, provider_code,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(provider_code)
            cursor.execute(self._select_sql("c.id = %s and c.user_id = %s"), (row[0], user_id))
            return self._safe_from_row(cursor.fetchone())

    def record_test_result(
        self, user_id: str, provider_code: str, result: ProviderTestResult
    ) -> ProviderConnectionSafe:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.user_provider_connections c set
                    status = %s, last_tested_at = %s, last_successful_request_at = %s,
                    last_market_update_at = %s, capabilities = %s::jsonb, coverage = %s::jsonb,
                    quota = %s::jsonb, last_error_code = %s, last_error_summary = %s, updated_at = now()
                  from public.providers p
                 where c.provider_id = p.id and c.user_id = %s and p.slug = %s
                returning c.id
                """,
                (
                    result.status, result.tested_at, result.successful_request_at, result.market_update_at,
                    _json_object(result.capabilities), _json_object(result.coverage), _json_object(result.quota),
                    result.error_code, redact_secret_text(result.error_summary or "") or None,
                    user_id, provider_code,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(provider_code)
            cursor.execute(self._select_sql("c.id = %s and c.user_id = %s"), (row[0], user_id))
            return self._safe_from_row(cursor.fetchone())

    def delete_connection(self, user_id: str, provider_code: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                delete from public.user_provider_connections c using public.providers p
                 where c.provider_id = p.id and c.user_id = %s and p.slug = %s
                """,
                (user_id, provider_code),
            )
            return bool(cursor.rowcount)


def _json_array(values: Iterable[str] | None) -> str | None:
    if values is None:
        return None
    import json

    return json.dumps(list(values), separators=(",", ":"))


def _json_object(values: Mapping[str, object]) -> str:
    import json

    return json.dumps(dict(values), separators=(",", ":"), sort_keys=True)
