from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from .domain import AlertCreate, AlertUpdate, WatchlistCreate


class UserStore:
    """Demo-mode synchronized-shape store; production uses the Supabase tables."""

    def __init__(self) -> None:
        self._watchlists: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._alerts: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._settings: dict[str, dict[str, object]] = defaultdict(
            lambda: {
                "timezone": "UTC", "currency": "USD", "demo_mode": True,
                "default_allocation": 1000, "maximum_quote_age_seconds": 120,
                "verified_matches_only": True,
            }
        )
        self._alert_events: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()

    def watchlists(self, user_id: str) -> tuple[dict[str, object], ...]:
        return tuple(self._watchlists[user_id])

    def add_watchlist(self, user_id: str, request: WatchlistCreate) -> dict[str, object]:
        with self._lock:
            operation_key = (user_id, "watchlist", request.idempotency_key or "")
            if request.idempotency_key and operation_key in self._idempotency:
                return self._owned(self._watchlists[user_id], user_id, self._idempotency[operation_key])
            for item in self._watchlists[user_id]:
                if item["kind"] == request.kind and item["value"] == request.value:
                    return item
            item = {
                "id": str(uuid4()), "user_id": user_id, "kind": request.kind,
                "value": request.value, "label": request.label or request.value,
                "detail": None, "created_at": datetime.now(UTC), "is_demo": True,
            }
            self._watchlists[user_id].append(item)
            if request.idempotency_key:
                self._idempotency[operation_key] = str(item["id"])
            return item

    @staticmethod
    def _owned(items: list[dict[str, object]], user_id: str, item_id: str) -> dict[str, object]:
        for item in items:
            if item.get("id") == item_id and item.get("user_id") == user_id:
                return item
        raise KeyError(item_id)

    def delete_watchlist(self, user_id: str, item_id: str) -> None:
        with self._lock:
            item = self._owned(self._watchlists[user_id], user_id, item_id)
            self._watchlists[user_id].remove(item)

    def alerts(self, user_id: str) -> tuple[dict[str, object], ...]:
        return tuple(self._alerts[user_id])

    def add_alert(self, user_id: str, request: AlertCreate) -> dict[str, object]:
        with self._lock:
            operation_key = (user_id, "alert", request.idempotency_key or "")
            if request.idempotency_key and operation_key in self._idempotency:
                return self._owned(self._alerts[user_id], user_id, self._idempotency[operation_key])
            signature = (
                request.condition,
                request.threshold,
                request.minimum_net_profit,
                request.minimum_net_roi,
                request.minimum_liquidity,
                request.maximum_quote_age_seconds,
            )
            for item in self._alerts[user_id]:
                existing_signature = (
                    item["condition"],
                    item["threshold"],
                    item.get("minimum_net_profit"),
                    item.get("minimum_net_roi"),
                    item.get("minimum_liquidity"),
                    item.get("maximum_quote_age_seconds"),
                )
                if existing_signature == signature:
                    return item
            item = {
                "id": str(uuid4()), "user_id": user_id,
                "name": request.name or request.condition.replace("_", " ").title(),
                "condition": request.condition, "threshold": request.threshold,
                "minimum_net_profit": request.minimum_net_profit,
                "minimum_net_roi": request.minimum_net_roi,
                "minimum_liquidity": request.minimum_liquidity,
                "maximum_quote_age_seconds": request.maximum_quote_age_seconds,
                "enabled": request.enabled, "cooldown_seconds": request.cooldown_seconds,
                "created_at": datetime.now(UTC), "last_triggered_at": None, "is_demo": True,
            }
            self._alerts[user_id].append(item)
            if request.idempotency_key:
                self._idempotency[operation_key] = str(item["id"])
            return item

    def update_alert(self, user_id: str, alert_id: str, request: AlertUpdate) -> dict[str, object]:
        with self._lock:
            item = self._owned(self._alerts[user_id], user_id, alert_id)
            changes = request.model_dump(exclude_unset=True)
            item.update(changes)
            return item

    def delete_alert(self, user_id: str, alert_id: str) -> None:
        with self._lock:
            item = self._owned(self._alerts[user_id], user_id, alert_id)
            self._alerts[user_id].remove(item)

    def alert_events(self, user_id: str) -> tuple[dict[str, object], ...]:
        return tuple(self._alert_events[user_id])

    def settings(self, user_id: str) -> dict[str, object]:
        return dict(self._settings[user_id])

    def update_settings(self, user_id: str, values: dict[str, object]) -> dict[str, object]:
        allowed = {
            "timezone", "currency", "default_allocation",
            "maximum_quote_age_seconds", "demo_mode", "verified_matches_only",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported settings: {', '.join(sorted(unknown))}")
        with self._lock:
            self._settings[user_id].update(values)
            return dict(self._settings[user_id])

