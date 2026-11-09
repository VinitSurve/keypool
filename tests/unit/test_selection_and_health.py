"""Unit tests for deterministic multi-key selection, fallback, and health caching."""

from __future__ import annotations

import httpx
import pytest
from tests.fixtures.provider_payloads import (
    FAKE_GROQ_KEY_1,
    FAKE_GROQ_KEY_2,
    FAKE_GROQ_KEY_3,
    GROQ_CHAT_COMPLETION_PAYLOAD,
    GROQ_MODELS_SUCCESS_PAYLOAD,
)

from keypool.cli.formatting import RuntimeContext, configure_test_overrides
from keypool.config.settings import AppPaths
from keypool.core.exceptions import CredentialUnavailableError
from keypool.providers.base import ProviderErrorClassification
from keypool.storage.keychain import InMemorySecretStore
from keypool.storage.models import CredentialRecord, HealthState


def test_selection_prefers_healthy_and_uses_lru_tiebreaker(runtime_ctx: RuntimeContext) -> None:
    """Verify selection prefers healthy credentials and tie-breaks via Least-Recently-Used."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    sel = runtime_ctx.selection_engine

    c1 = cm.add_credential(provider="groq", name="Groq 1", secret=FAKE_GROQ_KEY_1)
    c2 = cm.add_credential(provider="groq", name="Groq 2", secret=FAKE_GROQ_KEY_2)
    c3 = cm.add_credential(provider="groq", name="Groq 3", secret=FAKE_GROQ_KEY_3)

    pm.assign_credential("testing", c1.id)
    pm.assign_credential("testing", c2.id)
    pm.assign_credential("testing", c3.id)

    # Mark c2 as healthy; c1 and c3 are untested
    with runtime_ctx.db.session_scope() as session:
        rec2 = session.get(CredentialRecord, c2.id)
        assert rec2 is not None
        rec2.status = HealthState.HEALTHY.value

    chosen1 = sel.select_credential(provider="groq", profile="testing")
    assert chosen1.metadata.id == c2.id

    # Mark c3 as healthy too; since c3 has never been used (last_used_at=None) and c2 was just used,
    # LRU tie-breaker must select c3!
    with runtime_ctx.db.session_scope() as session:
        rec3 = session.get(CredentialRecord, c3.id)
        assert rec3 is not None
        rec3.status = HealthState.HEALTHY.value

    chosen2 = sel.select_credential(provider="groq", profile="testing")
    assert chosen2.metadata.id == c3.id


def test_selection_excludes_disabled_auth_failed_and_cooldown(runtime_ctx: RuntimeContext) -> None:
    """Verify disabled, auth_failed, and cooldown credentials are never selected."""
    cm = runtime_ctx.credential_manager
    sel = runtime_ctx.selection_engine

    c1 = cm.add_credential(provider="groq", name="Disabled Key", secret=FAKE_GROQ_KEY_1)
    c2 = cm.add_credential(provider="groq", name="Auth Failed Key", secret=FAKE_GROQ_KEY_2)
    c3 = cm.add_credential(provider="groq", name="Cooldown Key", secret=FAKE_GROQ_KEY_3)

    cm.disable_credential(c1.id)
    with runtime_ctx.db.session_scope() as session:
        r2 = session.get(CredentialRecord, c2.id)
        r3 = session.get(CredentialRecord, c3.id)
        assert r2 is not None and r3 is not None
        r2.status = HealthState.AUTH_FAILED.value
        r3.status = HealthState.RATE_LIMITED.value
        r3.cooldown_until = "2099-01-01T00:00:00+00:00"

    with pytest.raises(CredentialUnavailableError):
        sel.select_credential(provider="groq")


def test_execute_with_fallback_respects_quota_domain_and_avoids_infinite_retries(
    runtime_ctx: RuntimeContext,
) -> None:
    """Verify fallback switches across eligible keys, respects quota_domain, and bounds retries."""
    cm = runtime_ctx.credential_manager
    sel = runtime_ctx.selection_engine

    # c1 and c2 share quota_domain 'org_a'; c3 is in 'org_b'
    c1 = cm.add_credential(
        provider="groq", name="Org A Key 1", secret=FAKE_GROQ_KEY_1, quota_domain="org_a"
    )
    c2 = cm.add_credential(
        provider="groq", name="Org A Key 2", secret=FAKE_GROQ_KEY_2, quota_domain="org_a"
    )
    c3 = cm.add_credential(
        provider="groq", name="Org B Key 1", secret=FAKE_GROQ_KEY_3, quota_domain="org_b"
    )

    attempted: list[str] = []

    def op(selected: object, _secret: str) -> str:
        from keypool.core.selection_engine import SelectedCredential

        assert isinstance(selected, SelectedCredential)
        attempted.append(selected.metadata.id)
        if selected.metadata.id == c1.id:
            raise RuntimeError("rate_limit_429")
        return f"success_with_{selected.metadata.id}"

    def classify(exc: Exception) -> ProviderErrorClassification | None:
        if "rate_limit_429" in str(exc):
            return ProviderErrorClassification(
                state=HealthState.RATE_LIMITED,
                error_category="rate_limited",
                http_status=429,
                sanitized_message="Rate limit hit",
                retry_after_seconds=120,
                is_retryable=True,
            )
        return None

    winner, output = sel.execute_with_fallback(
        provider="groq",
        operation=op,
        classify_failure=classify,
        max_attempts=3,
    )
    # Because c1 hit a rate limit in quota_domain 'org_a', c2 (also in 'org_a') is skipped and c3 wins!
    assert c2.id not in attempted
    assert winner.metadata.id == c3.id
    assert output == f"success_with_{c3.id}"


def test_health_manager_caching_and_transitions(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify HealthManager transitions states and caches observations without redundant calls."""
    paths, secret_store = isolated_env
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=GROQ_MODELS_SUCCESS_PAYLOAD)
        return httpx.Response(200, json=GROQ_CHAT_COMPLETION_PAYLOAD)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    configure_test_overrides(secret_store=secret_store, http_client=mock_client, paths=paths)
    ctx = RuntimeContext(paths=paths)

    cred = ctx.credential_manager.add_credential(
        provider="groq", name="Health Cache Key", secret=FAKE_GROQ_KEY_1
    )

    snap1 = ctx.health_manager.test_credential(cred.id, functional=True, force=True)
    assert snap1.state == HealthState.HEALTHY.value
    assert snap1.authentication_succeeded is True
    assert snap1.model_accessible is True
    assert snap1.request_completable is True
    assert call_count == 2  # /models + /chat/completions

    # Cached call with force=False should not hit the network
    snap2 = ctx.health_manager.test_credential(cred.id, functional=True, force=False)
    assert snap2.state == HealthState.HEALTHY.value
    assert snap2.cache_fresh is True
    assert call_count == 2
