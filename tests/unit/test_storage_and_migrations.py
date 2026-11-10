"""Storage tests for SQLite initialization, migrations, rollback, and reconciliation."""

from __future__ import annotations

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1, FAKE_GROQ_KEY_2

from keypool.cli.formatting import RuntimeContext
from keypool.config.settings import AppPaths
from keypool.core.exceptions import KeychainError, StorageError
from keypool.storage.keychain import InMemorySecretStore
from keypool.storage.models import CredentialRecord, HealthState
from keypool.version import SCHEMA_VERSION


def test_sqlite_migrations_and_integrity(runtime_ctx: RuntimeContext) -> None:
    """Verify database schema version and SQLite integrity check."""
    assert runtime_ctx.db.get_schema_version() == SCHEMA_VERSION
    ok, msg = runtime_ctx.db.check_integrity()
    assert ok is True
    assert "ok" in msg.lower()


def test_transaction_rollback_on_failure(runtime_ctx: RuntimeContext) -> None:
    """Verify SQLite session_scope rolls back changes when an exception occurs."""
    cm = runtime_ctx.credential_manager
    cred = cm.add_credential(provider="groq", name="Rollback Test", secret=FAKE_GROQ_KEY_1)

    with pytest.raises(StorageError), runtime_ctx.db.session_scope() as session:
        rec = session.get(CredentialRecord, cred.id)
        assert rec is not None
        rec.name = "Should Be Rolled Back"
        raise RuntimeError("Simulated database error")

    after = cm.get_credential(cred.id)
    assert after.name == "Rollback Test"


def test_partial_creation_and_reconciliation(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify Keychain errors and missing Keychain items are handled and reconciled."""
    paths, secret_store = isolated_env
    ctx = RuntimeContext(paths=paths)
    cm = ctx.credential_manager

    # 1. If Keychain fails on set, no SQLite record is created
    secret_store.fail_on_set = KeychainError("Simulated Keychain lock")
    with pytest.raises(KeychainError):
        cm.add_credential(provider="groq", name="Should Fail", secret=FAKE_GROQ_KEY_1)
    assert cm.list_credentials() == []
    secret_store.fail_on_set = None

    # 2. Normal creation followed by external deletion from Keychain -> reconcile detects it
    cred = cm.add_credential(provider="groq", name="Orphan Test", secret=FAKE_GROQ_KEY_2)
    secret_store.delete_secret(cred.keychain_reference)

    reconcile = cm.reconcile_storage()
    assert cred.id in reconcile.missing_in_keychain
    updated = cm.get_credential(cred.id)
    assert updated.status == HealthState.KEYCHAIN_ERROR.value


def test_concurrent_credential_additions_no_duplicate_records(
    runtime_ctx: RuntimeContext,
) -> None:
    """Verify concurrent metadata operations succeed without duplicate records or race conditions."""
    from concurrent.futures import ThreadPoolExecutor

    cm = runtime_ctx.credential_manager

    def add_one(idx: int) -> str:
        c = cm.add_credential(
            provider="groq" if idx % 2 == 0 else "gemini",
            name=f"Concurrent Key {idx:02d}",
            secret=f"gsk_CONCURRENTFAKEKEY0000000000{idx:04d}",
        )
        return c.id

    with ThreadPoolExecutor(max_workers=4) as pool:
        created_ids = list(pool.map(add_one, range(12)))

    assert len(set(created_ids)) == 12
    all_creds = cm.list_credentials()
    assert len(all_creds) == 12


def test_sqlite_high_concurrency_nullpool_wal_stress(
    runtime_ctx: RuntimeContext,
) -> None:
    """Stress test multi-threaded SQLite read/write transactions under WAL mode and NullPool."""
    import concurrent.futures

    cm = runtime_ctx.credential_manager
    tracker = runtime_ctx.usage_tracker
    engine = runtime_ctx.selection_engine

    # Seed 3 credentials
    for i in range(3):
        cm.add_credential(
            provider="groq",
            name=f"Stress Key {i}",
            secret=f"gsk_STRESSFAKEKEY00000000000000{i:04d}",
            priority=100 - i * 10,
        )

    errors: list[Exception] = []

    def stress_worker(worker_id: int) -> None:
        try:
            for j in range(20):
                # Interleave selection (read/write check) and event recording (insert write)
                sel = engine.select_credential(provider="groq")
                assert sel.metadata.id
                tracker.record_event(
                    provider="groq",
                    event_type="gateway_completion",
                    status="success" if j % 5 != 0 else "failure",
                    credential_id=sel.metadata.id,
                    model="llama-3.3-70b-versatile",
                    duration_ms=12.5,
                )
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
        futures = [pool.submit(stress_worker, w) for w in range(25)]
        concurrent.futures.wait(futures)

    assert not errors, f"Encountered {len(errors)} concurrency errors: {errors[:3]}"

    report = tracker.get_usage_report(provider="groq")
    assert report.total_executions == 25 * 20
    assert report.successful_executions > 0
