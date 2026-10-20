"""Durable, device-local state for the packaged EDGEOS desktop application.

This store is intentionally limited to simulated portfolio activity and private
UI preferences.  Provider credentials, Supabase tokens, signing material, and
live market data do not belong in this database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Callable, TypeVar, cast
from uuid import uuid4

from .domain import (
    AlertCreate,
    AlertUpdate,
    FillCreate,
    PositionCreate,
    PriceKind,
    SettlementCreate,
    WatchlistCreate,
)
from .ledger import LedgerEntry, PaperFill, PaperLedger, PaperPosition
from .store import UserStore


LOCAL_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 16 * 1024 * 1024
LOCAL_BACKUP_FORMAT = "edgeos-local-workspace-backup"
LOCAL_BACKUP_VERSION = 1
MAX_BACKUP_BYTES = (MAX_STATE_BYTES * 3) + (1024 * 1024)
PORTABLE_NAMESPACES = frozenset({"paper_ledger", "private_user_state", "saved_opportunities"})
_JSON_TYPE_KEY = "__edgeos_local_type__"
_T = TypeVar("_T")
_UserStateSnapshot = tuple[
    dict[str, list[dict[str, object]]],
    dict[str, list[dict[str, object]]],
    dict[str, dict[str, object]],
    dict[str, list[dict[str, object]]],
    dict[tuple[str, str, str], str],
]


class LocalPersistenceError(RuntimeError):
    """Safe local-storage failure that never includes the database path."""


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return {_JSON_TYPE_KEY: "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {
            _JSON_TYPE_KEY: "datetime",
            "value": value.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
    raise TypeError(f"unsupported local-state value: {type(value).__name__}")


def _json_object_hook(value: dict[str, object]) -> object:
    kind = value.get(_JSON_TYPE_KEY)
    if kind == "decimal" and set(value) == {_JSON_TYPE_KEY, "value"}:
        return Decimal(str(value["value"]))
    if kind == "datetime" and set(value) == {_JSON_TYPE_KEY, "value"}:
        parsed = datetime.fromisoformat(str(value["value"]).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("persisted datetime is missing a timezone")
        return parsed.astimezone(UTC)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


class LocalStateDatabase:
    """Small schema-versioned SQLite state store with atomic blob replacement."""

    def __init__(self, path: str | os.PathLike[str], *, allowed_root: str | os.PathLike[str]) -> None:
        configured_path = Path(path)
        configured_root = Path(allowed_root)
        if not configured_path.is_absolute() or not configured_root.is_absolute():
            raise LocalPersistenceError("local data paths must be absolute")
        self.path = configured_path.resolve(strict=False)
        self.allowed_root = configured_root.resolve(strict=False)
        if self.path == self.allowed_root or self.allowed_root not in self.path.parents:
            raise LocalPersistenceError("local database must stay inside the application data directory")
        self._lock = RLock()
        self._generation = 0
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Tighten inherited/pre-existing permissions before SQLite opens
            # the database or creates WAL companions, then repeat afterward.
            self._restrict_permissions()
            self._initialize()
            self._restrict_permissions()
        except LocalPersistenceError:
            raise
        except (OSError, sqlite3.Error):
            # OS errors can include the absolute user-profile path. Keep the
            # desktop diagnostic safe while preserving the database untouched.
            raise LocalPersistenceError("local database could not be initialized") from None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma trusted_schema = off")
        connection.execute("pragma busy_timeout = 5000")
        connection.execute("pragma synchronous = full")
        connection.execute("pragma journal_mode = wal")
        connection.execute("pragma wal_autocheckpoint = 1000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            check = connection.execute("pragma quick_check(1)").fetchone()
            if check is None or check[0] != "ok":
                raise LocalPersistenceError(
                    "local database failed its integrity check; the original file was preserved"
                )
            version = int(connection.execute("pragma user_version").fetchone()[0])
            if version not in {0, LOCAL_SCHEMA_VERSION}:
                raise LocalPersistenceError("local database schema version is unsupported")
            if version == 0:
                connection.execute("begin immediate")
                try:
                    connection.execute(
                        """
                        create table if not exists edgeos_local_state (
                          namespace text primary key
                            check (namespace glob '[a-z0-9_]*' and length(namespace) between 1 and 80),
                          schema_version integer not null check (schema_version > 0),
                          payload_json text not null,
                          payload_sha256 text not null check (length(payload_sha256) = 64),
                          updated_at text not null
                        ) strict
                        """
                    )
                    connection.execute(f"pragma user_version = {LOCAL_SCHEMA_VERSION}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    def _restrict_permissions(self) -> None:
        if os.name == "nt":
            # The file lives under the current user's profile; Windows ACLs are
            # inherited from that directory.  The installer never makes it shared.
            return
        try:
            os.chmod(self.path.parent, 0o700)
            if self.path.exists():
                os.chmod(self.path, 0o600)
            for suffix in ("-wal", "-shm"):
                companion = Path(f"{self.path}{suffix}")
                if companion.exists():
                    os.chmod(companion, 0o600)
        except OSError:
            raise LocalPersistenceError("local database permissions could not be secured") from None

    def load(self, namespace: str) -> object | None:
        with self._lock:
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        """select schema_version, payload_json, payload_sha256
                             from edgeos_local_state where namespace = ?""",
                        (namespace,),
                    ).fetchone()
            except sqlite3.Error:
                raise LocalPersistenceError("local data could not be read") from None
        if row is None:
            return None
        version, payload, expected_hash = row
        if version != LOCAL_SCHEMA_VERSION:
            raise LocalPersistenceError("local state version is unsupported")
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_STATE_BYTES:
            raise LocalPersistenceError("local state exceeds its safe size limit")
        actual_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise LocalPersistenceError("local state failed its integrity check")
        try:
            return json.loads(payload, object_hook=_json_object_hook)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LocalPersistenceError("local state could not be decoded") from exc

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def save(self, namespace: str, value: object, *, expected_generation: int | None = None) -> None:
        try:
            payload = json.dumps(
                value,
                default=_json_default,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise LocalPersistenceError("local state contains an unsupported value") from exc
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise LocalPersistenceError("local state exceeds its safe size limit")
        digest = hashlib.sha256(encoded).hexdigest()
        updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                raise LocalPersistenceError("local state handle is stale after restore")
            try:
                with self._connect() as connection:
                    connection.execute("begin immediate")
                    try:
                        connection.execute(
                            """
                            insert into edgeos_local_state
                              (namespace, schema_version, payload_json, payload_sha256, updated_at)
                            values (?, ?, ?, ?, ?)
                            on conflict(namespace) do update set
                              schema_version=excluded.schema_version,
                              payload_json=excluded.payload_json,
                              payload_sha256=excluded.payload_sha256,
                              updated_at=excluded.updated_at
                            """,
                            (namespace, LOCAL_SCHEMA_VERSION, payload, digest, updated_at),
                        )
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                self._restrict_permissions()
            except LocalPersistenceError:
                raise
            except (OSError, sqlite3.Error):
                raise LocalPersistenceError("local data could not be saved") from None

    def backup(self, destination: str | os.PathLike[str]) -> None:
        """Create a transactionally consistent SQLite backup inside user data."""

        target = Path(destination)
        if not target.is_absolute():
            raise LocalPersistenceError("local backup path must be absolute")
        resolved = target.resolve(strict=False)
        if resolved == self.allowed_root or self.allowed_root not in resolved.parents:
            raise LocalPersistenceError("local backup must stay inside the application data directory")
        if resolved == self.path:
            raise LocalPersistenceError("local backup must not replace the active database")
        resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = resolved.with_name(f".{resolved.name}.{uuid4().hex}.tmp")
        with self._lock:
            try:
                # Connection context managers only commit/rollback; close both
                # handles before replacing the temporary database on Windows.
                with closing(self._connect()) as source, closing(sqlite3.connect(temporary)) as destination_db:
                    source.backup(destination_db)
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                    os.chmod(resolved.parent, 0o700)
                os.replace(temporary, resolved)
            except (OSError, sqlite3.Error):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise LocalPersistenceError("local backup could not be created") from None

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def export_portable(self) -> bytes:
        """Return a portable, integrity-checked backup of local user namespaces."""

        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        """select namespace, schema_version, payload_json, payload_sha256
                             from edgeos_local_state order by namespace"""
                    ).fetchall()
            except sqlite3.Error:
                raise LocalPersistenceError("local backup could not be created") from None
        namespaces: list[dict[str, object]] = []
        for namespace, schema_version, payload, digest in rows:
            if namespace not in PORTABLE_NAMESPACES:
                continue
            if schema_version != LOCAL_SCHEMA_VERSION or not isinstance(payload, str):
                raise LocalPersistenceError("local state version is unsupported")
            encoded = payload.encode("utf-8")
            if len(encoded) > MAX_STATE_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
                raise LocalPersistenceError("local state failed its integrity check")
            try:
                decoded = json.loads(payload, object_hook=_json_object_hook)
                _validate_portable_namespace(namespace, decoded)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise LocalPersistenceError("local state could not be backed up safely") from exc
            namespaces.append({
                "namespace": namespace,
                "schema_version": LOCAL_SCHEMA_VERSION,
                "payload_json": payload,
                "payload_sha256": digest,
            })
        document: dict[str, object] = {
            "format": LOCAL_BACKUP_FORMAT,
            "format_version": LOCAL_BACKUP_VERSION,
            "local_schema_version": LOCAL_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "namespaces": namespaces,
        }
        document["content_sha256"] = hashlib.sha256(self._canonical_json(document)).hexdigest()
        output = self._canonical_json(document)
        if len(output) > MAX_BACKUP_BYTES:
            raise LocalPersistenceError("local backup exceeds its safe size limit")
        return output

    def restore_portable(self, source: bytes) -> tuple[str, ...]:
        """Validate a portable backup completely, then replace local state atomically."""

        if not source or len(source) > MAX_BACKUP_BYTES:
            raise LocalPersistenceError("local backup is empty or exceeds its safe size limit")
        try:
            document = json.loads(source.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalPersistenceError("local backup is not valid JSON") from exc
        expected_keys = {
            "format", "format_version", "local_schema_version", "created_at",
            "namespaces", "content_sha256",
        }
        if not isinstance(document, dict) or set(document) != expected_keys:
            raise LocalPersistenceError("local backup has an invalid document shape")
        if (
            document["format"] != LOCAL_BACKUP_FORMAT
            or document["format_version"] != LOCAL_BACKUP_VERSION
            or document["local_schema_version"] != LOCAL_SCHEMA_VERSION
        ):
            raise LocalPersistenceError("local backup version is unsupported")
        try:
            created_at = datetime.fromisoformat(str(document["created_at"]).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise LocalPersistenceError("local backup timestamp is invalid") from exc
        claimed_digest = document["content_sha256"]
        unsigned = {key: value for key, value in document.items() if key != "content_sha256"}
        actual_digest = hashlib.sha256(self._canonical_json(unsigned)).hexdigest()
        if not isinstance(claimed_digest, str) or claimed_digest != actual_digest:
            raise LocalPersistenceError("local backup failed its integrity check")
        raw_namespaces = document["namespaces"]
        if not isinstance(raw_namespaces, list) or len(raw_namespaces) > len(PORTABLE_NAMESPACES):
            raise LocalPersistenceError("local backup namespaces are invalid")
        prepared: dict[str, tuple[str, str]] = {}
        for row in raw_namespaces:
            if not isinstance(row, dict) or set(row) != {
                "namespace", "schema_version", "payload_json", "payload_sha256"
            }:
                raise LocalPersistenceError("local backup namespace has an invalid shape")
            namespace = row["namespace"]
            payload = row["payload_json"]
            digest = row["payload_sha256"]
            if (
                not isinstance(namespace, str)
                or namespace not in PORTABLE_NAMESPACES
                or namespace in prepared
                or row["schema_version"] != LOCAL_SCHEMA_VERSION
                or not isinstance(payload, str)
                or not isinstance(digest, str)
            ):
                raise LocalPersistenceError("local backup namespace is invalid")
            encoded = payload.encode("utf-8")
            if len(encoded) > MAX_STATE_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
                raise LocalPersistenceError("local backup namespace failed its integrity check")
            try:
                decoded = json.loads(payload, object_hook=_json_object_hook)
                _validate_portable_namespace(namespace, decoded)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise LocalPersistenceError("local backup namespace data is invalid") from exc
            prepared[namespace] = (payload, digest)
        restored_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute("begin immediate")
                    try:
                        placeholders = ",".join("?" for _ in PORTABLE_NAMESPACES)
                        connection.execute(
                            f"delete from edgeos_local_state where namespace in ({placeholders})",
                            tuple(sorted(PORTABLE_NAMESPACES)),
                        )
                        connection.executemany(
                            """insert into edgeos_local_state
                                 (namespace, schema_version, payload_json, payload_sha256, updated_at)
                                 values (?, ?, ?, ?, ?)""",
                            [
                                (namespace, LOCAL_SCHEMA_VERSION, payload, digest, restored_at)
                                for namespace, (payload, digest) in sorted(prepared.items())
                            ],
                        )
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                self._restrict_permissions()
                self._generation += 1
            except LocalPersistenceError:
                raise
            except (OSError, sqlite3.Error):
                raise LocalPersistenceError("local backup could not be restored") from None
        return tuple(sorted(prepared))


class LocalPaperLedger(PaperLedger):
    """PaperLedger-compatible durable desktop implementation."""

    _NAMESPACE = "paper_ledger"

    def __init__(self, database: LocalStateDatabase, *, starting_balance: Decimal = Decimal("10000")) -> None:
        self._database = database
        self._database_generation = database.generation
        super().__init__(starting_balance=starting_balance)
        persisted = database.load(self._NAMESPACE)
        if persisted is not None:
            self._restore_payload(persisted)

    def _payload(self) -> dict[str, object]:
        return {
            "starting_balance": self.starting_balance,
            "positions": [position.model_dump(mode="json") for position in self._positions.values()],
            "entries": [entry.model_dump(mode="json") for entry in self._entries],
            "idempotency": [
                {"user_id": key[0], "operation": key[1], "key": key[2], "value": value}
                for key, value in self._idempotency.items()
            ],
        }

    def _restore_payload(self, value: object) -> None:
        if not isinstance(value, dict):
            raise LocalPersistenceError("paper ledger state has an invalid shape")
        try:
            balance = Decimal(str(value["starting_balance"]))
            positions = [PaperPosition.model_validate(item) for item in cast(list[object], value["positions"])]
            entries = [LedgerEntry.model_validate(item) for item in cast(list[object], value["entries"])]
            idempotency = {
                (str(item["user_id"]), str(item["operation"]), str(item["key"])): str(item["value"])
                for item in cast(list[dict[str, object]], value["idempotency"])
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalPersistenceError("paper ledger state has an invalid shape") from exc
        if balance < 0:
            raise LocalPersistenceError("paper ledger balance is invalid")
        position_map = {position.position_id: position for position in positions}
        if len(position_map) != len(positions) or len({entry.entry_id for entry in entries}) != len(entries):
            raise LocalPersistenceError("paper ledger contains duplicate identifiers")
        self.starting_balance = balance
        self._positions = position_map
        self._entries = entries
        self._idempotency = idempotency
        self._validate_financial_integrity()

    def _validate_financial_integrity(self) -> None:
        fills_by_id: dict[str, tuple[PaperPosition, PaperFill]] = {}
        for position in self._positions.values():
            leg_by_id = {leg.offer_id: leg for leg in position.request.simulation.legs}
            filled_by_leg: dict[str, Decimal] = defaultdict(Decimal)
            for fill in position.fills:
                if fill.fill_id in fills_by_id or fill.leg_id not in leg_by_id:
                    raise LocalPersistenceError("paper ledger fill references are invalid")
                leg = leg_by_id[fill.leg_id]
                if (
                    (leg.price_kind is PriceKind.DECIMAL_ODDS and fill.price <= 1)
                    or (leg.price_kind is PriceKind.CONTRACT_ASK and fill.price > 1)
                ):
                    raise LocalPersistenceError("paper ledger fill price is invalid")
                filled_by_leg[fill.leg_id] += fill.quantity
                if filled_by_leg[fill.leg_id] > leg.filled_quantity:
                    raise LocalPersistenceError("paper ledger fill exceeds its planned quantity")
                fills_by_id[fill.fill_id] = (position, fill)

        recorded_fills: set[str] = set()
        for entry in self._entries:
            entry_position = self._positions.get(entry.position_id)
            if entry_position is None or entry.user_id != entry_position.user_id:
                raise LocalPersistenceError("paper ledger entry ownership is invalid")
            if entry.entry_type != "fill":
                continue
            fill_id = entry.metadata.get("fill_id")
            if fill_id is None:
                raise LocalPersistenceError("paper ledger fill entry is invalid")
            record = fills_by_id.get(fill_id)
            if record is None or fill_id in recorded_fills:
                raise LocalPersistenceError("paper ledger fill entry is invalid")
            fill_position, fill_value = record
            if fill_position.position_id != entry.position_id:
                raise LocalPersistenceError("paper ledger fill entry is invalid")
            leg = next(
                leg for leg in fill_position.request.simulation.legs
                if leg.offer_id == fill_value.leg_id
            )
            expected_cost = (
                fill_value.quantity
                if leg.price_kind is PriceKind.DECIMAL_ODDS
                else fill_value.quantity * fill_value.price
            ) + fill_value.fee
            if entry.amount != -expected_cost:
                raise LocalPersistenceError("paper ledger fill cash flow is inconsistent")
            recorded_fills.add(fill_id)
        if recorded_fills != set(fills_by_id):
            raise LocalPersistenceError("paper ledger fill entries are incomplete")
        user_ids = {position.user_id for position in self._positions.values()}
        if any(self.summary(user_id)["cash_balance"] < 0 for user_id in user_ids):
            raise LocalPersistenceError("paper ledger cash balance is negative")

    def _mutate(self, operation: Callable[[], _T]) -> _T:
        with self._lock:
            previous = (
                self.starting_balance,
                dict(self._positions),
                list(self._entries),
                dict(self._idempotency),
            )
            try:
                result = operation()
                self._database.save(
                    self._NAMESPACE, self._payload(),
                    expected_generation=self._database_generation,
                )
                return result
            except Exception:
                self.starting_balance, self._positions, self._entries, self._idempotency = previous
                raise

    def create_position(
        self, user_id: str, request: PositionCreate, *, idempotency_key: str | None = None
    ) -> PaperPosition:
        return self._mutate(
            lambda: super(LocalPaperLedger, self).create_position(
                user_id, request, idempotency_key=idempotency_key
            )
        )

    def add_fill(
        self,
        user_id: str,
        position_id: str,
        request: FillCreate,
        *,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        return self._mutate(
            lambda: super(LocalPaperLedger, self).add_fill(
                user_id, position_id, request, idempotency_key=idempotency_key
            )
        )

    def settle(
        self,
        user_id: str,
        position_id: str,
        request: SettlementCreate,
        *,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        return self._mutate(
            lambda: super(LocalPaperLedger, self).settle(
                user_id, position_id, request, idempotency_key=idempotency_key
            )
        )

    def cancel(
        self,
        user_id: str,
        position_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        return self._mutate(
            lambda: super(LocalPaperLedger, self).cancel(
                user_id, position_id, reason=reason, idempotency_key=idempotency_key
            )
        )

    def adjustment(self, user_id: str, position_id: str, amount: Decimal, reason: str) -> LedgerEntry:
        return self._mutate(
            lambda: super(LocalPaperLedger, self).adjustment(user_id, position_id, amount, reason)
        )


class LocalUserStore(UserStore):
    """UserStore-compatible durable desktop preferences/watchlist/alert store."""

    _NAMESPACE = "private_user_state"

    def __init__(self, database: LocalStateDatabase) -> None:
        self._database = database
        self._database_generation = database.generation
        super().__init__()
        persisted = database.load(self._NAMESPACE)
        if persisted is not None:
            self._restore_payload(persisted)

    def _payload(self) -> dict[str, object]:
        return {
            "watchlists": dict(self._watchlists),
            "alerts": dict(self._alerts),
            "settings": dict(self._settings),
            "alert_events": dict(self._alert_events),
            "idempotency": [
                {"user_id": key[0], "operation": key[1], "key": key[2], "value": value}
                for key, value in self._idempotency.items()
            ],
        }

    @staticmethod
    def _mapping_of_lists(value: object) -> dict[str, list[dict[str, object]]]:
        if not isinstance(value, dict):
            raise LocalPersistenceError("local user state has an invalid shape")
        result: dict[str, list[dict[str, object]]] = {}
        for key, items in value.items():
            if not isinstance(key, str) or not isinstance(items, list) or any(
                not isinstance(item, dict) for item in items
            ):
                raise LocalPersistenceError("local user state has an invalid shape")
            result[key] = cast(list[dict[str, object]], items)
        return result

    def _restore_payload(self, value: object) -> None:
        if not isinstance(value, dict):
            raise LocalPersistenceError("local user state has an invalid shape")
        try:
            watchlists = self._mapping_of_lists(value["watchlists"])
            alerts = self._mapping_of_lists(value["alerts"])
            alert_events = self._mapping_of_lists(value["alert_events"])
            settings = cast(dict[str, dict[str, object]], value["settings"])
            if not isinstance(settings, dict) or any(
                not isinstance(key, str) or not isinstance(item, dict)
                for key, item in settings.items()
            ):
                raise LocalPersistenceError("local user settings have an invalid shape")
            idempotency = {
                (str(item["user_id"]), str(item["operation"]), str(item["key"])): str(item["value"])
                for item in cast(list[dict[str, object]], value["idempotency"])
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalPersistenceError("local user state has an invalid shape") from exc
        self._watchlists = defaultdict(list, watchlists)
        self._alerts = defaultdict(list, alerts)
        self._settings.update(settings)
        self._alert_events = defaultdict(list, alert_events)
        self._idempotency = idempotency

    def _snapshot(self) -> _UserStateSnapshot:
        return (
            copy.deepcopy(self._watchlists),
            copy.deepcopy(self._alerts),
            copy.deepcopy(self._settings),
            copy.deepcopy(self._alert_events),
            dict(self._idempotency),
        )

    def _mutate(self, operation: Callable[[], _T]) -> _T:
        with self._lock:
            previous = self._snapshot()
            try:
                result = operation()
                self._database.save(
                    self._NAMESPACE, self._payload(),
                    expected_generation=self._database_generation,
                )
                return result
            except Exception:
                (
                    self._watchlists,
                    self._alerts,
                    self._settings,
                    self._alert_events,
                    self._idempotency,
                ) = previous
                raise

    def add_watchlist(self, user_id: str, request: WatchlistCreate) -> dict[str, object]:
        return self._mutate(lambda: super(LocalUserStore, self).add_watchlist(user_id, request))

    def delete_watchlist(self, user_id: str, item_id: str) -> None:
        self._mutate(lambda: super(LocalUserStore, self).delete_watchlist(user_id, item_id))

    def add_alert(self, user_id: str, request: AlertCreate) -> dict[str, object]:
        return self._mutate(lambda: super(LocalUserStore, self).add_alert(user_id, request))

    def update_alert(self, user_id: str, alert_id: str, request: AlertUpdate) -> dict[str, object]:
        return self._mutate(
            lambda: super(LocalUserStore, self).update_alert(user_id, alert_id, request)
        )

    def delete_alert(self, user_id: str, alert_id: str) -> None:
        self._mutate(lambda: super(LocalUserStore, self).delete_alert(user_id, alert_id))

    def update_settings(self, user_id: str, values: dict[str, object]) -> dict[str, object]:
        return self._mutate(
            lambda: super(LocalUserStore, self).update_settings(user_id, values)
        )


class LocalSavedOpportunityStore:
    """Durable local saved-opportunity metadata, separate from demo market data."""

    _NAMESPACE = "saved_opportunities"

    def __init__(self, database: LocalStateDatabase | None = None) -> None:
        self._database = database
        self._database_generation = database.generation if database is not None else None
        self._rows: dict[str, dict[str, tuple[bool, datetime]]] = defaultdict(dict)
        self._lock = RLock()
        if database is not None:
            persisted = database.load(self._NAMESPACE)
            if persisted is not None:
                self._restore_payload(persisted)

    def _restore_payload(self, value: object) -> None:
        if not isinstance(value, dict):
            raise LocalPersistenceError("saved-opportunity state has an invalid shape")
        rows: dict[str, dict[str, tuple[bool, datetime]]] = {}
        try:
            for user_id, user_rows in value.items():
                if not isinstance(user_id, str) or not isinstance(user_rows, dict):
                    raise TypeError
                rows[user_id] = {}
                for opportunity_id, item in user_rows.items():
                    if not isinstance(opportunity_id, str) or not isinstance(item, dict):
                        raise TypeError
                    timestamp = item["saved_at"]
                    if not isinstance(timestamp, datetime):
                        raise TypeError
                    rows[user_id][opportunity_id] = (bool(item["favorited"]), timestamp)
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalPersistenceError("saved-opportunity state has an invalid shape") from exc
        self._rows = defaultdict(dict, rows)

    def _payload(self) -> dict[str, object]:
        return {
            user_id: {
                opportunity_id: {"favorited": record[0], "saved_at": record[1]}
                for opportunity_id, record in rows.items()
            }
            for user_id, rows in self._rows.items()
        }

    def _save(self) -> None:
        if self._database is not None:
            self._database.save(
                self._NAMESPACE, self._payload(),
                expected_generation=self._database_generation,
            )

    def list(self, user_id: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(
                {
                    "opportunity_id": opportunity_id,
                    "favorited": value[0],
                    "saved_at": value[1],
                }
                for opportunity_id, value in self._rows[user_id].items()
            )

    def save(self, user_id: str, opportunity_id: str, *, favorited: bool) -> dict[str, object]:
        with self._lock:
            previous = copy.deepcopy(self._rows)
            saved_at = datetime.now(UTC)
            self._rows[user_id][opportunity_id] = (favorited, saved_at)
            try:
                self._save()
            except Exception:
                self._rows = previous
                raise
            return {
                "opportunity_id": opportunity_id,
                "favorited": favorited,
                "saved_at": saved_at,
            }

    def delete(self, user_id: str, opportunity_id: str) -> bool:
        with self._lock:
            previous = copy.deepcopy(self._rows)
            removed = self._rows[user_id].pop(opportunity_id, None) is not None
            if not removed:
                return False
            try:
                self._save()
            except Exception:
                self._rows = previous
                raise
            return True


def _validate_portable_namespace(namespace: str, value: object) -> None:
    """Exercise the same domain decoders used at startup before a restore commits."""

    if namespace == LocalPaperLedger._NAMESPACE:
        ledger_probe = object.__new__(LocalPaperLedger)
        PaperLedger.__init__(ledger_probe)
        ledger_probe._restore_payload(value)
        return
    if namespace == LocalUserStore._NAMESPACE:
        user_probe = object.__new__(LocalUserStore)
        UserStore.__init__(user_probe)
        user_probe._restore_payload(value)
        allowed_settings = {
            "timezone", "currency", "default_allocation", "maximum_quote_age_seconds",
            "demo_mode", "verified_matches_only",
        }
        if any(set(item) - allowed_settings for item in user_probe._settings.values()):
            raise ValueError("unsupported local setting in backup")
        return
    if namespace == LocalSavedOpportunityStore._NAMESPACE:
        saved_probe = LocalSavedOpportunityStore()
        saved_probe._restore_payload(value)
        return
    raise ValueError("unsupported local namespace")
