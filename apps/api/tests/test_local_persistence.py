from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import decimal_offer
from edgeos.arbitrage import calculate_arbitrage
from edgeos.domain import AlertCreate, FillCreate, PositionCreate, SettlementCreate, WatchlistCreate
from edgeos.local_persistence import (
    LocalPaperLedger,
    LocalPersistenceError,
    LocalSavedOpportunityStore,
    LocalStateDatabase,
    LocalUserStore,
)


def database(tmp_path: Path) -> LocalStateDatabase:
    return LocalStateDatabase(
        tmp_path / "data" / "edgeos-local.sqlite3",
        allowed_root=tmp_path,
    )


def test_local_paper_ledger_survives_restart_with_decimal_safe_fills(
    tmp_path: Path, fixed_now, zero_fee
) -> None:
    state = database(tmp_path)
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("home", "away"),
    )
    first = LocalPaperLedger(state)
    position = first.create_position(
        "desktop-user",
        PositionCreate(simulation=simulation),
        idempotency_key="desktop-position-0001",
    )
    first.add_fill(
        "desktop-user",
        position.position_id,
        FillCreate(
            leg_id="a",
            quantity=Decimal("100"),
            price=Decimal("2.17"),
            fee=Decimal("0.03"),
            filled_at=fixed_now,
        ),
        idempotency_key="desktop-fill-0001",
    )
    first.settle(
        "desktop-user",
        position.position_id,
        SettlementCreate(outcome_id="home", settled_at=fixed_now),
        idempotency_key="desktop-settlement-0001",
    )

    reopened = LocalPaperLedger(database(tmp_path))
    restored = reopened.get_position("desktop-user", position.position_id)
    assert restored.fills[0].price == Decimal("2.17")
    assert restored.fills[0].fee == Decimal("0.03")
    assert restored.settled_outcome_id == "home"
    assert reopened.summary("desktop-user")["simulated_cash_flow"] == Decimal("116.97")
    replay = reopened.create_position(
        "desktop-user",
        PositionCreate(simulation=simulation),
        idempotency_key="desktop-position-0001",
    )
    assert replay.position_id == position.position_id


def test_local_watchlist_alert_and_settings_survive_restart(tmp_path: Path) -> None:
    first = LocalUserStore(database(tmp_path))
    watched = first.add_watchlist(
        "desktop-user",
        WatchlistCreate(
            kind="provider",
            value="kalshi",
            label="Kalshi public data",
            idempotency_key="desktop-watch-0001",
        ),
    )
    alert = first.add_alert(
        "desktop-user",
        AlertCreate(
            condition="net_roi_above",
            threshold=Decimal("1.25"),
            minimum_net_profit=Decimal("10.50"),
            minimum_net_roi=Decimal("0.02"),
            minimum_liquidity=Decimal("250"),
            maximum_quote_age_seconds=45,
            idempotency_key="desktop-alert-0001",
        ),
    )
    first.update_settings(
        "desktop-user",
        {"timezone": "America/New_York", "default_allocation": Decimal("250.50")},
    )

    reopened = LocalUserStore(database(tmp_path))
    assert reopened.watchlists("desktop-user")[0]["id"] == watched["id"]
    assert reopened.alerts("desktop-user")[0]["id"] == alert["id"]
    assert reopened.alerts("desktop-user")[0]["threshold"] == Decimal("1.25")
    assert reopened.alerts("desktop-user")[0]["minimum_net_profit"] == Decimal("10.50")
    assert reopened.alerts("desktop-user")[0]["minimum_net_roi"] == Decimal("0.02")
    assert reopened.alerts("desktop-user")[0]["minimum_liquidity"] == Decimal("250")
    assert reopened.alerts("desktop-user")[0]["maximum_quote_age_seconds"] == 45
    assert reopened.settings("desktop-user")["timezone"] == "America/New_York"
    assert reopened.settings("desktop-user")["default_allocation"] == Decimal("250.50")
    replay = reopened.add_watchlist(
        "desktop-user",
        WatchlistCreate(
            kind="provider",
            value="kalshi",
            label="Kalshi public data",
            idempotency_key="desktop-watch-0001",
        ),
    )
    assert replay["id"] == watched["id"]


def test_local_settings_reject_provider_secrets(tmp_path: Path) -> None:
    state = database(tmp_path)
    store = LocalUserStore(state)
    secret = "provider-secret-must-not-be-persisted"

    with pytest.raises(ValueError, match="unsupported settings"):
        store.update_settings("desktop-user", {"provider_api_key": secret})

    for candidate in state.path.parent.iterdir():
        if candidate.is_file():
            assert secret.encode() not in candidate.read_bytes()


def test_local_saved_opportunities_survive_restart_and_delete(tmp_path: Path) -> None:
    first = LocalSavedOpportunityStore(database(tmp_path))
    first.save("desktop-user", "demo-opportunity-001", favorited=True)

    reopened = LocalSavedOpportunityStore(database(tmp_path))
    assert reopened.list("desktop-user")[0]["favorited"] is True
    assert reopened.delete("desktop-user", "demo-opportunity-001") is True
    assert LocalSavedOpportunityStore(database(tmp_path)).list("desktop-user") == ()


def test_local_database_rejects_paths_outside_application_data(tmp_path: Path) -> None:
    with pytest.raises(LocalPersistenceError, match="inside the application data directory"):
        LocalStateDatabase(
            tmp_path.parent / "outside.sqlite3",
            allowed_root=tmp_path,
        )

    with pytest.raises(LocalPersistenceError, match="must be absolute"):
        LocalStateDatabase("relative.sqlite3", allowed_root=tmp_path)


def test_local_database_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "user-data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(LocalPersistenceError, match="inside the application data directory"):
        LocalStateDatabase(link / "edgeos.sqlite3", allowed_root=root)


def test_local_state_checksum_failure_preserves_database(tmp_path: Path) -> None:
    state = database(tmp_path)
    state.save("test_state", {"amount": Decimal("1.23")})
    with sqlite3.connect(state.path) as connection:
        connection.execute(
            "update edgeos_local_state set payload_json = ? where namespace = ?",
            ('{"amount":"modified"}', "test_state"),
        )

    with pytest.raises(LocalPersistenceError, match="integrity check"):
        database(tmp_path).load("test_state")
    assert state.path.exists()


def test_corrupt_sqlite_is_preserved_and_startup_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "data" / "edgeos-local.sqlite3"
    path.parent.mkdir()
    original = b"not-a-sqlite-database\x00edgeos-user-data"
    path.write_bytes(original)

    with pytest.raises(LocalPersistenceError, match="could not be initialized"):
        LocalStateDatabase(path, allowed_root=tmp_path)

    assert path.read_bytes() == original


def test_unsupported_schema_is_preserved_and_rejected(tmp_path: Path) -> None:
    state = database(tmp_path)
    with sqlite3.connect(state.path) as connection:
        connection.execute("pragma user_version = 999")

    with pytest.raises(LocalPersistenceError, match="schema version is unsupported"):
        database(tmp_path)
    with sqlite3.connect(state.path) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 999


def test_failed_save_rolls_back_in_memory_mutations(
    tmp_path: Path, fixed_now, zero_fee, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = database(tmp_path)
    simulation = calculate_arbitrage(
        [
            decimal_offer("a", "home", "2.20", fixed_now, zero_fee),
            decimal_offer("b", "away", "2.00", fixed_now, zero_fee),
        ],
        Decimal("210"),
        now=fixed_now,
        expected_outcome_ids=("home", "away"),
    )
    ledger = LocalPaperLedger(state)
    store = LocalUserStore(state)

    def fail_save(
        namespace: str, value: object, *, expected_generation: int | None = None
    ) -> None:
        assert expected_generation is not None
        raise LocalPersistenceError("injected local save failure")

    monkeypatch.setattr(state, "save", fail_save)
    with pytest.raises(LocalPersistenceError, match="injected"):
        ledger.create_position("desktop-user", PositionCreate(simulation=simulation))
    with pytest.raises(LocalPersistenceError, match="injected"):
        store.update_settings("desktop-user", {"timezone": "America/Chicago"})

    assert ledger.positions("desktop-user") == ()
    assert ledger.entries("desktop-user") == ()
    assert store.settings("desktop-user")["timezone"] == "UTC"


@pytest.mark.skipif(__import__("os").name == "nt", reason="Windows uses inherited profile ACLs")
def test_local_database_permissions_are_owner_only(tmp_path: Path) -> None:
    state = database(tmp_path)

    assert state.path.stat().st_mode & 0o777 == 0o600
    assert state.path.parent.stat().st_mode & 0o777 == 0o700


def test_local_backup_uses_consistent_sqlite_copy_inside_user_data(tmp_path: Path) -> None:
    state = database(tmp_path)
    state.save("test_state", {"amount": Decimal("4.56")})
    target = tmp_path / "backups" / "edgeos-backup.sqlite3"

    state.backup(target)

    backup = LocalStateDatabase(target, allowed_root=tmp_path)
    assert backup.load("test_state") == {"amount": Decimal("4.56")}


def test_local_backup_rejects_destination_outside_user_data(tmp_path: Path) -> None:
    state = database(tmp_path)

    with pytest.raises(LocalPersistenceError, match="inside the application data directory"):
        state.backup(tmp_path.parent / "edgeos-backup-outside.sqlite3")
    with pytest.raises(LocalPersistenceError, match="must not replace the active database"):
        state.backup(state.path)


def test_portable_backup_round_trip_replaces_all_local_namespaces(tmp_path: Path) -> None:
    state = database(tmp_path)
    users = LocalUserStore(state)
    saved = LocalSavedOpportunityStore(state)
    users.update_settings("desktop-user", {"timezone": "America/Chicago"})
    users.add_watchlist(
        "desktop-user",
        WatchlistCreate(kind="provider", value="kalshi", label="Kalshi"),
    )
    saved.save("desktop-user", "demo-opportunity-001", favorited=True)
    portable = state.export_portable()

    users.update_settings("desktop-user", {"timezone": "Europe/London"})
    saved.delete("desktop-user", "demo-opportunity-001")
    restored = state.restore_portable(portable)

    assert restored == ("private_user_state", "saved_opportunities")
    reopened_users = LocalUserStore(state)
    reopened_saved = LocalSavedOpportunityStore(state)
    assert reopened_users.settings("desktop-user")["timezone"] == "America/Chicago"
    assert reopened_users.watchlists("desktop-user")[0]["value"] == "kalshi"
    assert reopened_saved.list("desktop-user")[0]["opportunity_id"] == "demo-opportunity-001"


def test_portable_restore_rejects_tampering_without_changing_current_state(tmp_path: Path) -> None:
    state = database(tmp_path)
    users = LocalUserStore(state)
    users.update_settings("desktop-user", {"timezone": "America/New_York"})
    portable = state.export_portable().replace(b"America/New_York", b"America/Los_Angeles")

    with pytest.raises(LocalPersistenceError, match="integrity check"):
        state.restore_portable(portable)

    assert LocalUserStore(state).settings("desktop-user")["timezone"] == "America/New_York"


def test_portable_restore_rejects_unsupported_or_oversize_documents(tmp_path: Path) -> None:
    state = database(tmp_path)

    with pytest.raises(LocalPersistenceError, match="not valid JSON"):
        state.restore_portable(b"{not-json")
    with pytest.raises(LocalPersistenceError, match="empty or exceeds"):
        state.restore_portable(b"")


def test_facade_created_before_restore_cannot_overwrite_restored_state(tmp_path: Path) -> None:
    state = database(tmp_path)
    stale_store = LocalUserStore(state)
    stale_store.update_settings("desktop-user", {"timezone": "America/Chicago"})
    portable = state.export_portable()
    stale_store.update_settings("desktop-user", {"timezone": "Europe/London"})

    state.restore_portable(portable)

    with pytest.raises(LocalPersistenceError, match="stale after restore"):
        stale_store.update_settings("desktop-user", {"timezone": "America/New_York"})
    assert LocalUserStore(state).settings("desktop-user")["timezone"] == "America/Chicago"

