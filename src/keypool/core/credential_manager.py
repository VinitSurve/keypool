"""Credential lifecycle management coordinating macOS Keychain and SQLite metadata."""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import secrets
from datetime import UTC
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select

from keypool.core.exceptions import (
    CredentialNotFoundError,
    InvalidArgumentError,
    KeychainError,
    SecurityError,
    StorageError,
)
from keypool.core.usage_tracker import UsageTracker
from keypool.providers.registry import ProviderRegistry
from keypool.security.redaction import (
    mask_secret_display,
    redact_text,
    register_secret_for_redaction,
)
from keypool.security.validation import (
    validate_api_key_input,
    validate_credential_id,
    validate_credential_name,
)
from keypool.storage.database import DatabaseManager
from keypool.storage.keychain import SecretStore
from keypool.storage.models import (
    CredentialRecord,
    HealthObservationRecord,
    HealthState,
    ModelCacheRecord,
    ProfileAssignmentRecord,
    UsageEventRecord,
    utc_now_iso,
)


def compute_secret_fingerprint(secret: str) -> str:
    """Compute a deterministic, salted SHA-256 fingerprint of a raw secret for duplicate detection."""
    cleaned = secret.strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


class CredentialMetadata(BaseModel):
    """Non-secret metadata snapshot of a managed credential."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    provider: str
    keychain_reference: str
    storage_backend: str = "macOS Keychain"
    enabled: bool
    status: str
    quota_domain: str | None = None
    profiles: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    last_tested_at: str | None = None
    last_successful_at: str | None = None
    last_failure_at: str | None = None
    cooldown_until: str | None = None
    cache_expires_at: str | None = None
    last_error_category: str | None = None
    last_http_status: int | None = None
    last_error_message: str | None = None
    last_tested_model: str | None = None
    cached_models: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    masked_preview: str | None = None

    # v2.0 fields
    tags: list[str] = Field(default_factory=list)
    priority: int = 100
    description: str | None = None
    expires_at: str | None = None
    rotation_interval_days: int | None = None
    last_rotated_at: str | None = None
    secret_fingerprint: str | None = None

    @property
    def effective_status(self) -> str:
        if not self.enabled:
            return HealthState.DISABLED.value
        return self.status

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize metadata for JSON output (guaranteed free of secret values)."""
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "keychain_reference": self.keychain_reference,
            "storage_backend": self.storage_backend,
            "enabled": self.enabled,
            "status": self.effective_status,
            "raw_health_status": self.status,
            "profiles": list(self.profiles),
            "tags": list(self.tags),
            "priority": self.priority,
            "description": self.description,
            "expires_at": self.expires_at,
            "rotation_interval_days": self.rotation_interval_days,
            "last_rotated_at": self.last_rotated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "last_tested_at": self.last_tested_at,
            "last_successful_at": self.last_successful_at,
            "last_failure_at": self.last_failure_at,
            "cooldown_until": self.cooldown_until,
            "cache_expires_at": self.cache_expires_at,
            "last_error_category": self.last_error_category,
            "last_http_status": self.last_http_status,
            "last_error_message": redact_text(self.last_error_message)
            if self.last_error_message
            else None,
            "last_tested_model": self.last_tested_model,
            "cached_models": list(self.cached_models),
        }


class ReconciliationReport(BaseModel):
    """Result of reconciling SQLite metadata against macOS Keychain items."""

    model_config = ConfigDict(extra="forbid")

    total_checked: int = 0
    healthy_references: int = 0
    missing_in_keychain: list[str] = Field(default_factory=list)
    keychain_errors: list[str] = Field(default_factory=list)


class CredentialManager:
    """Manages unlimited API credentials across providers with safe two-phase persistence."""

    def __init__(
        self,
        db: DatabaseManager,
        secret_store: SecretStore,
        provider_registry: ProviderRegistry,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._registry = provider_registry
        self._usage = usage_tracker or UsageTracker(db)

    def _generate_unique_id(self) -> str:
        """Generate a unique credential ID like cred_a83f2109."""
        with self._db.session_scope() as session:
            for _ in range(20):
                candidate = f"cred_{secrets.token_hex(4)}"
                if session.get(CredentialRecord, candidate) is None:
                    return candidate
        raise StorageError("Unable to allocate a unique credential ID.")

    @staticmethod
    def _parse_tags(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [str(item).strip() for item in val if str(item).strip()]
        except Exception:  # noqa: S110
            pass
        return []

    @classmethod
    def _record_to_metadata(
        cls,
        rec: CredentialRecord,
        profiles: list[str],
        cached_models: list[str] | None = None,
        warnings: list[str] | None = None,
        masked_preview: str | None = None,
    ) -> CredentialMetadata:
        return CredentialMetadata(
            id=rec.id,
            name=rec.name,
            provider=rec.provider,
            keychain_reference=rec.keychain_reference,
            enabled=rec.enabled,
            status=rec.status,
            quota_domain=rec.quota_domain,
            profiles=sorted(profiles),
            created_at=rec.created_at,
            updated_at=rec.updated_at,
            last_used_at=rec.last_used_at,
            last_tested_at=rec.last_tested_at,
            last_successful_at=rec.last_successful_at,
            last_failure_at=rec.last_failure_at,
            cooldown_until=rec.cooldown_until,
            cache_expires_at=rec.cache_expires_at,
            last_error_category=rec.last_error_category,
            last_http_status=rec.last_http_status,
            last_error_message=redact_text(rec.last_error_message)
            if rec.last_error_message
            else None,
            last_tested_model=rec.last_tested_model,
            cached_models=cached_models or [],
            warnings=warnings or [],
            masked_preview=masked_preview,
            tags=cls._parse_tags(rec.tags),
            priority=rec.priority,
            description=rec.description,
            expires_at=rec.expires_at,
            rotation_interval_days=rec.rotation_interval_days,
            last_rotated_at=rec.last_rotated_at,
            secret_fingerprint=rec.secret_fingerprint,
        )

    def add_credential(
        self,
        *,
        provider: str,
        name: str,
        secret: str,
        quota_domain: str | None = None,
        tags: list[str] | None = None,
        priority: int = 100,
        description: str | None = None,
        expires_at: str | None = None,
        rotation_interval_days: int | None = None,
        allow_duplicate: bool = False,
    ) -> CredentialMetadata:
        """Add a new credential to macOS Keychain and register metadata in SQLite.

        Includes duplicate key detection, format validation, and rollback on DB failure.
        """
        register_secret_for_redaction(secret)
        adapter = self._registry.get(provider)
        clean_name = validate_credential_name(name)
        clean_secret, warnings = validate_api_key_input(secret, provider=adapter.name)
        masked = mask_secret_display(clean_secret)
        fingerprint = compute_secret_fingerprint(clean_secret)

        # Duplicate key check
        if not allow_duplicate:
            with self._db.session_scope() as session:
                existing = session.execute(
                    select(CredentialRecord).where(
                        CredentialRecord.provider == adapter.name,
                        CredentialRecord.secret_fingerprint == fingerprint,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise InvalidArgumentError(
                        f"Duplicate credential detected: an existing key for {adapter.display_name} has the identical API key ('{existing.name}' [{existing.id}]).",
                        hint="Specify --allow-duplicate to force adding a duplicate key.",
                    )

        cred_id = self._generate_unique_id()
        keychain_ref = f"keypool:{cred_id}"
        now = utc_now_iso()
        tags_json = json.dumps(sorted(list(set(tags or []))))

        # Phase 1: Write secret to macOS Keychain
        self._secret_store.set_secret(keychain_ref, clean_secret, allow_overwrite=False)

        # Phase 2: Register metadata in SQLite; rollback Keychain on any DB failure
        try:
            with self._db.session_scope() as session:
                record = CredentialRecord(
                    id=cred_id,
                    name=clean_name,
                    provider=adapter.name,
                    keychain_reference=keychain_ref,
                    enabled=True,
                    status=HealthState.UNTESTED.value,
                    quota_domain=quota_domain.strip() if quota_domain else None,
                    tags=tags_json,
                    priority=priority,
                    description=description.strip() if description else None,
                    expires_at=expires_at,
                    rotation_interval_days=rotation_interval_days,
                    last_rotated_at=now,
                    secret_fingerprint=fingerprint,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.flush()
                meta = self._record_to_metadata(
                    record,
                    profiles=[],
                    warnings=warnings,
                    masked_preview=masked,
                )
        except Exception as db_exc:
            with contextlib.suppress(Exception):
                self._secret_store.delete_secret(keychain_ref)
            if isinstance(db_exc, StorageError):
                raise
            raise StorageError(
                f"Failed to save credential metadata in SQLite: {db_exc}"
            ) from db_exc

        self._usage.record_event(
            provider=adapter.name,
            credential_id=cred_id,
            event_type="credential_added",
            status="info",
            summary=f"New {adapter.display_name} credential added ({clean_name})",
        )
        return meta

    def list_credentials(self, *, provider: str | None = None) -> list[CredentialMetadata]:
        """List credentials ordered by creation timestamp, optionally filtered by provider."""
        norm_provider: str | None = None
        if provider:
            adapter = self._registry.get(provider)
            norm_provider = adapter.name

        with self._db.session_scope() as session:
            stmt = select(CredentialRecord).order_by(
                CredentialRecord.provider, CredentialRecord.created_at, CredentialRecord.id
            )
            if norm_provider:
                stmt = stmt.where(CredentialRecord.provider == norm_provider)
            records = session.execute(stmt).scalars().all()
            if not records:
                return []

            cred_ids = [r.id for r in records]
            assign_rows = (
                session.execute(
                    select(ProfileAssignmentRecord).where(
                        ProfileAssignmentRecord.credential_id.in_(cred_ids)
                    )
                )
                .scalars()
                .all()
            )
            profiles_by_cred: dict[str, list[str]] = {cid: [] for cid in cred_ids}
            for row in assign_rows:
                profiles_by_cred.setdefault(row.credential_id, []).append(row.profile_name)

            return [
                self._record_to_metadata(rec, profiles_by_cred.get(rec.id, [])) for rec in records
            ]

    def get_credential(self, credential_id: str) -> CredentialMetadata:
        """Fetch metadata for a single credential by ID."""
        clean_id = validate_credential_id(credential_id)
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(
                    f"Credential '{clean_id}' not found.",
                    hint="Run 'keypool list' to view configured credentials.",
                )
            assign_rows = (
                session.execute(
                    select(ProfileAssignmentRecord).where(
                        ProfileAssignmentRecord.credential_id == clean_id
                    )
                )
                .scalars()
                .all()
            )
            profiles = [r.profile_name for r in assign_rows]
            model_rows = (
                session.execute(
                    select(ModelCacheRecord).where(
                        ModelCacheRecord.credential_id == clean_id,
                        ModelCacheRecord.available.is_(True),
                    )
                )
                .scalars()
                .all()
            )
            cached_models = sorted(r.model_id for r in model_rows)
            return self._record_to_metadata(record, profiles, cached_models=cached_models)

    def retrieve_secret(self, credential_id: str) -> str:
        """Retrieve the actual secret from macOS Keychain for an existing credential."""
        clean_id = validate_credential_id(credential_id)
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            keychain_ref = record.keychain_reference

        try:
            secret = self._secret_store.get_secret(keychain_ref)
            register_secret_for_redaction(secret)
            return secret
        except KeychainError as exc:
            with self._db.session_scope() as session:
                rec = session.get(CredentialRecord, clean_id)
                if rec is not None:
                    rec.status = HealthState.KEYCHAIN_ERROR.value
                    rec.last_failure_at = utc_now_iso()
                    rec.last_error_category = "keychain_error"
                    rec.last_error_message = redact_text(str(exc))
                    rec.updated_at = utc_now_iso()
            raise

    def tag_credential(self, credential_id: str, tags: list[str]) -> CredentialMetadata:
        """Add tags to an existing credential."""
        clean_id = validate_credential_id(credential_id)
        now = utc_now_iso()
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            current_tags = set(self._parse_tags(record.tags))
            for t in tags:
                if t.strip():
                    current_tags.add(t.strip())
            record.tags = json.dumps(sorted(list(current_tags)))
            record.updated_at = now
        return self.get_credential(clean_id)

    def untag_credential(self, credential_id: str, tags: list[str]) -> CredentialMetadata:
        """Remove tags from an existing credential."""
        clean_id = validate_credential_id(credential_id)
        now = utc_now_iso()
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            current_tags = set(self._parse_tags(record.tags))
            for t in tags:
                current_tags.discard(t.strip())
            record.tags = json.dumps(sorted(list(current_tags)))
            record.updated_at = now
        return self.get_credential(clean_id)

    def rotate_credential(self, credential_id: str, new_secret: str) -> CredentialMetadata:
        """Rotate the underlying secret for a credential safely."""
        clean_id = validate_credential_id(credential_id)
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            provider = record.provider
            keychain_ref = record.keychain_reference

        clean_secret, warnings = validate_api_key_input(new_secret, provider=provider)
        fingerprint = compute_secret_fingerprint(clean_secret)
        self._secret_store.replace_secret(keychain_ref, clean_secret)
        now = utc_now_iso()

        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is not None:
                record.status = HealthState.UNTESTED.value
                record.secret_fingerprint = fingerprint
                record.last_rotated_at = now
                record.cooldown_until = None
                record.cache_expires_at = None
                record.last_error_category = None
                record.last_http_status = None
                record.last_error_message = None
                record.updated_at = now

        self._usage.record_event(
            provider=provider,
            credential_id=clean_id,
            event_type="credential_rotated",
            status="info",
            summary=f"Credential {clean_id} API key rotated",
        )
        meta = self.get_credential(clean_id)
        meta.warnings = warnings
        return meta

    def rename_credential(self, credential_id: str, new_name: str) -> CredentialMetadata:
        """Rename a credential's friendly label."""
        clean_id = validate_credential_id(credential_id)
        clean_name = validate_credential_name(new_name)
        now = utc_now_iso()

        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            old_name = record.name
            record.name = clean_name
            record.updated_at = now
            provider = record.provider

        self._usage.record_event(
            provider=provider,
            credential_id=clean_id,
            event_type="credential_renamed",
            status="info",
            summary=f"Credential {clean_id} renamed from '{old_name}' to '{clean_name}'",
        )
        return self.get_credential(clean_id)

    def update_credential(
        self,
        credential_id: str,
        *,
        new_name: str | None = None,
        new_secret: str | None = None,
    ) -> CredentialMetadata:
        """Update a credential's friendly name and/or safely replace its Keychain secret."""
        clean_id = validate_credential_id(credential_id)
        if new_name is None and new_secret is None:
            raise InvalidArgumentError(
                "At least one of a new name or a new API key must be provided to update a credential."
            )

        clean_name = validate_credential_name(new_name) if new_name is not None else None
        warnings: list[str] = []
        masked: str | None = None

        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            provider = record.provider
            keychain_ref = record.keychain_reference

        if new_secret is not None:
            register_secret_for_redaction(new_secret)
            clean_secret, warnings = validate_api_key_input(new_secret, provider=provider)
            masked = mask_secret_display(clean_secret)
            fingerprint = compute_secret_fingerprint(clean_secret)
            self._secret_store.replace_secret(keychain_ref, clean_secret)
        else:
            fingerprint = None

        now = utc_now_iso()
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            if clean_name is not None:
                record.name = clean_name
            if new_secret is not None:
                record.status = HealthState.UNTESTED.value
                record.secret_fingerprint = fingerprint
                record.last_rotated_at = now
                record.cooldown_until = None
                record.cache_expires_at = None
                record.last_error_category = None
                record.last_http_status = None
                record.last_error_message = None
            record.updated_at = now

        self._usage.record_event(
            provider=provider,
            credential_id=clean_id,
            event_type="credential_updated",
            status="info",
            summary=f"Credential {clean_id} updated",
        )
        meta = self.get_credential(clean_id)
        meta.warnings = warnings
        meta.masked_preview = masked
        return meta

    def set_enabled(self, credential_id: str, *, enabled: bool) -> CredentialMetadata:
        """Enable or disable a credential. Disabled credentials are never selected."""
        clean_id = validate_credential_id(credential_id)
        now = utc_now_iso()
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, clean_id)
            if record is None:
                raise CredentialNotFoundError(f"Credential '{clean_id}' not found.")
            record.enabled = enabled
            record.updated_at = now
            provider = record.provider

        action = "enabled" if enabled else "disabled"
        self._usage.record_event(
            provider=provider,
            credential_id=clean_id,
            event_type=f"credential_{action}",
            status="info",
            summary=f"Credential {clean_id} {action}",
        )
        return self.get_credential(clean_id)

    def enable_credential(self, credential_id: str) -> CredentialMetadata:
        return self.set_enabled(credential_id, enabled=True)

    def disable_credential(self, credential_id: str) -> CredentialMetadata:
        return self.set_enabled(credential_id, enabled=False)

    def remove_credential(self, credential_id: str) -> CredentialMetadata:
        """Delete a credential's Keychain secret and SQLite metadata safely."""
        meta = self.get_credential(credential_id)

        # Phase 1: Delete from Keychain first so no orphaned secret remains if Keychain is locked
        self._secret_store.delete_secret(meta.keychain_reference)

        # Phase 2: Delete SQLite record (cascades to profile assignments & observations)
        with self._db.session_scope() as session:
            record = session.get(CredentialRecord, meta.id)
            if record is not None:
                session.delete(record)

        self._usage.record_event(
            provider=meta.provider,
            credential_id=None,
            event_type="credential_removed",
            status="info",
            summary=f"Credential {meta.id} ({meta.name}) removed",
        )
        return meta

    def reconcile_storage(self) -> ReconciliationReport:
        """Verify that every SQLite credential record has a valid macOS Keychain item."""
        report = ReconciliationReport()
        with self._db.session_scope() as session:
            records = session.execute(select(CredentialRecord)).scalars().all()
            report.total_checked = len(records)
            now = utc_now_iso()

            for rec in records:
                try:
                    exists = self._secret_store.secret_exists(rec.keychain_reference)
                    if exists:
                        report.healthy_references += 1
                        if rec.status == HealthState.KEYCHAIN_ERROR.value:
                            rec.status = HealthState.UNTESTED.value
                            rec.last_error_category = None
                            rec.last_error_message = None
                            rec.updated_at = now
                    else:
                        report.missing_in_keychain.append(rec.id)
                        rec.status = HealthState.KEYCHAIN_ERROR.value
                        rec.last_error_category = "keychain_secret_missing"
                        rec.last_error_message = f"Secret missing in macOS Keychain for reference '{rec.keychain_reference}'"
                        rec.updated_at = now
                except Exception as exc:
                    report.keychain_errors.append(f"{rec.id}: {redact_text(str(exc))}")
                    rec.status = HealthState.KEYCHAIN_ERROR.value
                    rec.last_error_category = "keychain_error"
                    rec.last_error_message = redact_text(str(exc))
                    rec.updated_at = now

        return report

    def bulk_add_credentials(
        self,
        credentials: list[dict[str, Any]],
        *,
        allow_duplicate: bool = False,
    ) -> list[CredentialMetadata]:
        """Add multiple credentials in a single batch operation."""
        results: list[CredentialMetadata] = []
        for item in credentials:
            meta = self.add_credential(
                provider=item["provider"],
                name=item["name"],
                secret=item["secret"],
                quota_domain=item.get("quota_domain"),
                tags=item.get("tags"),
                priority=int(item.get("priority", 100)),
                description=item.get("description"),
                expires_at=item.get("expires_at"),
                rotation_interval_days=item.get("rotation_interval_days"),
                allow_duplicate=allow_duplicate,
            )
            results.append(meta)
        return results

    def bulk_remove_credentials(self, credential_ids: list[str]) -> list[CredentialMetadata]:
        """Delete multiple credentials from Keychain and SQLite."""
        removed: list[CredentialMetadata] = []
        for cid in credential_ids:
            with contextlib.suppress(Exception):
                removed.append(self.remove_credential(cid))
        return removed

    def bulk_set_enabled(
        self, credential_ids: list[str], *, enabled: bool
    ) -> list[CredentialMetadata]:
        """Enable or disable multiple credentials."""
        updated: list[CredentialMetadata] = []
        for cid in credential_ids:
            with contextlib.suppress(Exception):
                updated.append(self.set_enabled(cid, enabled=enabled))
        return updated

    def bulk_tag(self, credential_ids: list[str], tags: list[str]) -> list[CredentialMetadata]:
        """Apply tags to multiple credentials."""
        updated: list[CredentialMetadata] = []
        for cid in credential_ids:
            with contextlib.suppress(Exception):
                updated.append(self.tag_credential(cid, tags))
        return updated

    def bulk_untag(self, credential_ids: list[str], tags: list[str]) -> list[CredentialMetadata]:
        """Remove tags from multiple credentials."""
        updated: list[CredentialMetadata] = []
        for cid in credential_ids:
            with contextlib.suppress(Exception):
                updated.append(self.untag_credential(cid, tags))
        return updated

    def check_rotation_reminders(self, threshold_days: int = 7) -> list[dict[str, Any]]:
        """Identify credentials that are expired, expiring soon, or past rotation interval."""
        reminders: list[dict[str, Any]] = []
        now = datetime.datetime.now(UTC)
        creds = self.list_credentials()

        for c in creds:
            if not c.enabled:
                continue

            # Check explicit expiration date
            if c.expires_at:
                try:
                    exp_dt = datetime.datetime.fromisoformat(c.expires_at)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    diff = exp_dt - now
                    days_remaining = diff.total_seconds() / 86400.0

                    if days_remaining <= 0:
                        reminders.append(
                            {
                                "credential_id": c.id,
                                "name": c.name,
                                "provider": c.provider,
                                "state": "expired",
                                "severity": "critical",
                                "days_remaining": round(days_remaining, 1),
                                "message": f"Credential '{c.name}' expired on {c.expires_at}",
                            }
                        )
                    elif days_remaining <= threshold_days:
                        reminders.append(
                            {
                                "credential_id": c.id,
                                "name": c.name,
                                "provider": c.provider,
                                "state": "expiring_soon",
                                "severity": "warning",
                                "days_remaining": round(days_remaining, 1),
                                "message": f"Credential '{c.name}' expires in {days_remaining:.1f} days ({c.expires_at})",
                            }
                        )
                except ValueError:
                    pass

            # Check rotation interval
            if c.rotation_interval_days and c.last_rotated_at:
                try:
                    rot_dt = datetime.datetime.fromisoformat(c.last_rotated_at)
                    if rot_dt.tzinfo is None:
                        rot_dt = rot_dt.replace(tzinfo=UTC)
                    age_days = (now - rot_dt).total_seconds() / 86400.0
                    interval = float(c.rotation_interval_days)

                    if age_days >= interval:
                        reminders.append(
                            {
                                "credential_id": c.id,
                                "name": c.name,
                                "provider": c.provider,
                                "state": "rotation_due",
                                "severity": "warning",
                                "days_overdue": round(age_days - interval, 1),
                                "message": f"Credential '{c.name}' has not been rotated for {age_days:.1f} days (rotation policy: every {c.rotation_interval_days} days)",
                            }
                        )
                except ValueError:
                    pass

        return reminders

    def get_credential_history(self, credential_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve historical audit timeline (executions, health checks, rotations) for a credential."""
        clean_id = validate_credential_id(credential_id)
        entries: list[dict[str, Any]] = []

        with self._db.session_scope() as session:
            # 1. Usage events
            usage_rows = (
                session.execute(
                    select(UsageEventRecord)
                    .where(UsageEventRecord.credential_id == clean_id)
                    .order_by(desc(UsageEventRecord.occurred_at))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            for u in usage_rows:
                entries.append(
                    {
                        "type": "usage_event",
                        "event_type": u.event_type,
                        "status": u.status,
                        "timestamp": u.occurred_at,
                        "model": u.model,
                        "cost_usd": u.cost_usd,
                        "duration_ms": u.duration_ms,
                        "summary": u.summary,
                    }
                )

            # 2. Health observations
            health_rows = (
                session.execute(
                    select(HealthObservationRecord)
                    .where(HealthObservationRecord.credential_id == clean_id)
                    .order_by(desc(HealthObservationRecord.observed_at))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            for h in health_rows:
                entries.append(
                    {
                        "type": "health_check",
                        "state": h.state,
                        "error_category": h.error_category,
                        "http_status": h.http_status,
                        "latency_ms": h.latency_ms,
                        "timestamp": h.observed_at,
                        "model_tested": h.model_tested,
                        "message": h.sanitized_message,
                    }
                )

        # Sort combined history chronologically descending
        entries.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
        return entries[:limit]

    def export_credentials_bundle(
        self,
        passphrase: str,
        provider: str | None = None,
    ) -> bytes:
        """Export credentials and their secrets in an AES-256-GCM encrypted binary bundle."""
        from keypool.sync.protocol import encrypt_sync_payload

        creds = self.list_credentials(provider=provider)
        items: list[dict[str, Any]] = []

        for c in creds:
            with contextlib.suppress(Exception):
                secret = self.retrieve_secret(c.id)
                items.append(
                    {
                        "name": c.name,
                        "provider": c.provider,
                        "secret": secret,
                        "quota_domain": c.quota_domain,
                        "tags": c.tags,
                        "priority": c.priority,
                        "description": c.description,
                        "expires_at": c.expires_at,
                        "rotation_interval_days": c.rotation_interval_days,
                    }
                )

        payload = {
            "type": "keypool_credentials_bundle",
            "version": 1,
            "exported_at": utc_now_iso(),
            "count": len(items),
            "credentials": items,
        }
        return encrypt_sync_payload(payload, passphrase)

    def import_credentials_bundle(
        self,
        bundle_bytes: bytes,
        passphrase: str,
        *,
        allow_duplicate: bool = False,
    ) -> list[CredentialMetadata]:
        """Decrypt and import credentials from an encrypted bundle."""
        from keypool.sync.protocol import decrypt_sync_payload

        payload = decrypt_sync_payload(bundle_bytes, passphrase)
        if payload.get("type") not in ("keypool_credentials_bundle", "keypool_sync_bundle"):
            raise SecurityError("Unrecognized credential bundle format.")

        creds_data = payload.get("credentials") or []
        imported: list[CredentialMetadata] = []

        for item in creds_data:
            prov = item.get("provider")
            name = item.get("name")
            secret = item.get("secret")
            if not prov or not name or not secret:
                continue

            with contextlib.suppress(Exception):
                meta = self.add_credential(
                    provider=prov,
                    name=name,
                    secret=secret,
                    quota_domain=item.get("quota_domain"),
                    tags=item.get("tags") or [],
                    priority=int(item.get("priority", 100)),
                    description=item.get("description"),
                    expires_at=item.get("expires_at"),
                    rotation_interval_days=item.get("rotation_interval_days"),
                    allow_duplicate=allow_duplicate,
                )
                imported.append(meta)

        return imported
