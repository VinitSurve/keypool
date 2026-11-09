"""SQLAlchemy 2.x ORM models and health state enumerations for KEYPOOL metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format with microsecond precision."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


class HealthState(StrEnum):
    """Granular credential health states (Section 9)."""

    HEALTHY = "healthy"
    UNTESTED = "untested"
    AUTH_FAILED = "auth_failed"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    QUOTA_Exhausted = "quota_exhausted"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK_ERROR = "network_error"
    KEYCHAIN_ERROR = "keychain_error"
    DISABLED = "disabled"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Human-readable label for CLI display."""
        labels = {
            "healthy": "Healthy",
            "untested": "Untested",
            "auth_failed": "Authentication failed",
            "permission_denied": "Permission denied",
            "rate_limited": "Rate limited",
            "quota_exhausted": "Quota exhausted",
            "model_unavailable": "Model unavailable",
            "provider_unavailable": "Provider unavailable",
            "network_error": "Network error",
            "keychain_error": "Keychain error",
            "disabled": "Disabled",
            "unknown": "Unknown",
        }
        return labels.get(self.value, self.value)





class TokenSource(StrEnum):
    """Distinguishes source of token/usage metrics (Section 14)."""

    UNKNOWN = "unknown"
    LOCALLY_OBSERVED = "locally_observed"
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"


class Base(DeclarativeBase):
    """Declarative base for KEYPOOL SQLite models."""


class SchemaMigrationRecord(Base):
    """Tracks applied SQLite schema migrations."""

    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    description: Mapped[str] = mapped_column(String(255), nullable=False)


class CredentialRecord(Base):
    """Non-secret metadata for a managed API credential.

    Actual secret values are NEVER stored in this table; they reside in macOS Keychain
    referenced by ``keychain_reference``.
    """

    __tablename__ = "credentials"
    __table_args__ = (
        Index("ix_credentials_provider", "provider"),
        Index("ix_credentials_enabled_status", "enabled", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    keychain_reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=HealthState.UNTESTED.value
    )
    quota_domain: Mapped[str | None] = mapped_column(String(120), nullable=True, default=None)

    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    last_used_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    last_tested_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    last_successful_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    last_failure_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)

    cooldown_until: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)
    cache_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)

    last_error_category: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    last_tested_model: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)


class HealthObservationRecord(Base):
    """Historical and cached health observation for a credential."""

    __tablename__ = "health_observations"
    __table_args__ = (
        Index("ix_health_obs_cred_time", "credential_id", "observed_at"),
        Index("ix_health_obs_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    sanitized_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    model_tested: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    cache_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)


class ModelCacheRecord(Base):
    """Cached model availability per credential and model."""

    __tablename__ = "model_cache"
    __table_args__ = (
        UniqueConstraint("credential_id", "model_id", name="uq_model_cache_cred_model"),
        Index("ix_model_cache_cred", "credential_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ProfileRecord(Base):
    """Execution profile metadata (e.g., testing, development, production, or custom)."""

    __tablename__ = "profiles"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    preferred_provider: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)


class ProfileAssignmentRecord(Base):
    """Explicit assignment linking a credential to a profile."""

    __tablename__ = "profile_assignments"
    __table_args__ = (
        UniqueConstraint("profile_name", "credential_id", name="uq_profile_credential"),
        Index("ix_profile_assignments_profile", "profile_name"),
        Index("ix_profile_assignments_credential", "credential_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("profiles.name", ondelete="CASCADE"),
        nullable=False,
    )
    credential_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    assigned_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)


class UsageEventRecord(Base):
    """Local usage and activity tracking record (never contains secrets)."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_occurred", "occurred_at"),
        Index("ix_usage_events_provider", "provider"),
        Index("ix_usage_events_credential", "credential_id"),
        Index("ix_usage_events_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_name: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    is_rate_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    observed_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    token_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TokenSource.UNKNOWN.value
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    command_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
