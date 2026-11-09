"""Tests for Credential Intelligence (v2.0): duplicate detection, strategies, projects, budgets, models, backups."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1, FAKE_GROQ_KEY_2

from keypool.cli.formatting import RuntimeContext
from keypool.core.backup_manager import BackupManager
from keypool.core.budget_manager import BudgetManager
from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.core.model_catalog import ModelCatalogManager
from keypool.core.project_manager import ProjectManager
from keypool.core.selection_engine import SelectionStrategy


def test_duplicate_key_detection_and_fingerprinting(runtime_ctx: RuntimeContext) -> None:
    """Verify duplicate detection blocks identical keys unless allow_duplicate=True."""
    cm = runtime_ctx.credential_manager

    c1 = cm.add_credential(
        provider="groq",
        name="Key Alpha",
        secret=FAKE_GROQ_KEY_1,
        tags=["fast", "prod"],
        priority=150,
        description="Primary test key",
    )
    assert c1.secret_fingerprint is not None
    assert "fast" in c1.tags
    assert c1.priority == 150

    # Attempting to add duplicate key should raise InvalidArgumentError
    with pytest.raises(InvalidArgumentError) as exc_info:
        cm.add_credential(provider="groq", name="Key Duplicate", secret=FAKE_GROQ_KEY_1)
    assert "Duplicate credential detected" in str(exc_info.value)

    # Adding with allow_duplicate=True should succeed
    c2 = cm.add_credential(
        provider="groq",
        name="Key Duplicate Allowed",
        secret=FAKE_GROQ_KEY_1,
        allow_duplicate=True,
    )
    assert c2.id != c1.id
    assert c2.secret_fingerprint == c1.secret_fingerprint


def test_credential_tagging_and_rotation(runtime_ctx: RuntimeContext) -> None:
    """Verify tagging, untagging, and key rotation."""
    cm = runtime_ctx.credential_manager
    cred = cm.add_credential(provider="groq", name="Rotating Key", secret=FAKE_GROQ_KEY_1)

    cm.tag_credential(cred.id, ["test-tag", "v2-tag"])
    refreshed = cm.get_credential(cred.id)
    assert "test-tag" in refreshed.tags
    assert "v2-tag" in refreshed.tags

    cm.untag_credential(cred.id, ["test-tag"])
    refreshed2 = cm.get_credential(cred.id)
    assert "test-tag" not in refreshed2.tags
    assert "v2-tag" in refreshed2.tags

    # Rotate secret
    rotated = cm.rotate_credential(cred.id, FAKE_GROQ_KEY_2)
    assert rotated.last_rotated_at is not None
    assert cm.retrieve_secret(cred.id) == FAKE_GROQ_KEY_2


def test_selection_strategies(runtime_ctx: RuntimeContext) -> None:
    """Verify multiple selection strategies (Priority, Round Robin, LRU)."""
    cm = runtime_ctx.credential_manager
    sel = runtime_ctx.selection_engine

    c_low = cm.add_credential(provider="groq", name="Low Prio", secret=FAKE_GROQ_KEY_1, priority=10)
    c_high = cm.add_credential(
        provider="groq", name="High Prio", secret=FAKE_GROQ_KEY_2, priority=200, allow_duplicate=True
    )

    # Priority selection should pick c_high first and c_low last
    cand_prio = sel.get_ordered_candidates(provider="groq", strategy=SelectionStrategy.PRIORITY)
    assert cand_prio[0][0].id == c_high.id
    assert cand_prio[-1][0].id == c_low.id

    # Round Robin should cycle candidates
    cand_rr1 = sel.get_ordered_candidates(provider="groq", strategy=SelectionStrategy.ROUND_ROBIN)
    cand_rr2 = sel.get_ordered_candidates(provider="groq", strategy=SelectionStrategy.ROUND_ROBIN)
    assert cand_rr1[0][0].id != cand_rr2[0][0].id


def test_project_manager_registration_and_discovery(runtime_ctx: RuntimeContext) -> None:
    """Verify project registration, directory resolution, and tree ancestor discovery."""
    pm = ProjectManager(runtime_ctx.db)

    with tempfile.TemporaryDirectory() as tmp_dir:
        root_path = Path(tmp_dir)
        sub_dir = root_path / "src" / "deep"
        sub_dir.mkdir(parents=True)

        proj = pm.register_project(name="Workspace Alpha", root_dir=root_path, profile_name="development")
        assert proj.name == "Workspace Alpha"
        assert proj.profile_name == "development"

        # Discovering from sub_dir should find Workspace Alpha
        found = pm.discover_project(sub_dir)
        assert found is not None
        assert found.id == proj.id
        assert found.name == "Workspace Alpha"


def test_model_catalog_and_cost_estimation(runtime_ctx: RuntimeContext) -> None:
    """Verify model catalog queries, search, and pricing calculations."""
    mcm = ModelCatalogManager(runtime_ctx.db)

    models = mcm.list_models(provider="openai")
    assert len(models) >= 2
    assert any(m.model_id == "gpt-4o" for m in models)

    # Search
    search_results = mcm.search_models("claude")
    assert len(search_results) >= 1
    assert any("claude" in m.model_id.lower() for m in search_results)

    # Cost estimation: 1,000,000 input tokens at $2.50 + 1,000,000 output at $10.00 = $12.50
    cost = mcm.estimate_cost(provider="openai", model_id="gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 12.50


def test_budget_controls_and_threshold_evaluation(runtime_ctx: RuntimeContext) -> None:
    """Verify budget limits, period evaluation, and hard limit enforcement."""
    bm = BudgetManager(runtime_ctx.db)

    b = bm.set_budget(
        name="Monthly Groq Cap",
        amount_limit=10.0,
        scope_type="provider",
        scope_target="groq",
        period="monthly",
        hard_limit_enabled=True,
    )
    assert b.amount_limit == 10.0
    assert b.current_spend == 0.0
    assert b.is_exceeded is False

    # Simulate usage
    ut = runtime_ctx.usage_tracker
    ut.record_event(
        provider="groq",
        event_type="exec_success",
        cost_usd=12.50,
    )

    evaluated = bm.evaluate_budget(b.id)
    assert evaluated.current_spend == 12.50
    assert evaluated.is_exceeded is True

    blocked = bm.check_hard_limits(provider="groq")
    assert len(blocked) == 1
    assert blocked[0].id == b.id


def test_encrypted_backup_and_tamper_detection(runtime_ctx: RuntimeContext) -> None:
    """Verify AES-256-GCM backup creation, decryption, and tampering resistance."""
    cm = runtime_ctx.credential_manager
    bm = BackupManager(runtime_ctx.db, runtime_ctx.secret_store, cm)

    cm.add_credential(provider="groq", name="Key Backup Test", secret=FAKE_GROQ_KEY_1)

    with tempfile.TemporaryDirectory() as tmp_dir:
        backup_file = Path(tmp_dir) / "keypool_backup.kpb"
        count = bm.create_backup(backup_file, password="StrongMasterPassword123!")
        assert count >= 1
        assert backup_file.exists()

        # 1. Successful restore
        report = bm.restore_backup(backup_file, password="StrongMasterPassword123!", overwrite_existing=True)
        assert report.credentials_restored >= 1

        # 2. Wrong password -> SecurityError
        with pytest.raises(SecurityError):
            bm.restore_backup(backup_file, password="WrongPassword999!")

        # 3. Tampered ciphertext -> SecurityError (AES-GCM authentication failure)
        corrupted_bytes = bytearray(backup_file.read_bytes())
        corrupted_bytes[-5] ^= 0xFF  # Flip bit in ciphertext
        corrupt_file = Path(tmp_dir) / "corrupt.kpb"
        corrupt_file.write_bytes(corrupted_bytes)

        with pytest.raises(SecurityError):
            bm.restore_backup(corrupt_file, password="StrongMasterPassword123!")
