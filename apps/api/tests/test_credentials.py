from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from edgeos.credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialDecryptionError,
    mask_credentials,
    redact_secret_text,
)


def _cipher() -> CredentialCipher:
    return CredentialCipher(bytes(range(32)), key_version="test-v1")


def test_credentials_round_trip_is_bound_to_owner_and_provider() -> None:
    cipher = _cipher()
    payload = {"api_key": "secret-key-123456", "account": "research"}
    envelope = cipher.encrypt(user_id="user-a", provider_code="the_odds_api", credentials=payload)

    assert cipher.decrypt(
        user_id="user-a", provider_code="the_odds_api", envelope=envelope
    ) == payload
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(user_id="user-b", provider_code="the_odds_api", envelope=envelope)
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(user_id="user-a", provider_code="kalshi", envelope=envelope)


def test_cipher_rejects_tampering_wrong_key_version_and_bad_master_key() -> None:
    cipher = _cipher()
    envelope = cipher.encrypt(
        user_id="user-a", provider_code="the_odds_api", credentials={"api_key": "top-secret"}
    )
    tampered = replace(envelope, ciphertext=envelope.ciphertext[:-1] + bytes([envelope.ciphertext[-1] ^ 1]))
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(user_id="user-a", provider_code="the_odds_api", envelope=tampered)
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher(bytes(range(32)), key_version="v2").decrypt(
            user_id="user-a", provider_code="the_odds_api", envelope=envelope
        )
    with pytest.raises(CredentialConfigurationError):
        CredentialCipher.from_base64(base64.urlsafe_b64encode(b"short").decode())


def test_masks_and_logs_never_return_provider_secrets() -> None:
    credentials = {"api_key": "abcdefghijkl", "token": "xy"}
    assert mask_credentials(credentials) == {"api_key": "••••ijkl", "token": "••••"}

    message = "api_key=abcdefghijkl Authorization: Bearer token=xy https://x.test/?apiKey=abcdefghijkl"
    redacted = redact_secret_text(message, secrets=tuple(credentials.values()))
    assert "abcdefghijkl" not in redacted
    assert "token=xy" not in redacted
    assert "[REDACTED]" in redacted


def test_redaction_removes_unrecognized_auth_and_cookie_credentials() -> None:
    message = (
        "Authorization: Bearer unrelated.jwt.token Cookie=session=browser-secret; "
        "Authorization=Basic dXNlcjpwYXNzd29yZA== set-cookie='refresh=secret-value'"
    )

    redacted = redact_secret_text(message)

    assert "unrelated.jwt.token" not in redacted
    assert "browser-secret" not in redacted
    assert "dXNlcjpwYXNzd29yZA" not in redacted
    assert "secret-value" not in redacted
    assert redacted.count("[REDACTED]") >= 4
