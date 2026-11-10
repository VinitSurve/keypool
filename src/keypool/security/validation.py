"""Input, identifier, secret format, and filesystem security validation."""

from __future__ import annotations

import contextlib
import os
import re
import stat
from pathlib import Path

from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.security.redaction import register_secret_for_redaction

_CREDENTIAL_ID_RE = re.compile(r"^cred_[a-z0-9]{6,36}$")
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,48}$")
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,35}$")
_LOOKS_LIKE_SECRET_RE = re.compile(
    r"^(?:gsk_[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9_\-]{15,})$"
)


def validate_credential_id(credential_id: str) -> str:
    """Validate and normalize a credential identifier."""
    cleaned = credential_id.strip()
    if not _CREDENTIAL_ID_RE.match(cleaned):
        raise InvalidArgumentError(
            f"Invalid credential ID '{cleaned}'. Expected format like 'cred_a83f21'.",
            hint="Run 'keypool list' to view valid credential IDs.",
        )
    return cleaned


def validate_credential_name(name: str) -> str:
    """Validate a human-friendly credential label, preventing accidental key entry."""
    cleaned = name.strip()
    if not cleaned:
        raise InvalidArgumentError("Credential name cannot be empty.")
    if len(cleaned) > 120:
        raise InvalidArgumentError("Credential name must be 120 characters or fewer.")
    if any(ch in cleaned for ch in ("\n", "\r", "\0")):
        raise InvalidArgumentError("Credential name contains invalid control characters.")
    if _LOOKS_LIKE_SECRET_RE.match(cleaned):
        register_secret_for_redaction(cleaned)
        raise SecurityError(
            "The provided credential name appears to be an API key. Never use a secret as a credential label.",
            hint="Provide a descriptive label such as 'Groq Testing 01' instead.",
        )
    return cleaned


def validate_profile_name(profile_name: str) -> str:
    """Validate and normalize a profile name."""
    cleaned = profile_name.strip().lower()
    if not _PROFILE_NAME_RE.match(cleaned):
        raise InvalidArgumentError(
            f"Invalid profile name '{profile_name}'. Profile names must be 2-49 lowercase alphanumeric, '-' or '_' characters.",
            hint="Examples: 'testing', 'development', 'production', 'staging'.",
        )
    return cleaned


def validate_provider_name(provider_name: str) -> str:
    """Validate and normalize a provider identifier, resolving known aliases."""
    raw = provider_name.strip().lower()
    from keypool.providers.capabilities import normalize_provider_name

    cleaned = normalize_provider_name(raw)
    if not _PROVIDER_NAME_RE.match(cleaned):
        raise InvalidArgumentError(
            f"Invalid provider identifier '{provider_name}'.",
            hint="Supported providers: 'openai', 'anthropic', 'gemini', 'groq', 'mistral', 'deepseek', 'azure_openai', etc.",
        )
    return cleaned


def validate_api_key_input(secret: str, provider: str | None = None) -> tuple[str, list[str]]:
    """Validate basic API key input format without assuming format proves authenticity.

    Returns:
        Tuple of (cleaned_secret, list_of_advisory_warnings).
    """
    if not secret or not secret.strip():
        raise InvalidArgumentError("API key cannot be empty.")

    cleaned = secret.strip()
    register_secret_for_redaction(cleaned)

    if len(cleaned) < 8:
        raise InvalidArgumentError("API key is too short (minimum 8 characters).")
    if len(cleaned) > 512:
        raise InvalidArgumentError("API key exceeds maximum allowed length (512 characters).")
    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise InvalidArgumentError("API key must not contain whitespace or control characters.")

    warnings: list[str] = []
    if provider == "groq" and not cleaned.startswith("gsk_"):
        warnings.append(
            "Key does not start with standard Groq prefix 'gsk_'. Stored as entered; run 'keypool test' to verify authenticity."
        )
    elif provider == "gemini" and not cleaned.startswith("AIza"):
        warnings.append(
            "Key does not start with standard Google API key prefix 'AIza'. Stored as entered; run 'keypool test' to verify authenticity."
        )

    return cleaned, warnings


def ensure_secure_directory(path: Path) -> Path:
    """Create a directory if needed and enforce 0700 (rwx------) owner-only permissions."""
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRWXU)
    return path


def ensure_secure_file(path: Path) -> None:
    """Enforce 0600 (rw-------) owner-only permissions on a file if it exists."""
    if path.exists():
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def check_secure_permissions(path: Path) -> tuple[bool, str]:
    """Inspect whether a path is protected against group/other access on POSIX."""
    if not path.exists():
        return True, f"{path} does not exist yet"
    mode = stat.S_IMODE(path.stat().st_mode)
    group_other = mode & (stat.S_IRWXG | stat.S_IRWXO)
    if group_other != 0:
        return False, f"{path} has overly permissive mode {oct(mode)} (expected 0o700/0o600)"
    return True, f"{path} has restrictive permissions ({oct(mode)})"
