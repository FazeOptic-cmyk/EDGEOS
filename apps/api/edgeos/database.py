"""Small server-only PostgreSQL connection boundary.

The database URL is intentionally never formatted into exceptions or logs by
this module.  Callers receive a fresh transaction-scoped psycopg connection so
repository context managers commit on success and roll back on failure.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .config import settings


class DatabaseConfigurationError(RuntimeError):
    """Raised when a live server feature has no durable PostgreSQL store."""


def connection_factory(
    database_url: str | None = None,
    *,
    application_name: str = "edgeos-api",
) -> Any:
    """Return a new psycopg connection without ever exposing its DSN.

    ``DATABASE_SSLMODE`` defaults to ``require`` in live mode and ``prefer`` in
    development/demo mode, allowing the local Docker database while ensuring a
    hosted live deployment does not silently downgrade transport security.
    """

    dsn = database_url or settings.database_url
    if not dsn:
        raise DatabaseConfigurationError(
            "DATABASE_URL is required for durable live-mode storage"
        )
    try:
        return psycopg.connect(
            dsn,
            connect_timeout=8,
            application_name=application_name,
            sslmode=settings.database_sslmode,
        )
    except Exception as exc:
        # Do not allow a driver error containing the connection string to cross
        # this trust boundary.
        raise ConnectionError("PostgreSQL connection failed") from exc
