from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = os.getenv("EDGEOS_ENV", "development")
    mode: str = os.getenv("EDGEOS_MODE", "demo").strip().lower()
    supabase_url: str | None = os.getenv("SUPABASE_URL")
    # SUPABASE_SECRET_KEY is server-only and is never used as a JWT signing key.
    supabase_secret_key: str | None = os.getenv("SUPABASE_SECRET_KEY")
    legacy_jwt_secret: str | None = os.getenv("SUPABASE_JWT_SECRET")
    odds_api_key: str | None = os.getenv("THE_ODDS_API_KEY")
    database_url: str | None = os.getenv("DATABASE_URL")
    # The packaged desktop host supplies both paths from Electron's userData
    # directory. They are never accepted from a browser request.
    local_data_path: str | None = os.getenv("EDGEOS_LOCAL_DATA_PATH")
    local_data_root: str | None = os.getenv("EDGEOS_LOCAL_DATA_ROOT")
    # Base64-encoded 32-byte AES key. It belongs in a deployment secret
    # manager and is intentionally never persisted alongside ciphertext.
    credential_master_key: str | None = os.getenv("EDGEOS_CREDENTIAL_MASTER_KEY")
    credential_key_version: str = os.getenv("EDGEOS_CREDENTIAL_KEY_VERSION", "v1")
    database_sslmode: str = os.getenv(
        "DATABASE_SSLMODE",
        "require" if os.getenv("EDGEOS_MODE", "demo").strip().lower() == "live" else "prefer",
    )
    reviewer_user_ids: tuple[str, ...] = tuple(
        value.strip()
        for value in os.getenv("EDGEOS_REVIEWER_USER_IDS", "").split(",")
        if value.strip()
    )
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "EDGEOS_CORS_ORIGINS", os.getenv("WEB_ORIGIN", "http://localhost:3000")
        ).split(",")
        if origin.strip()
    )
    max_quote_age_seconds: int = int(
        os.getenv("QUOTE_STALE_AFTER_SECONDS", os.getenv("EDGEOS_MAX_QUOTE_AGE_SECONDS", "30"))
    )

    @property
    def demo_mode(self) -> bool:
        legacy = os.getenv("EDGEOS_DEMO_MODE")
        return _bool("EDGEOS_DEMO_MODE", self.mode != "live") if legacy is not None else self.mode != "live"

    @property
    def auth_configured(self) -> bool:
        return bool(self.supabase_url)


settings = Settings()

