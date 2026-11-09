"""Deterministic multiple-key selection engine and fallback coordinator (Section 10)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

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
    HealthObservationRecord,
    HealthState,
    ProfileAssignmentRecord,
    ProfileRecord,
    UsageEventRecord,
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


class SelectionStrategy(StrEnum):
    """Supported multi-key credential selection strategies (Section 7)."""

    LRU = "lru"
    ROUND_ROBIN = "round_robin"
    PRIORITY = "priority"
    HEALTH_FIRST = "health_first"
    HEALTH_WEIGHTED = "health_weighted"
    LATENCY = "latency"
    LATENCY_AWARE = "latency_aware"
    COST = "cost"
    COST_OPTIMIZED = "cost_optimized"
    RANDOM = "random"
    WEIGHTED = "weighted"
    MANUAL = "manual"


def parse_selection_strategy(val: SelectionStrategy | str | None) -> SelectionStrategy:
    """Normalize and resolve strategy string or enum into a canonical SelectionStrategy."""
    if val is None:
        return SelectionStrategy.LRU
    if isinstance(val, SelectionStrategy):
        return val
    cleaned = str(val).strip().lower().replace("-", "_")
    if cleaned in ("health_first", "health_weighted", "health", "health-first"):
        return SelectionStrategy.HEALTH_FIRST
    if cleaned in ("latency", "latency_aware", "latency-aware"):
        return SelectionStrategy.LATENCY
    if cleaned in ("cost", "cost_optimized", "cost-optimized"):
        return SelectionStrategy.COST
    if cleaned in ("round_robin", "roundrobin", "rr", "round-robin"):
        return SelectionStrategy.ROUND_ROBIN
    if cleaned in ("priority", "prio"):
        return SelectionStrategy.PRIORITY
    if cleaned in ("random", "rand"):
        return SelectionStrategy.RANDOM
    if cleaned in ("weighted", "weight"):
        return SelectionStrategy.WEIGHTED
    if cleaned in ("manual", "pin", "pinned"):
        return SelectionStrategy.MANUAL
    if cleaned in ("lru", "least_recently_used", "least-recently-used"):
        return SelectionStrategy.LRU
    try:
        return SelectionStrategy(cleaned)
    except ValueError:
        return SelectionStrategy.LRU


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
        self._rr_counters: dict[str, int] = {}

    def get_ordered_candidates(
        self,
        *,
        provider: str,
        profile: str | None = None,
        strategy: SelectionStrategy | str | None = None,
        exclude_ids: set[str] | None = None,
        exclude_quota_domains: set[str] | None = None,
    ) -> list[tuple[CredentialMetadata, int, str]]:
        """Return ordered eligible candidate credentials with (metadata, priority, reason)."""
        adapter = self._registry.get(provider)
        norm_provider = adapter.name
        clean_profile = validate_profile_name(profile) if profile else None
        excluded_ids = exclude_ids or set()
        excluded_domains = set(exclude_quota_domains or set())

        active_strategy = parse_selection_strategy(strategy)

        with self._db.session_scope() as session:
            priority_by_cred: dict[str, int] = {}

            if clean_profile is not None:
                prof_rec = session.get(ProfileRecord, clean_profile)
                if prof_rec is None:
                    raise ProfileNotFoundError(
                        f"Profile '{clean_profile}' does not exist.",
                        hint="Run 'keypool profile list' to view available profiles.",
                    )
                if strategy is None and prof_rec.selection_strategy:
                    active_strategy = parse_selection_strategy(prof_rec.selection_strategy)

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

            # Identify quota_domains in active cooldown
            for rec in all_provider_creds:
                if (
                    rec.quota_domain
                    and rec.status
                    in (HealthState.RATE_LIMITED.value, HealthState.QUOTA_EXHAUSTED.value)
                    and is_iso_timestamp_future(rec.cooldown_until)
                ):
                    excluded_domains.add(rec.quota_domain)

            # Filter to profile if set
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

                meta = self._cred_manager._record_to_metadata(
                    rec,
                    profiles=[clean_profile] if clean_profile else [],
                )
                prio = priority_by_cred.get(rec.id, rec.priority)
                reason = f"{active_strategy.value}:" + (
                    "healthy"
                    if rec.status == HealthState.HEALTHY.value
                    else "previously_successful"
                    if rec.last_successful_at
                    else "untested"
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

            # Strategy-based sorting
            if active_strategy == SelectionStrategy.PRIORITY:
                # Lower profile priority rank first, then higher credential priority weight, then health tier, then LRU
                def _priority_sort(
                    item: tuple[CredentialMetadata, int, str],
                ) -> tuple[int, int, int, str]:
                    m, p, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    prof_rank = p if clean_profile is not None else 0
                    return (prof_rank, -m.priority, tier, m.last_used_at or "")

                eligible.sort(key=_priority_sort)

            elif active_strategy in (SelectionStrategy.LATENCY, SelectionStrategy.LATENCY_AWARE):
                # Fetch latest observed latency per credential
                latest_latencies: dict[str, float] = {}
                for m, _, _ in eligible:
                    latest_obs = session.execute(
                        select(HealthObservationRecord.latency_ms)
                        .where(
                            HealthObservationRecord.credential_id == m.id,
                            HealthObservationRecord.latency_ms.isnot(None),
                        )
                        .order_by(HealthObservationRecord.observed_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    latest_latencies[m.id] = latest_obs if latest_obs is not None else 99999.0

                def _latency_sort(
                    item: tuple[CredentialMetadata, int, str],
                ) -> tuple[int, float, str]:
                    m, _, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    return (tier, latest_latencies.get(m.id, 99999.0), m.last_used_at or "")

                eligible.sort(key=_latency_sort)

            elif active_strategy in (SelectionStrategy.HEALTH_FIRST, SelectionStrategy.HEALTH_WEIGHTED):
                # Healthy > Previously successful > Untested, then least recently used
                def _health_sort(item: tuple[CredentialMetadata, int, str]) -> tuple[int, str]:
                    m, _, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    return (tier, m.last_used_at or "")

                eligible.sort(key=_health_sort)

            elif active_strategy in (SelectionStrategy.COST, SelectionStrategy.COST_OPTIMIZED):
                # Order by lowest observed usage/cost
                usage_by_cred: dict[str, float] = {}
                for m, _, _ in eligible:
                    total_cost = session.execute(
                        select(func.coalesce(func.sum(UsageEventRecord.cost_usd), 0.0)).where(
                            UsageEventRecord.credential_id == m.id
                        )
                    ).scalar_one()
                    usage_by_cred[m.id] = float(total_cost or 0.0)

                def _cost_sort(item: tuple[CredentialMetadata, int, str]) -> tuple[int, float, str]:
                    m, _, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    return (tier, usage_by_cred.get(m.id, 0.0), m.last_used_at or "")

                eligible.sort(key=_cost_sort)

            elif active_strategy == SelectionStrategy.ROUND_ROBIN:
                # Deterministic round-robin index rotation
                key = f"{norm_provider}:{clean_profile or 'all'}"
                idx = self._rr_counters.get(key, 0)
                # Sort initially by ID for determinism, then rotate
                eligible.sort(key=lambda item: item[0].id)
                n = len(eligible)
                rotated = [eligible[(idx + i) % n] for i in range(n)]
                self._rr_counters[key] = (idx + 1) % n
                return rotated

            elif active_strategy == SelectionStrategy.RANDOM:
                # Randomize within the top health tier
                secrets.SystemRandom().shuffle(eligible)
                eligible.sort(
                    key=lambda item: 0 if item[0].status == HealthState.HEALTHY.value else 1
                )

            elif active_strategy == SelectionStrategy.WEIGHTED:
                # Weighted priority selection using exponential random sampling
                def _weighted_sort(
                    item: tuple[CredentialMetadata, int, str],
                ) -> tuple[int, float]:
                    m, _, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    w = max(1, m.priority)
                    score = secrets.SystemRandom().random() ** (1.0 / float(w))
                    return (tier, -score)

                eligible.sort(key=_weighted_sort)

            elif active_strategy == SelectionStrategy.MANUAL:
                # Preserve manual profile assignment priority order, then alphabetical by ID
                def _manual_sort(
                    item: tuple[CredentialMetadata, int, str],
                ) -> tuple[int, str]:
                    m, p, _ = item
                    return (p, m.id)

                eligible.sort(key=_manual_sort)

            else:
                # Default LRU strategy
                def _lru_sort(
                    item: tuple[CredentialMetadata, int, str],
                ) -> tuple[int, int, str, str, str]:
                    m, prio, _ = item
                    tier = (
                        0
                        if m.status == HealthState.HEALTHY.value
                        else (1 if m.last_successful_at else 2)
                    )
                    return (prio, tier, m.last_used_at or "", m.created_at, m.id)

                eligible.sort(key=_lru_sort)

            return eligible

    def select_credential(
        self,
        *,
        provider: str,
        profile: str | None = None,
        strategy: SelectionStrategy | str | None = None,
        record_usage: bool = True,
        verify_keychain: bool = True,
    ) -> SelectedCredential:
        """Select the best eligible credential, verifying Keychain retrievability."""
        adapter = self._registry.get(provider)
        candidates = self.get_ordered_candidates(
            provider=adapter.name, profile=profile, strategy=strategy
        )

        last_keychain_err: Exception | None = None
        for meta, _prio, reason in candidates:
            if verify_keychain:
                try:
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
        strategy: SelectionStrategy | str | None = None,
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
                    strategy=strategy,
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
                    if meta.quota_domain and classification.state in (
                        HealthState.RATE_LIMITED,
                        HealthState.QUOTA_EXHAUSTED,
                        HealthState.QUOTA_Exhausted,
                    ):
                        excluded_domains.add(meta.quota_domain)
                continue

        if last_error is not None:
            raise CredentialUnavailableError(
                f"Operation failed after {len(tried_ids)} credential attempt(s): {last_error}"
            ) from last_error
        raise CredentialUnavailableError(f"No eligible credentials available for '{adapter.name}'.")
