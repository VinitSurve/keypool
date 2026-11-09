"""Default configuration values and paths for KEYPOOL."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

DEFAULT_PROFILES: tuple[str, ...] = ("testing", "development", "production")
DEFAULT_ACTIVE_PROFILE: str = "testing"
SUPPORTED_PROVIDERS: tuple[str, ...] = ("groq", "gemini")

KEYCHAIN_SERVICE_NAME: str = "com.keypool.credentials"

# Cache TTL defaults (seconds)
DEFAULT_AUTH_SUCCESS_TTL_SECONDS: int = 900  # 15 minutes
DEFAULT_AUTH_FAILURE_TTL_SECONDS: int = 120  # 2 minutes
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS: int = 60  # 1 minute fallback if Retry-After absent
DEFAULT_OUTAGE_BACKOFF_SECONDS: int = 30  # 30 seconds initial backoff
DEFAULT_MODEL_CACHE_TTL_SECONDS: int = 900  # 15 minutes

# HTTP & retry defaults
DEFAULT_HTTP_TIMEOUT_SECONDS: float = 15.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 5.0
DEFAULT_MAX_RETRIES: int = 2
DEFAULT_BACKOFF_BASE_SECONDS: float = 0.5

# UI & logging defaults
DEFAULT_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
DEFAULT_COLOR_MODE: Literal["auto", "always", "never"] = "auto"

# Execution defaults
DEFAULT_EXEC_ENV_MODE: Literal["inherit", "controlled"] = "inherit"

# Preferred functional test models (used when present in discovered model list)
DEFAULT_GROQ_TEST_MODEL: str = "llama-3.1-8b-instant"
DEFAULT_GEMINI_TEST_MODEL: str = "gemini-2.5-flash"


def get_default_app_dir() -> Path:
    """Resolve the application data directory on macOS, honoring KEYPOOL_HOME if set."""
    env_override = os.environ.get("KEYPOOL_HOME", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()
    return (Path.home() / "Library" / "Application Support" / "keypool").resolve()
