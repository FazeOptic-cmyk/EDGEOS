"""Authenticated encryption and redaction for provider credentials.

The encryption key is supplied by the process secret manager and is never stored
with ciphertext.  AES-GCM authenticates both the payload and connection identity,
so a credential blob cannot be moved between users or providers undetected.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialConfigurationError(ValueError):
    """Raised when the server-side encryption configuration is unsafe."""


class CredentialDecryptionError(ValueError):
    """Raised for a corrupt, moved, or incorrectly keyed credential blob."""


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: str
    credential_fields: tuple[str, ...]


def _decode_key(value: str) -> bytes:
    candidate = value.strip()
    if not candidate:
        raise CredentialConfigurationError("credential master key is empty")
    # Secret managers commonly emit unpadded URL-safe base64.
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        key = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CredentialConfigurationError("credential master key must be base64") from exc
    if len(key) != 32:
        raise CredentialConfigurationError("credential master key must decode to exactly 32 bytes")
    return key


def _identity(user_id: str, provider_code: str, key_version: str) -> bytes:
    if not user_id or not provider_code or not key_version:
        raise ValueError("credential identity fields must be non-empty")
    return f"edgeos-provider-credential\x00{user_id}\x00{provider_code}\x00{key_version}".encode()


class CredentialCipher:
    """AES-256-GCM credential envelope bound to one user and provider."""

    def __init__(self, key: bytes, *, key_version: str = "v1") -> None:
        if len(key) != 32:
            raise CredentialConfigurationError("AES-256-GCM requires a 32-byte key")
        if not key_version.strip():
            raise CredentialConfigurationError("credential key version is required")
        self._cipher = AESGCM(bytes(key))
        self.key_version = key_version.strip()

    @classmethod
    def from_base64(cls, value: str, *, key_version: str = "v1") -> CredentialCipher:
        return cls(_decode_key(value), key_version=key_version)

    def encrypt(
        self,
        *,
        user_id: str,
        provider_code: str,
        credentials: Mapping[str, str],
    ) -> EncryptedCredential:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in credentials.items():
            name = raw_name.strip()
            if not name or not raw_value:
                raise ValueError("credential names and values must be non-empty")
            if len(name) > 80 or len(raw_value) > 16_384:
                raise ValueError("credential field exceeds the supported size")
            normalized[name] = raw_value
        if not normalized:
            raise ValueError("at least one credential field is required")

        plaintext = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            _identity(user_id, provider_code, self.key_version),
        )
        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self.key_version,
            credential_fields=tuple(sorted(normalized)),
        )

    def decrypt(
        self,
        *,
        user_id: str,
        provider_code: str,
        envelope: EncryptedCredential,
    ) -> dict[str, str]:
        if envelope.key_version != self.key_version:
            raise CredentialDecryptionError("credential key version is not available")
        try:
            plaintext = self._cipher.decrypt(
                envelope.nonce,
                envelope.ciphertext,
                _identity(user_id, provider_code, envelope.key_version),
            )
            decoded = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialDecryptionError("credential authentication failed") from exc
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()
        ):
            raise CredentialDecryptionError("credential payload has an invalid shape")
        if tuple(sorted(decoded)) != tuple(sorted(envelope.credential_fields)):
            raise CredentialDecryptionError("credential field manifest does not match")
        return decoded


def mask_credentials(credentials: Mapping[str, str]) -> dict[str, str]:
    """Return a field manifest that never reveals more than four trailing chars."""
    masked: dict[str, str] = {}
    for name, value in credentials.items():
        suffix = value[-4:] if len(value) >= 4 else ""
        masked[name] = f"••••{suffix}" if suffix else "••••"
    return masked


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|authorization|set-cookie|cookie|bearer|token|secret|password|passwd)"
    r"\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|secret|password)=)[^&#\s]+"
)
_SENSITIVE_AUTHORIZATION = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:basic|bearer)\s+[a-z0-9._~+\-/]+=*"
)
_SENSITIVE_BEARER = re.compile(
    r"(?i)\bbearer\s+[a-z0-9._~+\-/]+=*"
)


def redact_secret_text(message: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact known values and common secret-bearing error/query forms."""
    redacted = message
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _SENSITIVE_AUTHORIZATION.sub(
        lambda match: f"{match.group(1)}[REDACTED]", redacted
    )
    redacted = _SENSITIVE_BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    return _SENSITIVE_QUERY.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
