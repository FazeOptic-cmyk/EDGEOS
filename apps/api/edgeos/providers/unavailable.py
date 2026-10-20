"""Explicit adapter for configured providers that cannot be accessed legitimately."""

from __future__ import annotations

from .base import MarketDataAdapter, ProviderUnavailableError
from .models import (
    EventQuery,
    MarketQuery,
    OrderBookQuery,
    ProviderDescriptor,
    ProviderState,
)


class UnavailableProviderAdapter(MarketDataAdapter):
    def __init__(
        self,
        descriptor: ProviderDescriptor,
        *,
        reason: str,
        state: ProviderState = ProviderState.NOT_SUPPORTED,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.descriptor = descriptor
        self.reason = reason
        self.state = state

    def _raise(self):
        raise ProviderUnavailableError(self.reason)

    async def list_events(self, query: EventQuery | None = None):
        self._raise()

    async def list_markets(self, query: MarketQuery | None = None):
        self._raise()

    async def get_order_book(self, query: OrderBookQuery):
        self._raise()

    async def check_health(self):
        return self.health_snapshot(state=self.state, message=self.reason)

