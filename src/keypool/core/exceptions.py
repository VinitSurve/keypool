"""Domain exception hierarchy and standardized CLI exit codes for KEYPOOL."""

from __future__ import annotations

from enum import IntEnum

from keypool.security.redaction import redact_text


class ExitCode(IntEnum):
    """Standardized CLI exit codes for KEYPOOL."""

    SUCCESS = 0
    GENERAL_ERROR = 1
    INVALID_ARGUMENTS = 2
    CREDENTIAL_UNAVAILABLE = 3
    PROVIDER_ERROR = 4
    STORAGE_ERROR = 5
    SECURITY_ERROR = 6


class KeypoolError(Exception):
    """Base exception for all KEYPOOL errors with automatic secret redaction."""

    exit_code: int = int(ExitCode.GENERAL_ERROR)
    error_code: str = "keypool_error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        safe_message = redact_text(message)
        safe_hint = redact_text(hint) if hint else None
        super().__init__(safe_message)
        self.message = safe_message
        self.hint = safe_hint

    def __str__(self) -> str:
        return redact_text(self.message)


class InvalidArgumentError(KeypoolError):
    """Raised when user-supplied CLI arguments, identifiers, or inputs are invalid."""

    exit_code = int(ExitCode.INVALID_ARGUMENTS)
    error_code = "invalid_arguments"


class CredentialNotFoundError(KeypoolError):
    """Raised when a requested credential ID does not exist."""

    exit_code = int(ExitCode.CREDENTIAL_UNAVAILABLE)
    error_code = "credential_not_found"


class CredentialUnavailableError(KeypoolError):
    """Raised when no eligible credential is available for selection or execution."""

    exit_code = int(ExitCode.CREDENTIAL_UNAVAILABLE)
    error_code = "credential_unavailable"


class ProfileError(KeypoolError):
    """Raised on profile validation, missing profile, or illegal profile operation."""

    exit_code = int(ExitCode.INVALID_ARGUMENTS)
    error_code = "profile_error"


class ProfileNotFoundError(ProfileError):
    """Raised when a profile does not exist."""

    exit_code = int(ExitCode.CREDENTIAL_UNAVAILABLE)
    error_code = "profile_not_found"


class ProviderError(KeypoolError):
    """Raised when a provider adapter encounters an error or unsupported provider is requested."""

    exit_code = int(ExitCode.PROVIDER_ERROR)
    error_code = "provider_error"


class StorageError(KeypoolError):
    """Raised when SQLite or filesystem storage operations fail."""

    exit_code = int(ExitCode.STORAGE_ERROR)
    error_code = "storage_error"


class KeychainError(StorageError):
    """Raised when macOS Keychain secret storage operations fail."""

    exit_code = int(ExitCode.STORAGE_ERROR)
    error_code = "keychain_error"


class KeychainSecretMissingError(KeychainError):
    """Raised when metadata exists in SQLite but the secret is missing from macOS Keychain."""

    exit_code = int(ExitCode.STORAGE_ERROR)
    error_code = "keychain_secret_missing"


class KeychainAccessDeniedError(KeychainError):
    """Raised when macOS Keychain denies access or is locked."""

    exit_code = int(ExitCode.SECURITY_ERROR)
    error_code = "keychain_access_denied"


class SecurityError(KeypoolError):
    """Raised when a security policy or boundary validation fails."""

    exit_code = int(ExitCode.SECURITY_ERROR)
    error_code = "security_error"


class ExecutionError(KeypoolError):
    """Raised when child process launch fails before execution completes."""

    exit_code = int(ExitCode.GENERAL_ERROR)
    error_code = "execution_error"
