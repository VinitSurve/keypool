"""Safe child environment construction and boundary enforcement for process execution."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from keypool.core.exceptions import SecurityError
from keypool.security.redaction import register_secret_for_redaction

# Baseline safe system environment keys preserved in strict/controlled mode
CONTROLLED_ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "COLORTERM",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
        "SYSTEMROOT",
        "__CF_USER_TEXT_ENCODING",
    }
)

# Known AI provider environment variables to strip before injecting the single target key
KNOWN_PROVIDER_ENV_VARS: frozenset[str] = frozenset(
    {
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)


def build_child_environment(
    *,
    provider_env_var: str,
    secret_value: str,
    mode: str = "inherit",
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct an isolated environment dictionary for a child process.

    Never mutates ``os.environ`` in the parent process.
    Injects ONLY ``provider_env_var`` with ``secret_value``.

    Args:
        provider_env_var: The single environment variable name defined by the provider adapter.
        secret_value: The secret retrieved from macOS Keychain.
        mode: Either ``"inherit"`` (default: copies parent env excluding other provider keys)
              or ``"controlled"`` (strict whitelist of baseline OS variables).
        base_env: Optional source environment mapping (defaults to ``os.environ``).
    """
    if not provider_env_var or not provider_env_var.strip():
        raise SecurityError("Provider environment variable name cannot be empty.")
    if not secret_value or not secret_value.strip():
        raise SecurityError("Cannot inject an empty secret into child environment.")

    register_secret_for_redaction(secret_value)
    source = dict(os.environ if base_env is None else base_env)

    if mode == "controlled":
        child_env = {k: v for k, v in source.items() if k in CONTROLLED_ALLOWED_ENV_KEYS}
        if "PATH" not in child_env:
            child_env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    elif mode == "inherit":
        child_env = {k: v for k, v in source.items() if k not in KNOWN_PROVIDER_ENV_VARS}
    else:
        raise SecurityError(
            f"Unsupported execution environment mode '{mode}'. Use 'inherit' or 'controlled'."
        )

    child_env[provider_env_var] = secret_value
    return child_env


def verify_command_contains_no_secret(command: Sequence[str], secret_value: str) -> None:
    """Ensure the secret is never passed on the command line (argv)."""
    register_secret_for_redaction(secret_value)
    cleaned = secret_value.strip()
    for arg in command:
        if cleaned and cleaned in arg:
            raise SecurityError(
                "Refusing to execute command: secret value detected in command-line arguments."
            )

