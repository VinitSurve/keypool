"""Granular credential health monitoring, caching, and live testing (Section 9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete

from keypool.config.settings import KeypoolSettings
from keypool.core.credential_manager import CredentialManager, CredentialMetadata
from keypool.core.exceptions import KeychainError
from keypool.core.usage_tracker import UsageTracker
from keypool.providers.base import ProviderErrorClassification
from keypool.providers.registry import ProviderRegistry
from keypool.security.redaction import redact_text
from keypool.storage.database import DatabaseManager
from keypool.storage.keychain import SecretStore
from keypool.storage.models import (
    CredentialRecord,
    HealthObservationRecord,
    HealthState,
    ModelCacheRecord,
    TokenSource,
    utc_now_iso,
)


def _iso_after_seconds(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=max(0, seconds))).isoformat(timespec="seconds")


def is_iso_timestamp_future(iso_ts: str | None) -> bool:
    """Return True if an ISO 8601 UTC timestamp is strictly in the future."""
    if not iso_ts:
        return False
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt > datetime.now(UTC)
    except ValueError:
        return False


class CredentialHealthSnapshot(BaseModel):
    """Detailed 5-layer health status for a single credential."""

    model_config = ConfigDict(extra="forbid")

    credential_id: str
    name: str
    provider: str
    enabled: bool
    state: str
    state_label: str
    # 5 distinct health dimensions (Section 9)
    exists_in_db: bool = True
    keychain_retrievable: bool = False
    authentication_succeeded: bool | None = None
    model_accessible: bool | None = None
    request_completable: bool | None = None

    models_discovered: int = 0
    model_tested: str | None = None
    last_tested_at: str | None = None
    last_successful_at: str | None = None
    last_failure_at: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    sanitized_message: str | None = None
    cooldown_until: str | None = None
    cache_expires_at: str | None = None
    cache_fresh: bool = False
    latency_ms: float | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "name": self.name,
            "provider": self.provider,
            "enabled": self.enabled,
            "state": self.state,
            "state_label": self.state_label,
            "checks": {
                "credential_exists": self.exists_in_db,
                "keychain_retrievable": self.keychain_retrievable,
                "authentication_succeeded": self.authentication_succeeded,
                "model_accessible": self.model_accessible,
                "request_completable": self.request_completable,
            },
            "models_discovered": self.models_discovered,
            "model_tested": self.model_tested,
            "last_tested_at": self.last_tested_at,
            "last_successful_at": self.last_successful_at,
            "last_failure_at": self.last_failure_at,
            "error_category": self.error_category,
            "http_status": self.http_status,
            "sanitized_message": redact_text(self.sanitized_message)
            if self.sanitized_message
            else None,
            "cooldown_until": self.cooldown_until,
            "cache_expires_at": self.cache_expires_at,
            "cache_fresh": self.cache_fresh,
            "latency_ms": self.latency_ms,
        }


class HealthManager:
    """Evaluates, caches, and updates granular credential health observations."""

    def __init__(
        self,
        db: DatabaseManager,
        secret_store: SecretStore,
        provider_registry: ProviderRegistry,
        credential_manager: CredentialManager,
        settings: KeypoolSettings | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._registry = provider_registry
        self._cred_manager = credential_manager
        self._settings = settings or KeypoolSettings()
        self._usage = usage_tracker or UsageTracker(db)

    def _compute_cache_ttl(
        self,
        state: HealthState,
        retry_after_seconds: int | None = None,
    ) -> tuple[str, str | None]:
        """Compute (cache_expires_at, cooldown_until) based on state and configured TTLs."""
        cfg = self._settings.health_cache
        if state == HealthState.HEALTHY:
            return _iso_after_seconds(cfg.auth_success_ttl_seconds), None
        if state in (HealthState.AUTH_FAILED, HealthState.PERMISSION_DENIED):
            ttl = cfg.auth_failure_ttl_seconds
            exp = _iso_after_seconds(ttl)
            return exp, exp
        if state in (HealthState.RATE_LIMITED, HealthState.QUOTA_Exhausted):
            wait_sec = retry_after_seconds or cfg.rate_limit_cooldown_seconds
            exp = _iso_after_seconds(wait_sec)
            return exp, exp
        if state in (HealthState.PROVIDER_UNAVAILABLE, HealthState.NETWORK_ERROR):
            wait_sec = retry_after_seconds or cfg.outage_backoff_seconds
            exp = _iso_after_seconds(wait_sec)
            return exp, exp
        if state == HealthState.MODEL_UNAVAILABLE:
            exp = _iso_after_seconds(cfg.model_cache_ttl_seconds)
            return exp, None
        return _iso_after_seconds(cfg.auth_failure_ttl_seconds), None

    def test_credential(
        self,
        credential_id: str,
        *,
        functional: bool = True,
        force: bool = True,
    ) -> CredentialHealthSnapshot:
        """Execute a live health check on a single credential and persist observations."""
        meta = self._cred_manager.get_credential(credential_id)

        # If not forcing and cache is fresh, return cached snapshot
        if not force and is_iso_timestamp_future(meta.cache_expires_at):
            return self.get_cached_snapshot(meta)

        now = utc_now_iso()

        # Step 1: Retrieve secret from macOS Keychain
        try:
            secret = self._cred_manager.retrieve_secret(meta.id)
        except KeychainError as exc:
            msg = redact_text(str(exc))
            self._persist_observation(
                credential_id=meta.id,
                provider=meta.provider,
                state=HealthState.KEYCHAIN_ERROR,
                error_category="keychain_error",
                http_status=None,
                sanitized_message=msg,
                model_tested=None,
                latency_ms=0.0,
                retry_after_seconds=None,
            )
            return CredentialHealthSnapshot(
                credential_id=meta.id,
                name=meta.name,
                provider=meta.provider,
                enabled=meta.enabled,
                state=(
                    HealthState.DISABLED.value
                    if not meta.enabled
                    else HealthState.KEYCHAIN_ERROR.value
                ),
                state_label=(
                    HealthState.DISABLED.label
                    if not meta.enabled
                    else HealthState.KEYCHAIN_ERROR.label
                ),
                exists_in_db=True,
                keychain_retrievable=False,
                authentication_succeeded=None,
                model_accessible=None,
                request_completable=False,
                last_tested_at=now,
                last_failure_at=now,
                error_category="keychain_error",
                sanitized_message=msg,
            )

        # Step 2: Run provider adapter test_connection
        adapter = self._registry.get(meta.provider)
        preferred_model = (
            self._settings.providers.groq_test_model
            if adapter.name == "groq"
            else self._settings.providers.gemini_test_model
            if adapter.name == "gemini"
            else None
        )
        conn_res = adapter.test_connection(
            secret,
            functional=functional,
            preferred_model=preferred_model,
        )

        # Step 3: Update per-model availability cache if models were discovered
        if conn_res.models:
            self._update_model_cache(meta.id, meta.provider, conn_res.models)

        error_cat = conn_res.classification.error_category if conn_res.classification else None
        http_status = conn_res.classification.http_status if conn_res.classification else 200
        retry_after = (
            conn_res.classification.retry_after_seconds if conn_res.classification else None
        )

        cache_exp, cooldown_exp = self._persist_observation(
            credential_id=meta.id,
            provider=meta.provider,
            state=conn_res.state,
            error_category=error_cat,
            http_status=http_status,
            sanitized_message=conn_res.sanitized_message,
            model_tested=conn_res.model_tested,
            latency_ms=conn_res.latency_ms,
            retry_after_seconds=retry_after,
        )

        self._usage.record_event(
            provider=meta.provider,
            credential_id=meta.id,
            event_type="health_test",
            status="success" if conn_res.success else "failure",
            http_status=http_status,
            is_rate_limit=(conn_res.state == HealthState.RATE_LIMITED or http_status == 429),
            model=conn_res.model_tested,
            observed_tokens=conn_res.observed_tokens,
            token_source=(
                TokenSource.PROVIDER_REPORTED
                if conn_res.observed_tokens is not None
                else TokenSource.UNKNOWN
            ),
            duration_ms=conn_res.latency_ms,
            summary=(
                f"Credential {meta.id} tested successfully ({adapter.display_name})"
                if conn_res.success
                else f"Credential {meta.id} test failed: {conn_res.state.label}"
            ),
        )

        effective_state = HealthState.DISABLED if not meta.enabled else conn_res.state
        model_ok: bool | None = None
        if conn_res.auth_ok:
            model_ok = (
                conn_res.models_discovered > 0 and conn_res.state != HealthState.MODEL_UNAVAILABLE
            )

        req_ok: bool | None = (
            conn_res.functional_ok if conn_res.functional_tested else conn_res.success
        )

        return CredentialHealthSnapshot(
            credential_id=meta.id,
            name=meta.name,
            provider=meta.provider,
            enabled=meta.enabled,
            state=effective_state.value,
            state_label=effective_state.label,
            exists_in_db=True,
            keychain_retrievable=True,
            authentication_succeeded=conn_res.auth_ok,
            model_accessible=model_ok,
            request_completable=req_ok,
            models_discovered=conn_res.models_discovered,
            model_tested=conn_res.model_tested,
            last_tested_at=now,
            last_successful_at=now if conn_res.success else meta.last_successful_at,
            last_failure_at=now if not conn_res.success else meta.last_failure_at,
            error_category=error_cat,
            http_status=http_status,
            sanitized_message=redact_text(conn_res.sanitized_message),
            cooldown_until=cooldown_exp,
            cache_expires_at=cache_exp,
            cache_fresh=True,
            latency_ms=conn_res.latency_ms,
        )

    def record_runtime_failure(
        self,
        credential_id: str,
        classification: ProviderErrorClassification,
    ) -> None:
        """Record an error classification observed during selection/fallback."""
        meta = self._cred_manager.get_credential(credential_id)
        self._persist_observation(
            credential_id=meta.id,
            provider=meta.provider,
            state=classification.state,
            error_category=classification.error_category,
            http_status=classification.http_status,
            sanitized_message=classification.sanitized_message,
            model_tested=None,
            latency_ms=None,
            retry_after_seconds=classification.retry_after_seconds,
        )
        if classification.http_status == 429 or classification.state == HealthState.RATE_LIMITED:
            self._usage.record_event(
                provider=meta.provider,
                credential_id=meta.id,
                event_type="rate_limit_429",
                status="warning",
                http_status=429,
                is_rate_limit=True,
                summary=f"Observed HTTP 429 rate limit on {meta.id}",
            )

    def _update_model_cache(
        self,
        credential_id: str,
        provider: str,
        models: list[Any],
    ) -> None:
        now = utc_now_iso()
        exp = _iso_after_seconds(self._settings.health_cache.model_cache_ttl_seconds)
        with self._db.session_scope() as session:
            session.execute(
                delete(ModelCacheRecord).where(ModelCacheRecord.credential_id == credential_id)
            )
            for m in models:
                session.add(
                    ModelCacheRecord(
                        credential_id=credential_id,
                        provider=provider,
                        model_id=m.id,
                        display_name=m.display_name,
                        available=True,
                        observed_at=now,
                        expires_at=exp,
                    )
                )

    def _persist_observation(
        self,
        *,
        credential_id: str,
        provider: str,
        state: HealthState,
        error_category: str | None,
        http_status: int | None,
        sanitized_message: str | None,
        model_tested: str | None,
        latency_ms: float | None,
        retry_after_seconds: int | None,
    ) -> tuple[str, str | None]:
        now = utc_now_iso()
        cache_exp, cooldown_exp = self._compute_cache_ttl(state, retry_after_seconds)
        safe_msg = redact_text(sanitized_message) if sanitized_message else None

        with self._db.session_scope() as session:
            rec = session.get(CredentialRecord, credential_id)
            if rec is not None:
                rec.status = state.value
                rec.last_tested_at = now
                rec.updated_at = now
                rec.cache_expires_at = cache_exp
                rec.cooldown_until = cooldown_exp
                rec.last_tested_model = model_tested or rec.last_tested_model
                if state == HealthState.HEALTHY:
                    rec.last_successful_at = now
                    rec.last_error_category = None
                    rec.last_http_status = 200
                    rec.last_error_message = safe_msg
                else:
                    rec.last_failure_at = now
                    rec.last_error_category = error_category
                    rec.last_http_status = http_status
                    rec.last_error_message = safe_msg

            session.add(
                HealthObservationRecord(
                    credential_id=credential_id,
                    provider=provider,
                    observed_at=now,
                    state=state.value,
                    error_category=error_category,
                    http_status=http_status,
                    sanitized_message=safe_msg,
                    model_tested=model_tested,
                    latency_ms=latency_ms,
                    cache_expires_at=cache_exp,
                )
            )
        return cache_exp, cooldown_exp

    def get_cached_snapshot(self, meta: CredentialMetadata) -> CredentialHealthSnapshot:
        """Build a 5-layer health snapshot from cached SQLite metadata and local Keychain check."""
        keychain_ok = False
        try:
            keychain_ok = self._secret_store.secret_exists(meta.keychain_reference)
        except Exception:
            keychain_ok = False

        raw_state_str = meta.status
        if not keychain_ok:
            raw_state_str = HealthState.KEYCHAIN_ERROR.value

        try:
            raw_state = HealthState(raw_state_str)
        except ValueError:
            raw_state = HealthState.UNKNOWN

        effective_state = HealthState.DISABLED if not meta.enabled else raw_state
        cache_fresh = is_iso_timestamp_future(meta.cache_expires_at)

        auth_ok: bool | None = None
        model_ok: bool | None = None
        req_ok: bool | None = None

        if raw_state == HealthState.HEALTHY:
            auth_ok = True
            model_ok = True
            req_ok = True
        elif raw_state in (HealthState.AUTH_FAILED, HealthState.PERMISSION_DENIED):
            auth_ok = False
            model_ok = False
            req_ok = False
        elif raw_state == HealthState.MODEL_UNAVAILABLE:
            auth_ok = True
            model_ok = False
            req_ok = False
        elif raw_state in (HealthState.RATE_LIMITED, HealthState.QUOTA_Exhausted):
            auth_ok = True
            model_ok = True
            req_ok = False
        elif raw_state in (HealthState.PROVIDER_UNAVAILABLE, HealthState.NETWORK_ERROR):
            req_ok = False

        return CredentialHealthSnapshot(
            credential_id=meta.id,
            name=meta.name,
            provider=meta.provider,
            enabled=meta.enabled,
            state=effective_state.value,
            state_label=effective_state.label,
            exists_in_db=True,
            keychain_retrievable=keychain_ok,
            authentication_succeeded=auth_ok,
            model_accessible=model_ok,
            request_completable=req_ok,
            models_discovered=len(meta.cached_models),
            model_tested=meta.last_tested_model,
            last_tested_at=meta.last_tested_at,
            last_successful_at=meta.last_successful_at,
            last_failure_at=meta.last_failure_at,
            error_category=meta.last_error_category,
            http_status=meta.last_http_status,
            sanitized_message=meta.last_error_message,
            cooldown_until=meta.cooldown_until,
            cache_expires_at=meta.cache_expires_at,
            cache_fresh=cache_fresh,
        )

    def get_health_report(
        self,
        *,
        provider: str | None = None,
        refresh: bool = False,
        functional: bool = False,
    ) -> list[CredentialHealthSnapshot]:
        """Return health snapshots for all (or provider-filtered) credentials."""
        credentials = self._cred_manager.list_credentials(provider=provider)
        snapshots: list[CredentialHealthSnapshot] = []
        for meta in credentials:
            if refresh and meta.enabled:
                snapshots.append(self.test_credential(meta.id, functional=functional, force=True))
            else:
                detailed = self._cred_manager.get_credential(meta.id)
                snapshots.append(self.get_cached_snapshot(detailed))
        return snapshots
