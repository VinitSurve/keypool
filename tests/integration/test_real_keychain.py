"""Real macOS Keychain integration tests (Section 6, 21).

Uses an isolated service identifier ('com.keypool.test.keychain_verification')
and cleans up all test items in a finally block so user credentials are never touched.
"""

from __future__ import annotations

import sys
import uuid

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1, FAKE_GROQ_KEY_2

from keypool.core.exceptions import KeychainError, KeychainSecretMissingError, SecurityError
from keypool.storage.keychain import MacOSKeychainStore


@pytest.mark.keychain
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain test requires macOS")
def test_real_macos_keychain_lifecycle() -> None:
    """Verify set, get, exists, overwrite protection, replace, and delete against real macOS Keychain."""
    service = "com.keypool.test.keychain_verification"
    store = MacOSKeychainStore(service_name=service)
    ref = f"keypool:test_{uuid.uuid4().hex[:8]}"

    try:
        ok, msg = store.check_backend(probe_write=True)
        assert ok is True, f"macOS Keychain probe failed: {msg}"

        assert store.secret_exists(ref) is False

        # 1. Store secret in real macOS Keychain
        store.set_secret(ref, FAKE_GROQ_KEY_1, allow_overwrite=False)
        assert store.secret_exists(ref) is True
        assert store.get_secret(ref) == FAKE_GROQ_KEY_1

        # 2. Prevent silent overwrite when allow_overwrite=False
        with pytest.raises(KeychainError):
            store.set_secret(ref, FAKE_GROQ_KEY_2, allow_overwrite=False)
        assert store.get_secret(ref) == FAKE_GROQ_KEY_1

        # 3. Safe replacement
        store.replace_secret(ref, FAKE_GROQ_KEY_2)
        assert store.get_secret(ref) == FAKE_GROQ_KEY_2

        # 4. Deletion
        store.delete_secret(ref)
        assert store.secret_exists(ref) is False
        with pytest.raises(KeychainSecretMissingError):
            store.get_secret(ref)

        # 5. Idempotent delete on already-removed reference
        store.delete_secret(ref)
    finally:
        store.delete_secret(ref)


@pytest.mark.keychain
def test_keychain_store_rejects_non_macos_backend() -> None:
    """Verify MacOSKeychainStore refuses any non-macOS or plaintext fallback backend."""
    import keyring.backend

    class FakePlaintextBackend(keyring.backend.KeyringBackend):
        priority = 1

        def __init__(self) -> None:
            pass

        def get_password(self, service: str, username: str) -> str | None:
            return None

        def set_password(self, service: str, username: str, password: str) -> None:
            pass

        def delete_password(self, service: str, username: str) -> None:
            pass

    store = MacOSKeychainStore(
        service_name="com.keypool.test.security",
        backend=FakePlaintextBackend(),
    )
    with pytest.raises(SecurityError):
        store.set_secret("keypool:cred_123456", FAKE_GROQ_KEY_1)


@pytest.mark.keychain
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain test requires macOS")
def test_complete_16_step_end_to_end_lifecycle_with_real_keychain(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the full 16-step Section 29 verification lifecycle using real macOS Keychain and SQLite."""
    import hashlib
    import os
    from pathlib import Path

    import httpx
    from tests.fixtures.provider_payloads import (
        GROQ_CHAT_COMPLETION_PAYLOAD,
        GROQ_MODELS_SUCCESS_PAYLOAD,
    )

    from keypool.cli.formatting import RuntimeContext, configure_test_overrides
    from keypool.config.settings import AppPaths
    from keypool.core.exceptions import CredentialNotFoundError, CredentialUnavailableError
    from keypool.storage.models import HealthState

    assert isinstance(tmp_path, Path)
    app_dir = tmp_path / "e2e_keypool_home"
    monkeypatch.setenv("KEYPOOL_HOME", str(app_dir))
    paths = AppPaths(app_dir)

    real_keychain = MacOSKeychainStore(service_name="com.keypool.test.e2e_lifecycle")

    def mock_groq_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=GROQ_MODELS_SUCCESS_PAYLOAD)
        return httpx.Response(200, json=GROQ_CHAT_COMPLETION_PAYLOAD)

    mock_http = httpx.Client(transport=httpx.MockTransport(mock_groq_handler))
    configure_test_overrides(secret_store=real_keychain, http_client=mock_http, paths=paths)
    ctx = RuntimeContext(paths=paths)
    created_ref: str | None = None

    try:
        # 1-3. Create credential -> Store in macOS Keychain -> Register metadata in SQLite
        meta = ctx.credential_manager.add_credential(
            provider="groq",
            name="E2E Real Keychain Groq",
            secret=FAKE_GROQ_KEY_1,
        )
        created_ref = meta.keychain_reference
        assert real_keychain.secret_exists(created_ref) is True
        assert real_keychain.get_secret(created_ref) == FAKE_GROQ_KEY_1

        # 4. List credential
        listed = ctx.credential_manager.list_credentials(provider="groq")
        assert len(listed) == 1
        assert listed[0].id == meta.id

        # 5. Test credential
        snap = ctx.health_manager.test_credential(meta.id, functional=True, force=True)
        assert snap.state == HealthState.HEALTHY.value

        # 6. Assign to testing profile
        prof = ctx.profile_manager.assign_credential("testing", meta.id)
        assert meta.id in prof.assigned_credential_ids

        # 7-9. Execute test process -> Verify child receives credential -> Verify parent unchanged
        parent_before = dict(os.environ)
        expected_hash = hashlib.sha256(FAKE_GROQ_KEY_1.encode("utf-8")).hexdigest()
        child_code = (
            "import hashlib, os, sys; "
            "val = os.environ.get('GROQ_API_KEY', ''); "
            "sys.stdout.write(hashlib.sha256(val.encode('utf-8')).hexdigest())"
        )
        exec_res, out, _err = ctx.execution_manager.execute(
            provider="groq",
            profile="testing",
            command=[sys.executable, "-c", child_code],
            capture_output=True,
        )
        assert exec_res.exit_code == 0
        assert out.strip() == expected_hash
        assert dict(os.environ) == parent_before
        assert "GROQ_API_KEY" not in os.environ

        # 10-11. Disable credential -> Verify it cannot be selected
        ctx.credential_manager.disable_credential(meta.id)
        with pytest.raises(CredentialUnavailableError):
            ctx.selection_engine.select_credential(provider="groq", profile="testing")

        # 12-13. Enable credential -> Verify selection works again
        ctx.credential_manager.enable_credential(meta.id)
        selected = ctx.selection_engine.select_credential(provider="groq", profile="testing")
        assert selected.metadata.id == meta.id

        # 14-16. Delete credential -> Verify Keychain item removed -> Verify metadata removed
        ctx.credential_manager.remove_credential(meta.id)
        assert real_keychain.secret_exists(created_ref) is False
        with pytest.raises(CredentialNotFoundError):
            ctx.credential_manager.get_credential(meta.id)
        assert ctx.credential_manager.list_credentials() == []
    finally:
        if created_ref:
            real_keychain.delete_secret(created_ref)
        configure_test_overrides(secret_store=None, http_client=None, paths=None)
