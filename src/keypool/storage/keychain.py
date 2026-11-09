"""macOS Keychain secret storage abstraction and implementation.

Actual API secrets are stored exclusively in macOS Keychain and never in SQLite,
TOML files, logs, or plaintext fallbacks.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod

import keyring
import keyring.backend
from keyring.backends.macOS import Keyring as MacOSKeyringBackend
from keyring.errors import KeyringError, KeyringLocked, PasswordDeleteError, PasswordSetError

from keypool.config.defaults import KEYCHAIN_SERVICE_NAME
from keypool.core.exceptions import (
    KeychainAccessDeniedError,
    KeychainError,
    KeychainSecretMissingError,
    SecurityError,
)
from keypool.security.redaction import register_secret_for_redaction


class SecretStore(ABC):
    """Abstract interface for secret storage backends."""

    @abstractmethod
    def set_secret(self, reference: str, secret: str, *, allow_overwrite: bool = False) -> None:
        """Store a secret under a unique reference.

        Raises KeychainError if the reference already exists and allow_overwrite is False.
        """

    @abstractmethod
    def get_secret(self, reference: str) -> str:
        """Retrieve a secret by reference.

        Raises KeychainSecretMissingError if not found.
        """

    @abstractmethod
    def delete_secret(self, reference: str) -> None:
        """Delete a secret by reference.

        Succeeds idempotently if already absent, or raises KeychainError on failure.
        """

    @abstractmethod
    def secret_exists(self, reference: str) -> bool:
        """Return True if a non-empty secret exists for the given reference."""

    def replace_secret(self, reference: str, new_secret: str) -> None:
        """Safely replace an existing secret in the store."""
        self.set_secret(reference, new_secret, allow_overwrite=True)

    @abstractmethod
    def check_backend(self, *, probe_write: bool = False) -> tuple[bool, str]:
        """Verify backend availability and optionally test read/write capability."""


class MacOSKeychainStore(SecretStore):
    """Production secret store backed exclusively by macOS Keychain via keyring.

    Never falls back to plaintext files or non-Keychain backends.
    """

    def __init__(
        self,
        service_name: str = KEYCHAIN_SERVICE_NAME,
        backend: keyring.backend.KeyringBackend | None = None,
    ) -> None:
        if not service_name or not service_name.strip():
            raise SecurityError("Keychain service name cannot be empty.")
        self.service_name = service_name.strip()
        self._backend = backend

    def _resolve_backend(self) -> keyring.backend.KeyringBackend:
        """Ensure the active keyring backend is the native macOS Keychain."""
        backend = self._backend if self._backend is not None else keyring.get_keyring()
        backend_cls_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"

        # Enforce macOS Keychain and strictly forbid plaintext/file/null fallbacks
        if not isinstance(backend, MacOSKeyringBackend) and "macOS" not in backend_cls_name:
            raise SecurityError(
                f"Refusing to use non-macOS Keychain backend '{backend_cls_name}'. Plaintext or third-party fallback storage is strictly prohibited.",
                hint="Ensure KEYPOOL is running on macOS with Keychain services enabled.",
            )
        return backend

    @staticmethod
    def _validate_reference(reference: str) -> str:
        cleaned = reference.strip()
        if not cleaned:
            raise KeychainError("Keychain reference cannot be empty.")
        return cleaned

    def _classify_keyring_exception(
        self, exc: Exception, operation: str, reference: str
    ) -> KeychainError:
        """Translate low-level keyring exceptions into actionable domain errors."""
        msg = str(exc)
        lower = msg.lower()
        if isinstance(exc, KeyringLocked) or any(
            token in lower
            for token in (
                "locked",
                "denied",
                "auth",
                "user interaction is not allowed",
                "-25308",
                "-128",
                "errsecinteractionnotallowed",
                "errsecauthfailed",
            )
        ):
            return KeychainAccessDeniedError(
                f"macOS Keychain access was denied or Keychain is locked during {operation} for '{reference}': {msg}",
                hint="Unlock your macOS login Keychain or approve the Keychain access prompt.",
            )
        return KeychainError(
            f"macOS Keychain {operation} failed for reference '{reference}': {msg}",
            hint="Run 'keypool doctor' to verify macOS Keychain availability.",
        )

    def secret_exists(self, reference: str) -> bool:
        ref = self._validate_reference(reference)
        backend = self._resolve_backend()
        try:
            secret = backend.get_password(self.service_name, ref)
        except Exception as exc:
            raise self._classify_keyring_exception(exc, "lookup", ref) from exc
        if secret is not None:
            register_secret_for_redaction(secret)
            return len(secret) > 0
        return False

    def set_secret(self, reference: str, secret: str, *, allow_overwrite: bool = False) -> None:
        ref = self._validate_reference(reference)
        if not secret or not secret.strip():
            raise SecurityError("Refusing to store an empty secret in macOS Keychain.")
        cleaned_secret = secret.strip()
        register_secret_for_redaction(cleaned_secret)

        backend = self._resolve_backend()
        if not allow_overwrite and self.secret_exists(ref):
            raise KeychainError(
                f"Keychain reference '{ref}' already exists. Refusing to silently overwrite.",
                hint="Use 'keypool update <id>' to explicitly replace an existing credential.",
            )

        try:
            backend.set_password(self.service_name, ref, cleaned_secret)
        except (PasswordSetError, KeyringError, Exception) as exc:
            raise self._classify_keyring_exception(exc, "write", ref) from exc

    def get_secret(self, reference: str) -> str:
        ref = self._validate_reference(reference)
        backend = self._resolve_backend()
        try:
            secret = backend.get_password(self.service_name, ref)
        except (KeyringError, Exception) as exc:
            raise self._classify_keyring_exception(exc, "read", ref) from exc

        if secret is None or not secret.strip():
            raise KeychainSecretMissingError(
                f"No secret found in macOS Keychain for reference '{ref}'.",
                hint="The Keychain item may have been deleted externally. Use 'keypool update <id>' or 'keypool remove <id>'.",
            )
        cleaned = secret.strip()
        register_secret_for_redaction(cleaned)
        return cleaned

    def delete_secret(self, reference: str) -> None:
        ref = self._validate_reference(reference)
        backend = self._resolve_backend()
        try:
            existing = backend.get_password(self.service_name, ref)
            if existing is None:
                return
            register_secret_for_redaction(existing)
            backend.delete_password(self.service_name, ref)
        except PasswordDeleteError:
            # Already absent
            return
        except (KeyringError, Exception) as exc:
            raise self._classify_keyring_exception(exc, "delete", ref) from exc

    def replace_secret(self, reference: str, new_secret: str) -> None:
        ref = self._validate_reference(reference)
        if not new_secret or not new_secret.strip():
            raise SecurityError("Replacement secret cannot be empty.")
        cleaned_new = new_secret.strip()
        register_secret_for_redaction(cleaned_new)

        backend = self._resolve_backend()
        previous_secret: str | None = None
        try:
            previous_secret = backend.get_password(self.service_name, ref)
            if previous_secret:
                register_secret_for_redaction(previous_secret)
        except Exception as exc:
            raise self._classify_keyring_exception(exc, "pre-replace read", ref) from exc

        try:
            backend.set_password(self.service_name, ref, cleaned_new)
        except Exception as exc:
            # Attempt rollback if previous existed
            if previous_secret is not None:
                with contextlib.suppress(Exception):
                    backend.set_password(self.service_name, ref, previous_secret)
            raise self._classify_keyring_exception(exc, "replace", ref) from exc

    def check_backend(self, *, probe_write: bool = False) -> tuple[bool, str]:
        try:
            backend = self._resolve_backend()
        except Exception as exc:
            return False, str(exc)

        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        if not probe_write:
            return True, f"macOS Keychain available ({backend_name})"

        probe_ref = "keypool:__doctor_probe__"
        probe_val = "keypool_doctor_verification_token"
        try:
            backend.set_password(self.service_name, probe_ref, probe_val)
            read_back = backend.get_password(self.service_name, probe_ref)
            backend.delete_password(self.service_name, probe_ref)
            if read_back != probe_val:
                return False, "macOS Keychain probe read/write mismatch"
            return True, f"macOS Keychain read/write verified ({backend_name})"
        except Exception as exc:
            with contextlib.suppress(Exception):
                backend.delete_password(self.service_name, probe_ref)
            return False, f"macOS Keychain probe failed: {exc}"


class InMemorySecretStore(SecretStore):
    """Deterministic in-memory secret store strictly for isolated automated tests."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self.fail_on_set: Exception | None = None
        self.fail_on_get: Exception | None = None
        self.fail_on_delete: Exception | None = None

    def set_secret(self, reference: str, secret: str, *, allow_overwrite: bool = False) -> None:
        if self.fail_on_set is not None:
            raise self.fail_on_set
        ref = reference.strip()
        if not ref:
            raise KeychainError("Keychain reference cannot be empty.")
        if not secret or not secret.strip():
            raise SecurityError("Secret cannot be empty.")
        cleaned = secret.strip()
        register_secret_for_redaction(cleaned)
        if not allow_overwrite and ref in self._secrets:
            raise KeychainError(f"Keychain reference '{ref}' already exists.")
        self._secrets[ref] = cleaned

    def get_secret(self, reference: str) -> str:
        if self.fail_on_get is not None:
            raise self.fail_on_get
        ref = reference.strip()
        if ref not in self._secrets:
            raise KeychainSecretMissingError(
                f"No secret found in secret store for reference '{ref}'."
            )
        secret = self._secrets[ref]
        register_secret_for_redaction(secret)
        return secret

    def delete_secret(self, reference: str) -> None:
        if self.fail_on_delete is not None:
            raise self.fail_on_delete
        self._secrets.pop(reference.strip(), None)

    def secret_exists(self, reference: str) -> bool:
        if self.fail_on_get is not None:
            raise self.fail_on_get
        return reference.strip() in self._secrets

    def check_backend(self, *, probe_write: bool = False) -> tuple[bool, str]:
        if self.fail_on_set or self.fail_on_get:
            return False, "Mock secret store configured to fail"
        return True, "InMemorySecretStore ready"
