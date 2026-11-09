"""Deterministic multiple-key selection engine and fallback coordinator (Section 10)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from keypool.core.credential_manager import CredentialManager, CredentialMetadata
from keypool.core.exceptions import (
    CredentialUnavailableError,
    KeychainError,
    ProfileNotFoundError,
)
from keypool.core.health_manager import HealthManager, is_iso_timestamp_future
from keypool.core.usage_tracker import UsageTracker
from keypool.providers.base import ProviderErrorClassification
from keypool.providers.registry import ProviderRegistry
from keypool.security.validation import validate_profile_name
from keypool.storage.database import DatabaseManager
from keypool.storage.models import (
    CredentialRecord,
    HealthState,
    ProfileAssignmentRecord,
    ProfileRecord,
    utc_now_iso,
)

T = TypeVar("T")

_NON_ELIGIBLE_STATES = frozenset(
    {
        HealthState.AUTH_FAILED.value,
        HealthState.PERMISSION_DENIED.value,
        HealthState.KEYCHAIN_ERROR.value,
        HealthState.DISABLED.value,
    }
)


class SelectedCredential(BaseModel):
    """Represents an eligible credential chosen by the selection engine."""

    model_config = ConfigDict(extra="forbid")

    metadata: CredentialMetadata
    profile_used: str | None = None
    env_var_name: str
    selection_reason: str


class SelectionEngine:
    """Predictable, deterministic credential selector supporting unlimited keys per provider."""

    def __init__(
        self,
        db: DatabaseManager,
        credential_manager: CredentialManager,
        provider_registry: ProviderRegistry,
        health_manager: HealthManager | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._db = db
        self._cred_manager = credential_manager
        self._registry = provider_registry
        self._health_manager = health_manager
        self._usage = usage_tracker or UsageTracker(db)

    def get_ordered_candidates(
        self,
        *,
        provider: str,
        profile: str | None = None,
        exclude_ids: set[str] | None = None,
        exclude_quota_domains: set[str] | None = None,
    ) -> list[tuple[CredentialMetadata, int, str]]:
        """Return ordered eligible candidate credentials with (metadata, priority, reason).

        Enforces:
        1. Provider validation.
        2. Strict profile boundary when profile is specified (never crosses profiles).
        3. Excludes disabled credentials.
        4. Excludes known invalid authentication / permission denied / keychain error.
        5. Excludes credentials under an active cooldown or excluded quota domain.
        6. Prefers healthy / previously successful credentials.
        7. Tie-breaks by Least-Recently-Used (last_used_at ascending, None first).
        """
        adapter = self._registry.get(provider)
        norm_provider = adapter.name
        clean_profile = validate_profile_name(profile) if profile else None
        excluded_ids = exclude_ids or set()
        excluded_domains = set(exclude_quota_domains or set())

        with self._db.session_scope() as session:
            priority_by_cred: dict[str, int] = {}

            if clean_profile is not None:
                prof_rec = session.get(ProfileRecord, clean_profile)
                if prof_rec is None:
                    raise ProfileNotFoundError(
                        f"Profile '{clean_profile}' does not exist.",
                        hint="Run 'keypool profile list' to view available profiles.",
                    )
                assign_rows = (
                    session.execute(
                        select(ProfileAssignmentRecord).where(
                            ProfileAssignmentRecord.profile_name == clean_profile
                        )
                    )
                    .scalars()
                    .all()
                )
                for a in assign_rows:
                    priority_by_cred[a.credential_id] = a.priority

                if not priority_by_cred:
                    raise CredentialUnavailableError(
                        f"No credentials are assigned to profile '{clean_profile}' for provider '{norm_provider}'.",
                        hint=f"Assign a credential using 'keypool profile assign {clean_profile} <credential_id>'.",
                    )

            all_provider_creds = (
                session.execute(
                    select(CredentialRecord).where(CredentialRecord.provider == norm_provider)
                )
                .scalars()
                .all()
            )

            if not all_provider_creds:
                raise CredentialUnavailableError(
                    f"No credentials configured for provider '{norm_provider}'.",
                    hint=f"Add one using 'keypool add {norm_provider}'.",
                )

            # Identify any quota_domains currently in active cooldown across this provider
            for rec in all_provider_creds:
                if (
                    rec.quota_domain
                    and rec.status
                    in (HealthState.RATE_LIMITED.value, HealthState.QUOTA_Exhausted.value)
                    and is_iso_timestamp_future(rec.cooldown_until)
                ):
                    excluded_domains.add(rec.quota_domain)

            # If profile is specified, filter strictly to assigned credentials for this provider
            if clean_profile is not None:
                scoped_creds = [c for c in all_provider_creds if c.id in priority_by_cred]
                if not scoped_creds:
                    raise CredentialUnavailableError(
                        f"Profile '{clean_profile}' has no credentials assigned for provider '{norm_provider}'. "
                        "Cross-profile fallback is prohibited.",
                        hint=f"Assign a {norm_provider} credential to '{clean_profile}' with 'keypool profile assign {clean_profile} <id>'.",
                    )
            else:
                scoped_creds = list(all_provider_creds)

            # Track exclusion reasons for clear diagnostics if none remain eligible
            disabled_count = 0
            auth_failed_count = 0
            cooldown_count = 0
            eligible: list[tuple[CredentialMetadata, int, str]] = []

            for rec in scoped_creds:
                if rec.id in excluded_ids:
                    continue
                if not rec.enabled:
                    disabled_count += 1
                    continue
                if rec.status in _NON_ELIGIBLE_STATES:
                    auth_failed_count += 1
                    continue
                if is_iso_timestamp_future(rec.cooldown_until):
                    cooldown_count += 1
                    continue
                if rec.quota_domain and rec.quota_domain in excluded_domains:
                    cooldown_count += 1
                    continue

                meta = CredentialMetadata(
                    id=rec.id,
                    name=rec.name,
                    provider=rec.provider,
                    keychain_reference=rec.keychain_reference,
                    enabled=rec.enabled,
                    status=rec.status,
                    quota_domain=rec.quota_domain,
                    profiles=[clean_profile] if clean_profile else [],
                    created_at=rec.created_at,
                    updated_at=rec.updated_at,
                    last_used_at=rec.last_used_at,
                    last_tested_at=rec.last_tested_at,
                    last_successful_at=rec.last_successful_at,
                    last_failure_at=rec.last_failure_at,
                    cooldown_until=rec.cooldown_until,
                    cache_expires_at=rec.cache_expires_at,
                )
                prio = priority_by_cred.get(rec.id, 100)
                reason = (
                    "healthy+lru"
                    if rec.status == HealthState.HEALTHY.value
                    else "previously_successful+lru"
                    if rec.last_successful_at
                    else "untested+lru"
                )
                eligible.append((meta, prio, reason))

            if not eligible:
                details: list[str] = []
                if disabled_count:
                    details.append(f"{disabled_count} disabled")
                if auth_failed_count:
                    details.append(f"{auth_failed_count} failed auth/keychain")
                if cooldown_count:
                    details.append(f"{cooldown_count} in cooldown/quota limit")
                detail_str = f" ({', '.join(details)})" if details else ""
                scope_str = f" in profile '{clean_profile}'" if clean_profile else ""
                raise CredentialUnavailableError(
                    f"No eligible '{norm_provider}' credentials available{scope_str}{detail_str}.",
                    hint="Run 'keypool health' or 'keypool list' to inspect credential states.",
                )

            # Deterministic ordering:
            # 1. Explicit profile assignment priority (ascending, default 100)
            # 2. Health tier: HEALTHY (0) -> previously successful (1) -> UNTESTED/other (2)
            # 3. Least-Recently-Used: never used ("") before older ISO timestamps
            # 4. Creation timestamp & ID for total determinism
            def _sort_key(
                item: tuple[CredentialMetadata, int, str],
            ) -> tuple[int, int, str, str, str]:
                m, prio, _ = item
                if m.status == HealthState.HEALTHY.value:
                    health_tier = 0
                elif m.last_successful_at is not None:
                    health_tier = 1
                else:
                    health_tier = 2
                lru_key = m.last_used_at or ""
                return (prio, health_tier, lru_key, m.created_at, m.id)

            eligible.sort(key=_sort_key)
            return eligible

    def select_credential(
        self,
        *,
        provider: str,
        profile: str | None = None,
        record_usage: bool = True,
        verify_keychain: bool = True,
    ) -> SelectedCredential:
        """Select the best eligible credential, verifying Keychain retrievability."""
        adapter = self._registry.get(provider)
        candidates = self.get_ordered_candidates(provider=adapter.name, profile=profile)

        last_keychain_err: Exception | None = None
        for meta, _prio, reason in candidates:
            if verify_keychain:
                try:
                    # Verify secret is actually retrievable in Keychain
                    _ = self._cred_manager.retrieve_secret(meta.id)
                except KeychainError as exc:
                    last_keychain_err = exc
                    continue

            if record_usage:
                self.mark_credential_used(meta.id, profile=profile)

            refreshed = self._cred_manager.get_credential(meta.id)
            return SelectedCredential(
                metadata=refreshed,
                profile_used=profile.strip().lower() if profile else None,
                env_var_name=adapter.get_environment_variable(),
                selection_reason=reason,
            )

        if last_keychain_err is not None:
            raise CredentialUnavailableError(
                f"All candidate credentials for '{adapter.name}' failed macOS Keychain retrieval: {last_keychain_err}",
                hint="Run 'keypool doctor' to inspect Keychain status.",
            ) from last_keychain_err

        raise CredentialUnavailableError(f"No eligible credentials found for '{adapter.name}'.")

    def mark_credential_used(
        self,
        credential_id: str,
        *,
        profile: str | None = None,
    ) -> None:
        """Update last_used_at timestamp and record a local selection event."""
        now = utc_now_iso()
        with self._db.session_scope() as session:
            rec = session.get(CredentialRecord, credential_id)
            if rec is None:
                return
            rec.last_used_at = now
            rec.updated_at = now
            provider = rec.provider

        self._usage.record_event(
            provider=provider,
            credential_id=credential_id,
            profile_name=profile,
            event_type="selection",
            status="info",
            summary=f"Selected credential {credential_id}"
            + (f" for profile '{profile}'" if profile else ""),
        )

    def execute_with_fallback(
        self,
        *,
        provider: str,
        profile: str | None = None,
        operation: Callable[[SelectedCredential, str], T],
        classify_failure: Callable[[Exception], ProviderErrorClassification | None],
        max_attempts: int = 3,
    ) -> tuple[SelectedCredential, T]:
        """Execute an idempotent provider operation across eligible credentials with safe fallback.

        Respects cooldowns and shared quota_domains, never retries auth failures on the same key,
        and bounds total attempts to prevent infinite retry loops.
        """
        adapter = self._registry.get(provider)
        tried_ids: set[str] = set()
        excluded_domains: set[str] = set()
        bounded_attempts = max(1, min(max_attempts, 5))
        last_error: Exception | None = None

        for _attempt in range(bounded_attempts):
            try:
                candidates = self.get_ordered_candidates(
                    provider=adapter.name,
                    profile=profile,
                    exclude_ids=tried_ids,
                    exclude_quota_domains=excluded_domains,
                )
            except CredentialUnavailableError as exc:
                if last_error is not None:
                    raise CredentialUnavailableError(
                        f"All eligible credentials for '{adapter.name}' failed. Last error: {last_error}"
                    ) from last_error
                raise exc

            meta, _prio, reason = candidates[0]
            tried_ids.add(meta.id)

            try:
                secret = self._cred_manager.retrieve_secret(meta.id)
            except KeychainError as exc:
                last_error = exc
                continue

            self.mark_credential_used(meta.id, profile=profile)
            selected = SelectedCredential(
                metadata=self._cred_manager.get_credential(meta.id),
                profile_used=profile.strip().lower() if profile else None,
                env_var_name=adapter.get_environment_variable(),
                selection_reason=reason,
            )

            try:
                result = operation(selected, secret)
                return selected, result
            except Exception as op_exc:
                last_error = op_exc
                classification = classify_failure(op_exc)
                if classification is not None and self._health_manager is not None:
                    self._health_manager.record_runtime_failure(meta.id, classification)
                    # If credential belongs to a quota_domain and hit a 429/quota error,
                    # do not blindly rotate to another key in the same quota_domain
                    if meta.quota_domain and classification.state in (
                        HealthState.RATE_LIMITED,
                        HealthState.QUOTA_Exhausted,
                    ):
                        excluded_domains.add(meta.quota_domain)
                continue

        if last_error is not None:
            raise CredentialUnavailableError(
                f"Operation failed after {len(tried_ids)} credential attempt(s): {last_error}"
            ) from last_error
        raise CredentialUnavailableError(f"No eligible credentials available for '{adapter.name}'.")
