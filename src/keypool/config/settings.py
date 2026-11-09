"""Validated application settings backed by TOML configuration."""

from __future__ import annotations

import contextlib
import logging
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator

from keypool.config.defaults import (
    DEFAULT_ACTIVE_PROFILE,
    DEFAULT_AUTH_FAILURE_TTL_SECONDS,
    DEFAULT_AUTH_SUCCESS_TTL_SECONDS,
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_COLOR_MODE,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_EXEC_ENV_MODE,
    DEFAULT_GEMINI_TEST_MODEL,
    DEFAULT_GROQ_TEST_MODEL,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL_CACHE_TTL_SECONDS,
    DEFAULT_OUTAGE_BACKOFF_SECONDS,
    DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
    KEYCHAIN_SERVICE_NAME,
    get_default_app_dir,
)
from keypool.core.exceptions import SecurityError, StorageError
from keypool.security.redaction import (
    RedactingFormatter,
    RedactingLogFilter,
    register_secret_for_redaction,
)
from keypool.security.validation import ensure_secure_directory, ensure_secure_file

_FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "token",
        "groq_api_key",
        "gemini_api_key",
        "google_api_key",
        "credentials",
        "keys",
    }
)

_SECRET_VALUE_RE = re.compile(
    r"(?:\bgsk_[A-Za-z0-9_\-]{10,}\b|\bAIza[0-9A-Za-z_\-]{15,}\b|\bsk-[A-Za-z0-9_\-]{12,}\b)"
)


class HealthCacheConfig(BaseModel):
    """Configurable health check cache policy settings."""

    model_config = ConfigDict(extra="forbid")

    auth_success_ttl_seconds: int = Field(default=DEFAULT_AUTH_SUCCESS_TTL_SECONDS, ge=0, le=86400)
    auth_failure_ttl_seconds: int = Field(default=DEFAULT_AUTH_FAILURE_TTL_SECONDS, ge=0, le=3600)
    rate_limit_cooldown_seconds: int = Field(
        default=DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS, ge=1, le=86400
    )
    outage_backoff_seconds: int = Field(default=DEFAULT_OUTAGE_BACKOFF_SECONDS, ge=1, le=3600)
    model_cache_ttl_seconds: int = Field(default=DEFAULT_MODEL_CACHE_TTL_SECONDS, ge=0, le=86400)


class NetworkConfig(BaseModel):
    """HTTP timeout and retry configuration."""

    model_config = ConfigDict(extra="forbid")

    http_timeout_seconds: float = Field(default=DEFAULT_HTTP_TIMEOUT_SECONDS, ge=1.0, le=120.0)
    connect_timeout_seconds: float = Field(default=DEFAULT_CONNECT_TIMEOUT_SECONDS, ge=0.5, le=60.0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0, le=5)
    backoff_base_seconds: float = Field(default=DEFAULT_BACKOFF_BASE_SECONDS, ge=0.1, le=10.0)


class ProviderDefaultsConfig(BaseModel):
    """Provider preference and functional test model defaults."""

    model_config = ConfigDict(extra="forbid")

    preferred_provider: str = Field(default="groq")
    groq_test_model: str = Field(default=DEFAULT_GROQ_TEST_MODEL)
    gemini_test_model: str = Field(default=DEFAULT_GEMINI_TEST_MODEL)


class ExecutionConfig(BaseModel):
    """Child process execution behavior."""

    model_config = ConfigDict(extra="forbid")

    env_mode: Literal["inherit", "controlled"] = Field(default=DEFAULT_EXEC_ENV_MODE)
    default_timeout_seconds: int = Field(default=0, ge=0, le=86400)


class GatewayConfig(BaseModel):
    """Local AI Gateway configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765, ge=1, le=65535)
    require_auth: bool = Field(default=True)
    default_strategy: str = Field(default="priority")
    fallback_enabled: bool = Field(default=True)
    max_fallback_attempts: int = Field(default=3, ge=1, le=10)


class KeypoolSettings(BaseModel):
    """Root configuration model for KEYPOOL."""

    model_config = ConfigDict(extra="forbid")

    default_profile: str = Field(default=DEFAULT_ACTIVE_PROFILE)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default=DEFAULT_LOG_LEVEL)
    color: Literal["auto", "always", "never"] = Field(default=DEFAULT_COLOR_MODE)
    keychain_service: str = Field(default=KEYCHAIN_SERVICE_NAME)
    health_cache: HealthCacheConfig = Field(default_factory=HealthCacheConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    providers: ProviderDefaultsConfig = Field(default_factory=ProviderDefaultsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @field_validator("default_profile")
    @classmethod
    def _normalize_profile(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("default_profile cannot be empty")
        return cleaned


def _inspect_for_forbidden_secrets(data: Any, path_prefix: str = "") -> None:
    """Recursively reject TOML configurations containing secret keys or API key patterns."""
    if isinstance(data, dict):
        for k, v in data.items():
            key_lower = str(k).strip().lower()
            full_key = f"{path_prefix}.{k}" if path_prefix else str(k)
            if key_lower in _FORBIDDEN_CONFIG_KEYS:
                if isinstance(v, str):
                    register_secret_for_redaction(v)
                raise SecurityError(
                    f"Forbidden secret key '{full_key}' found in config.toml. API credentials must never be stored in configuration files.",
                    hint="Remove the secret from config.toml and use 'keypool add <provider>' to store credentials in macOS Keychain.",
                )
            _inspect_for_forbidden_secrets(v, full_key)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            _inspect_for_forbidden_secrets(item, f"{path_prefix}[{idx}]")
    elif isinstance(data, str) and _SECRET_VALUE_RE.search(data):
        register_secret_for_redaction(data)
        raise SecurityError(
            f"Potential API key detected in config.toml at '{path_prefix}'. Never store credentials in TOML files.",
            hint="Store API credentials exclusively via 'keypool add <provider>'.",
        )


class AppPaths:
    """Resolved application filesystem paths for KEYPOOL."""

    def __init__(self, app_dir: Path | None = None) -> None:
        self.app_dir: Path = (app_dir or get_default_app_dir()).resolve()
        self.db_path: Path = self.app_dir / "keypool.db"
        self.config_path: Path = self.app_dir / "config.toml"
        self.logs_dir: Path = self.app_dir / "logs"
        self.log_file: Path = self.logs_dir / "keypool.log"
        self.gateway_token_path: Path = self.app_dir / "gateway_token"
        self.gateway_pid_path: Path = self.app_dir / "gateway.pid"
        self.gateway_log_path: Path = self.logs_dir / "gateway.log"

    def ensure_directories(self) -> None:
        """Create app_dir and logs_dir with restrictive 0700 permissions."""
        ensure_secure_directory(self.app_dir)
        ensure_secure_directory(self.logs_dir)


def load_settings(paths: AppPaths | None = None, *, write_default: bool = True) -> KeypoolSettings:
    """Load and validate KeypoolSettings from config.toml, creating defaults if missing."""
    resolved_paths = paths or AppPaths()
    resolved_paths.ensure_directories()

    if not resolved_paths.config_path.exists():
        settings = KeypoolSettings()
        if write_default:
            save_settings(settings, resolved_paths)
        return settings

    ensure_secure_file(resolved_paths.config_path)
    try:
        raw_bytes = resolved_paths.config_path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")
    except OSError as exc:
        raise StorageError(
            f"Failed to read configuration file {resolved_paths.config_path}: {exc}"
        ) from exc

    # Inspect raw text for accidental API keys before even parsing
    if _SECRET_VALUE_RE.search(raw_text):
        for match in _SECRET_VALUE_RE.findall(raw_text):
            register_secret_for_redaction(match)
        raise SecurityError(
            f"API key pattern detected in {resolved_paths.config_path}. Credentials must never be stored in config.toml."
        )

    try:
        parsed = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise StorageError(
            f"Malformed TOML in configuration file {resolved_paths.config_path}: {exc}",
            hint="Fix the TOML syntax or remove config.toml to regenerate defaults.",
        ) from exc

    _inspect_for_forbidden_secrets(parsed)

    try:
        return KeypoolSettings.model_validate(parsed)
    except Exception as exc:
        raise StorageError(
            f"Invalid configuration in {resolved_paths.config_path}: {exc}",
            hint="Check field names and values in config.toml.",
        ) from exc


def save_settings(settings: KeypoolSettings, paths: AppPaths | None = None) -> None:
    """Persist validated settings to config.toml with 0600 permissions."""
    resolved_paths = paths or AppPaths()
    resolved_paths.ensure_directories()
    payload = settings.model_dump(mode="json")
    _inspect_for_forbidden_secrets(payload)
    try:
        toml_str = tomli_w.dumps(payload)
        resolved_paths.config_path.write_text(toml_str, encoding="utf-8")
        ensure_secure_file(resolved_paths.config_path)
    except OSError as exc:
        raise StorageError(
            f"Failed to write configuration to {resolved_paths.config_path}: {exc}"
        ) from exc


def configure_logging(
    paths: AppPaths | None = None, level: str = DEFAULT_LOG_LEVEL
) -> logging.Logger:
    """Configure application file logging with mandatory secret redaction."""
    resolved_paths = paths or AppPaths()
    resolved_paths.ensure_directories()

    logger = logging.getLogger("keypool")
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False

    # Clear existing handlers to avoid duplicate entries on repeated invocations
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()

    try:
        file_handler = logging.FileHandler(resolved_paths.log_file, encoding="utf-8")
        ensure_secure_file(resolved_paths.log_file)
        file_handler.setLevel(numeric_level)
        file_handler.addFilter(RedactingLogFilter())
        file_handler.setFormatter(
            RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
    except OSError:
        # Fall back to NullHandler if filesystem is read-only
        logger.addHandler(logging.NullHandler())

    return logger
