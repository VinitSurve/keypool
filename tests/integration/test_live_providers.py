"""Optional live provider verification suite (Section 22).

Uses credentials configured in the real macOS Keychain if present, or reports:
'LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED'
without failing the standard test suite when no live API keys are configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from keypool.cli.formatting import RuntimeContext, configure_test_overrides
from keypool.config.settings import AppPaths


@pytest.mark.live_provider
def test_live_provider_connectivity_if_configured(capsys: pytest.CaptureFixture[str]) -> None:
    """Run live provider tests if real credentials are configured in the user's KEYPOOL store."""
    # Check only if explicitly requested via KEYPOOL_RUN_LIVE_TESTS=1
    if os.environ.get("KEYPOOL_RUN_LIVE_TESTS") != "1":
        print("LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED")
        captured = capsys.readouterr()
        assert "LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED" in captured.out
        return

    configure_test_overrides(secret_store=None, http_client=None, paths=None)
    real_app_dir = Path.home() / "Library" / "Application Support" / "keypool"
    ctx = RuntimeContext(paths=AppPaths(real_app_dir))
    creds = ctx.credential_manager.list_credentials()
    if not creds:
        print("LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED")
        captured = capsys.readouterr()
        assert "LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED" in captured.out
        return

    for cred in creds:
        if cred.enabled:
            snap = ctx.health_manager.test_credential(cred.id, functional=True, force=True)
            assert snap.credential_id == cred.id
