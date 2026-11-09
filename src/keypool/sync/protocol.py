"""Cryptographic protocol and serialization primitives for multi-device synchronization."""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from keypool.core.exceptions import SecurityError

_SYNC_MAGIC_HEADER = b"KP_SYNC_v1"
_SALT_LENGTH_BYTES = 16
_NONCE_LENGTH_BYTES = 12
_PBKDF2_ITERATIONS = 600_000


def encrypt_sync_payload(data: dict[str, Any], passphrase: str) -> bytes:
    """Serialize and encrypt a synchronization payload using PBKDF2-HMAC-SHA256 and AES-256-GCM."""
    if not passphrase:
        raise SecurityError("A non-empty passphrase is required to encrypt synchronization bundle.")

    salt = os.urandom(_SALT_LENGTH_BYTES)
    nonce = os.urandom(_NONCE_LENGTH_BYTES)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    derived_key = kdf.derive(passphrase.encode("utf-8"))

    aesgcm = AESGCM(derived_key)
    plaintext_bytes = json.dumps(data, indent=None, sort_keys=True).encode("utf-8")

    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, associated_data=_SYNC_MAGIC_HEADER)

    # Format: MAGIC(10) + SALT(16) + NONCE(12) + CIPHERTEXT_WITH_TAG
    return _SYNC_MAGIC_HEADER + salt + nonce + ciphertext


def decrypt_sync_payload(bundle_bytes: bytes, passphrase: str) -> dict[str, Any]:
    """Decrypt and verify integrity of an encrypted synchronization bundle."""
    header_len = len(_SYNC_MAGIC_HEADER)
    if not bundle_bytes.startswith(_SYNC_MAGIC_HEADER):
        raise SecurityError("Invalid synchronization bundle header. Not a valid KEYPOOL sync file.")

    min_len = header_len + _SALT_LENGTH_BYTES + _NONCE_LENGTH_BYTES + 16
    if len(bundle_bytes) < min_len:
        raise SecurityError("Corrupt or truncated synchronization bundle.")

    offset = header_len
    salt = bundle_bytes[offset : offset + _SALT_LENGTH_BYTES]
    offset += _SALT_LENGTH_BYTES

    nonce = bundle_bytes[offset : offset + _NONCE_LENGTH_BYTES]
    offset += _NONCE_LENGTH_BYTES

    ciphertext = bundle_bytes[offset:]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    derived_key = kdf.derive(passphrase.encode("utf-8"))

    aesgcm = AESGCM(derived_key)
    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, associated_data=_SYNC_MAGIC_HEADER)
    except Exception as exc:
        raise SecurityError(
            "Decryption failed: invalid passphrase or corrupted payload.",
            hint="Verify that the exact same sync passphrase is used on all paired devices.",
        ) from exc

    try:
        parsed = json.loads(decrypted_bytes.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise SecurityError("Invalid synchronization payload format.")
        return parsed
    except json.JSONDecodeError as exc:
        raise SecurityError(f"Failed to parse decrypted synchronization payload: {exc}") from exc
