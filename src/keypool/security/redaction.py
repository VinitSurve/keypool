"""Centralized secret redaction for logs, exceptions, HTTP responses, and CLI output."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

REDACTED_TOKEN = "[REDACTED]"  # noqa: S105

# Regex patterns for known provider keys, Bearer tokens, query keys, and header values
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Groq keys (gsk_...)
    (re.compile(r"\bgsk_[A-Za-z0-9_\-]{10,}\b"), REDACTED_TOKEN),
    # Google Gemini / Google Cloud API keys (AIza...)
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{15,}\b"), REDACTED_TOKEN),
    # Generic sk- keys
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"), REDACTED_TOKEN),
    # Authorization Bearer headers
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-~+/]+=*"), r"\1" + REDACTED_TOKEN),
    # x-goog-api-key header values
    (re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)[^\s,;\"']+"), r"\1" + REDACTED_TOKEN),
    # URL query parameters ?key=... or &key=... or api_key=...
    (re.compile(r"(?i)([?&](?:api_)?key=)[^&\s\"']+"), r"\1" + REDACTED_TOKEN),
    # JSON/dict key-value pairs for api_key / secret / token
    (
        re.compile(
            r'(?i)("(?:api_key|apikey|secret|access_token|authorization|x-goog-api-key)"\s*:\s*")([^"]+)(")'
        ),
        r"\1" + REDACTED_TOKEN + r"\3",
    ),
]

_SENSITIVE_DICT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "token",
        "access_token",
        "authorization",
        "x-goog-api-key",
        "groq_api_key",
        "gemini_api_key",
        "google_api_key",
    }
)


class SecretRedactor:
    """Thread-safe registry and sanitizer that prevents secrets from leaking."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._known_secrets: set[str] = set()

    def register_secret(self, secret: str | None) -> None:
        """Register a secret in memory so any accidental appearance is scrubbed."""
        if not secret:
            return
        cleaned = secret.strip()
        if len(cleaned) >= 4:
            with self._lock:
                self._known_secrets.add(cleaned)

    def clear_registered_secrets(self) -> None:
        """Clear in-memory registered secrets (e.g. after test runs or child execution)."""
        with self._lock:
            self._known_secrets.clear()

    def redact(self, text: str | None) -> str:
        """Scrub all registered secrets and pattern-matched API keys from text."""
        if not text:
            return ""
        result = str(text)
        with self._lock:
            # Sort longest first so partial substrings don't leave fragments
            secrets_snapshot = sorted(self._known_secrets, key=len, reverse=True)
        for secret in secrets_snapshot:
            if secret and secret in result:
                result = result.replace(secret, REDACTED_TOKEN)
        for pattern, replacement in _SECRET_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    def redact_obj(self, obj: Any) -> Any:
        """Recursively scrub strings, dicts, lists, and tuples."""
        if obj is None:
            return None
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            sanitized: dict[Any, Any] = {}
            for k, v in obj.items():
                key_str = str(k).lower()
                if key_str in _SENSITIVE_DICT_KEYS:
                    sanitized[k] = REDACTED_TOKEN
                else:
                    sanitized[k] = self.redact_obj(v)
            return sanitized
        if isinstance(obj, list):
            return [self.redact_obj(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(self.redact_obj(item) for item in obj)
        return obj


_GLOBAL_REDACTOR = SecretRedactor()


def register_secret_for_redaction(secret: str | None) -> None:
    """Register a known secret value with the global redactor."""
    _GLOBAL_REDACTOR.register_secret(secret)


def clear_registered_secrets() -> None:
    """Clear all registered secrets from the global redactor."""
    _GLOBAL_REDACTOR.clear_registered_secrets()


def redact_text(text: str | None) -> str:
    """Redact any known or pattern-matched secrets from a string."""
    return _GLOBAL_REDACTOR.redact(text)


def redact_data(data: Any) -> Any:
    """Redact any known or pattern-matched secrets from structured data."""
    return _GLOBAL_REDACTOR.redact_obj(data)


def mask_secret_display(secret: str) -> str:
    """Return a non-reversible masked representation that never reveals the secret."""
    register_secret_for_redaction(secret)
    cleaned = secret.strip()
    if len(cleaned) <= 8:
        return "••••••••"
    prefix = cleaned[:4] if cleaned.startswith(("gsk_", "AIza")) else cleaned[:2]
    return f"{prefix}{'•' * 12}"


class RedactingLogFilter(logging.Filter):
    """Logging filter that scrubs log messages and arguments before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_text(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_text(str(a)) if isinstance(a, str) else a for a in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    """Logging formatter that guarantees formatted output including tracebacks is redacted."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_text(rendered)
