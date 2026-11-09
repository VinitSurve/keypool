"""Unit and integration tests for KEYPOOL Multi-Device Sync Platform (v4.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1
from typer.testing import CliRunner

from keypool.cli.app import app
from keypool.core.exceptions import SecurityError
from keypool.sync.manager import SyncManager
from keypool.sync.protocol import decrypt_sync_payload, encrypt_sync_payload


def test_sync_cryptographic_primitives() -> None:
    test_payload = {"foo": "bar", "credentials": [1, 2, 3]}
    passphrase = "my-secure-sync-passphrase"

    bundle = encrypt_sync_payload(test_payload, passphrase)
    assert bundle.startswith(b"KP_SYNC_v1")

    # Successful decryption
    decrypted = decrypt_sync_payload(bundle, passphrase)
    assert decrypted["foo"] == "bar"
    assert decrypted["credentials"] == [1, 2, 3]

    # Incorrect passphrase
    with pytest.raises(SecurityError, match="Decryption failed"):
        decrypt_sync_payload(bundle, "wrong-passphrase")

    # Corrupted bundle
    corrupt = bundle[:20] + b"garbage" + bundle[30:]
    with pytest.raises(SecurityError):
        decrypt_sync_payload(corrupt, passphrase)

    # Missing header
    with pytest.raises(SecurityError, match="Invalid synchronization bundle header"):
        decrypt_sync_payload(b"NON_SYNC_DATA", passphrase)


def test_device_enrollment_and_revocation(runtime_ctx: Any) -> None:
    mgr = SyncManager(runtime_ctx)

    # Initial device exists from migration
    devices = mgr.list_devices()
    assert len(devices) >= 1

    # Enroll new device
    new_dev = mgr.register_device("MacBook Pro M3 Workstation")
    assert new_dev["name"] == "MacBook Pro M3 Workstation"
    assert new_dev["is_revoked"] is False

    # Revoke device
    assert mgr.revoke_device(new_dev["id"]) is True
    updated = [d for d in mgr.list_devices() if d["id"] == new_dev["id"]][0]
    assert updated["is_revoked"] is True


def test_sync_bundle_export_and_import_with_conflicts(
    runtime_ctx: Any, isolated_env: Any, tmp_path: Path
) -> None:
    mgr = SyncManager(runtime_ctx)

    # Add credential
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Sync Test Groq Key",
        secret=FAKE_GROQ_KEY_1,
        priority=100,
        tags=["prod", "ai"],
    )

    passphrase = "test-sync-secret-pass"
    bundle_file = tmp_path / "sync.kp"
    bundle_bytes = mgr.export_sync_bundle(passphrase=passphrase, output_file=bundle_file)
    assert bundle_file.exists()
    assert len(bundle_bytes) > 100

    # Test import back into system
    res = mgr.import_sync_bundle(bundle_bytes=bundle_bytes, passphrase=passphrase)
    # Existing credential has identical fingerprint -> conflict resolved
    assert res["conflicts_resolved"] == 1


def test_cli_sync_commands(isolated_env: Any, tmp_path: Path) -> None:
    runner = CliRunner()

    # Status
    res_status = runner.invoke(app, ["sync", "status"])
    assert res_status.exit_code == 0
    assert "Current Device" in res_status.stdout

    # Register device
    res_reg = runner.invoke(app, ["sync", "device", "register", "iPad Pro Studio"])
    assert res_reg.exit_code == 0
    assert "Enrolled new device" in res_reg.stdout

    # Export bundle
    bundle_out = tmp_path / "export_test.kp"
    res_exp = runner.invoke(
        app,
        ["sync", "export", str(bundle_out), "--passphrase", "sync-pass-1234"],
    )
    assert res_exp.exit_code == 0
    assert "Successfully exported" in res_exp.stdout
    assert bundle_out.exists()

    # Import bundle
    res_imp = runner.invoke(
        app,
        ["sync", "import", str(bundle_out), "--passphrase", "sync-pass-1234"],
    )
    assert res_imp.exit_code == 0
    assert "Sync import complete" in res_imp.stdout
