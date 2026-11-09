"""Shared test fixtures for KEYPOOL."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from keypool.cli.formatting import RuntimeContext, configure_test_overrides
from keypool.config.settings import AppPaths
from keypool.security.redaction import clear_registered_secrets
from keypool.storage.keychain import InMemorySecretStore


@pytest.fixture()
def isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[AppPaths, InMemorySecretStore]]:
    """Provide an isolated KEYPOOL_HOME directory and deterministic InMemorySecretStore."""
    app_dir = tmp_path / "keypool_app"
    monkeypatch.setenv("KEYPOOL_HOME", str(app_dir))
    paths = AppPaths(app_dir)
    secret_store = InMemorySecretStore()
    configure_test_overrides(secret_store=secret_store, http_client=None, paths=paths)
    clear_registered_secrets()
    try:
        yield paths, secret_store
    finally:
        configure_test_overrides(secret_store=None, http_client=None, paths=None)
        clear_registered_secrets()


@pytest.fixture()
def runtime_ctx(isolated_env: tuple[AppPaths, InMemorySecretStore]) -> RuntimeContext:
    """Return an initialized RuntimeContext bound to the isolated test environment."""
    paths, _store = isolated_env
    return RuntimeContext(paths=paths)
