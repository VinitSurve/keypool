"""KEYPOOL: Local-first macOS API credential manager."""

from __future__ import annotations

from keypool.sdk import AsyncKeypoolClient, KeypoolClient
from keypool.version import APP_DISPLAY_NAME, APP_NAME, SCHEMA_VERSION, __version__

__all__ = [
    "APP_DISPLAY_NAME",
    "APP_NAME",
    "AsyncKeypoolClient",
    "KeypoolClient",
    "SCHEMA_VERSION",
    "__version__",
]
