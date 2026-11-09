"""Unit tests for credential lifecycle and profile management."""

from __future__ import annotations

import pytest
from tests.fixtures.provider_payloads import (
    FAKE_GEMINI_KEY_1,
    FAKE_GEMINI_KEY_2,
    FAKE_GROQ_KEY_1,
    FAKE_GROQ_KEY_2,
    FAKE_GROQ_KEY_3,
)

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import (
    CredentialNotFoundError,
    InvalidArgumentError,
    ProfileError,
    ProfileNotFoundError,
)
from keypool.storage.models import HealthState


def test_unlimited_credential_creation_and_listing(runtime_ctx: RuntimeContext) -> None:
    """Verify multiple credentials per provider can be added, listed, and inspected."""
    cm = runtime_ctx.credential_manager
    c1 = cm.add_credential(provider="groq", name="Groq Key 1", secret=FAKE_GROQ_KEY_1)
    c2 = cm.add_credential(provider="groq", name="Groq Key 2", secret=FAKE_GROQ_KEY_2)
    c3 = cm.add_credential(provider="groq", name="Groq Key 3", secret=FAKE_GROQ_KEY_3)
    g1 = cm.add_credential(provider="gemini", name="Gemini Key 1", secret=FAKE_GEMINI_KEY_1)
    g2 = cm.add_credential(provider="gemini", name="Gemini Key 2", secret=FAKE_GEMINI_KEY_2)

    assert len({c1.id, c2.id, c3.id, g1.id, g2.id}) == 5
    assert c1.status == HealthState.UNTESTED.value
    assert c1.enabled is True

    all_creds = cm.list_credentials()
    assert len(all_creds) == 5

    groq_creds = cm.list_credentials(provider="groq")
    assert len(groq_creds) == 3

    gemini_creds = cm.list_credentials(provider="gemini")
    assert len(gemini_creds) == 2


def test_credential_rename_update_enable_disable_remove(runtime_ctx: RuntimeContext) -> None:
    """Verify full credential lifecycle operations."""
    cm = runtime_ctx.credential_manager
    cred = cm.add_credential(provider="groq", name="Initial Name", secret=FAKE_GROQ_KEY_1)

    renamed = cm.rename_credential(cred.id, "Renamed Label")
    assert renamed.name == "Renamed Label"

    updated = cm.update_credential(cred.id, new_name="Updated Label", new_secret=FAKE_GROQ_KEY_2)
    assert updated.name == "Updated Label"
    assert cm.retrieve_secret(cred.id) == FAKE_GROQ_KEY_2

    disabled = cm.disable_credential(cred.id)
    assert disabled.enabled is False
    assert disabled.effective_status == HealthState.DISABLED.value

    enabled = cm.enable_credential(cred.id)
    assert enabled.enabled is True

    removed = cm.remove_credential(cred.id)
    assert removed.id == cred.id

    with pytest.raises(CredentialNotFoundError):
        cm.get_credential(cred.id)
    assert not runtime_ctx.secret_store.secret_exists(cred.keychain_reference)


def test_default_and_custom_profiles(runtime_ctx: RuntimeContext) -> None:
    """Verify built-in profiles, custom profile creation, assignment, unassignment, and deletion."""
    pm = runtime_ctx.profile_manager
    cm = runtime_ctx.credential_manager

    profiles = pm.list_profiles()
    names = [p.name for p in profiles]
    assert "testing" in names
    assert "development" in names
    assert "production" in names

    staging = pm.create_profile("staging", description="Staging environment")
    assert staging.name == "staging"
    assert staging.is_builtin is False
    assert staging.assigned_credential_ids == []

    cred = cm.add_credential(provider="groq", name="Groq Staging", secret=FAKE_GROQ_KEY_1)
    assigned = pm.assign_credential("staging", cred.id, priority=10)
    assert cred.id in assigned.assigned_credential_ids

    # Ensure testing profile did not gain access
    testing_prof = pm.get_profile("testing")
    assert cred.id not in testing_prof.assigned_credential_ids

    unassigned = pm.unassign_credential("staging", cred.id)
    assert cred.id not in unassigned.assigned_credential_ids

    # Built-in profiles cannot be deleted
    with pytest.raises(ProfileError):
        pm.delete_profile("testing")

    pm.delete_profile("staging")
    with pytest.raises(ProfileNotFoundError):
        pm.get_profile("staging")


def test_invalid_inputs_rejected(runtime_ctx: RuntimeContext) -> None:
    """Verify invalid provider, credential ID, and profile names raise InvalidArgumentError."""
    cm = runtime_ctx.credential_manager
    with pytest.raises(InvalidArgumentError):
        cm.add_credential(provider="unsupported_ai", name="Test", secret=FAKE_GROQ_KEY_1)

    with pytest.raises(InvalidArgumentError):
        cm.get_credential("invalid-id-format")

    with pytest.raises(InvalidArgumentError):
        runtime_ctx.profile_manager.create_profile("INVALID SPACES!")
