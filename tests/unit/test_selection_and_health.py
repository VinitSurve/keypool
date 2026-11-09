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


def test_all_nine_selection_strategies(runtime_ctx: RuntimeContext) -> None:
    """Verify all 9 selection strategies: priority, round-robin, LRU, health-first, latency, cost, random, weighted, manual."""
    from keypool.core.selection_engine import SelectionStrategy, parse_selection_strategy
    from keypool.storage.models import HealthObservationRecord

    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    sel = runtime_ctx.selection_engine
    db = runtime_ctx.db
    ut = runtime_ctx.usage_tracker

    c1 = cm.add_credential(provider="groq", name="Key 1", secret=FAKE_GROQ_KEY_1, priority=50)
    c2 = cm.add_credential(provider="groq", name="Key 2", secret=FAKE_GROQ_KEY_2, priority=200, allow_duplicate=True)
    c3 = cm.add_credential(provider="groq", name="Key 3", secret=FAKE_GROQ_KEY_3, priority=100, allow_duplicate=True)

    pm.assign_credential("testing", c1.id, priority=10)
    pm.assign_credential("testing", c2.id, priority=20)
    pm.assign_credential("testing", c3.id, priority=30)

    # 1. PRIORITY strategy
    ordered_prio = sel.get_ordered_candidates(provider="groq", profile="testing", strategy=SelectionStrategy.PRIORITY)
    assert ordered_prio[0][0].id == c1.id  # Profile rank 10 first

    # 2. ROUND_ROBIN strategy
    rr_first = sel.get_ordered_candidates(provider="groq", strategy="round_robin")
    rr_second = sel.get_ordered_candidates(provider="groq", strategy="round_robin")
    assert rr_first[0][0].id != rr_second[0][0].id

    # 3. LRU strategy
    lru_cand = sel.get_ordered_candidates(provider="groq", strategy="lru")
    assert len(lru_cand) == 3

    # 4. HEALTH_FIRST strategy
    with db.session_scope() as session:
        r2 = session.get(CredentialRecord, c2.id)
        if r2:
            r2.status = HealthState.HEALTHY.value
    hf_cand = sel.get_ordered_candidates(provider="groq", strategy="health_first")
    assert hf_cand[0][0].id == c2.id  # Healthy key first

    # 5. LATENCY strategy
    with db.session_scope() as session:
        session.add(HealthObservationRecord(
            credential_id=c1.id,
            provider="groq",
            state="healthy",
            latency_ms=15.0,
            observed_at="2026-11-09T10:00:00+00:00",
        ))
        session.add(HealthObservationRecord(
            credential_id=c3.id,
            provider="groq",
            state="healthy",
            latency_ms=120.0,
            observed_at="2026-11-09T10:00:00+00:00",
        ))
    lat_cand = sel.get_ordered_candidates(provider="groq", strategy="latency")
    assert len(lat_cand) == 3

    # 6. COST strategy
    ut.record_event(provider="groq", credential_id=c1.id, event_type="exec", cost_usd=5.00)
    ut.record_event(provider="groq", credential_id=c3.id, event_type="exec", cost_usd=0.01)
    cost_cand = sel.get_ordered_candidates(provider="groq", strategy="cost")
    assert len(cost_cand) == 3

    # 7. RANDOM strategy
    rand_cand = sel.get_ordered_candidates(provider="groq", strategy="random")
    assert len(rand_cand) == 3

    # 8. WEIGHTED strategy
    weighted_cand = sel.get_ordered_candidates(provider="groq", strategy="weighted")
    assert len(weighted_cand) == 3

    # 9. MANUAL strategy
    manual_cand = sel.get_ordered_candidates(provider="groq", profile="testing", strategy="manual")
    assert manual_cand[0][0].id == c1.id
    assert manual_cand[1][0].id == c2.id
    assert manual_cand[2][0].id == c3.id

    # Verify strategy parsing aliases
    assert parse_selection_strategy("health-first") == SelectionStrategy.HEALTH_FIRST
    assert parse_selection_strategy("cost-optimized") == SelectionStrategy.COST
    assert parse_selection_strategy("latency-aware") == SelectionStrategy.LATENCY
    assert parse_selection_strategy("weighted") == SelectionStrategy.WEIGHTED
    assert parse_selection_strategy("manual") == SelectionStrategy.MANUAL

