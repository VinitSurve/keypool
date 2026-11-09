"""Profile management and explicit credential isolation for KEYPOOL (Section 11)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from keypool.config.defaults import DEFAULT_PROFILES
from keypool.core.credential_manager import CredentialMetadata
from keypool.core.exceptions import (
    CredentialNotFoundError,
    ProfileError,
    ProfileNotFoundError,
)
from keypool.security.validation import (
    validate_credential_id,
    validate_profile_name,
    validate_provider_name,
)
from keypool.storage.database import DatabaseManager
from keypool.storage.models import (
    CredentialRecord,
    ProfileAssignmentRecord,
    ProfileRecord,
    utc_now_iso,
)


class ProfileDetails(BaseModel):
    """Structured metadata for a profile and its explicit credential assignments."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    is_builtin: bool = False
    preferred_provider: str | None = None
    selection_strategy: str = "lru"
    created_at: str
    updated_at: str
    assigned_credential_ids: list[str] = Field(default_factory=list)
    credentials: list[CredentialMetadata] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "is_builtin": self.is_builtin,
            "preferred_provider": self.preferred_provider,
            "selection_strategy": self.selection_strategy,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "credential_count": len(self.assigned_credential_ids),
            "assigned_credential_ids": list(self.assigned_credential_ids),
            "credentials": [c.to_public_dict() for c in self.credentials],
        }


class ProfileManager:
    """Manages execution profiles and explicit credential assignments."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def list_profiles(self) -> list[ProfileDetails]:
        """Return all profiles with their assigned credentials."""
        with self._db.session_scope() as session:
            profile_rows = (
                session.execute(select(ProfileRecord).order_by(ProfileRecord.name)).scalars().all()
            )
            assign_rows = (
                session.execute(
                    select(ProfileAssignmentRecord).order_by(
                        ProfileAssignmentRecord.priority,
                        ProfileAssignmentRecord.assigned_at,
                    )
                )
                .scalars()
                .all()
            )
            cred_rows = session.execute(select(CredentialRecord)).scalars().all()
            cred_map = {c.id: c for c in cred_rows}

            profiles_for_cred: dict[str, list[str]] = {}
            creds_for_profile: dict[str, list[str]] = {p.name: [] for p in profile_rows}
            for row in assign_rows:
                profiles_for_cred.setdefault(row.credential_id, []).append(row.profile_name)
                creds_for_profile.setdefault(row.profile_name, []).append(row.credential_id)

            # Order built-ins first (testing, development, production), then custom alphabetically
            builtin_order = {name: i for i, name in enumerate(DEFAULT_PROFILES)}
            sorted_profiles = sorted(
                profile_rows,
                key=lambda p: (
                    0 if p.name in builtin_order else 1,
                    builtin_order.get(p.name, 99),
                    p.name,
                ),
            )

            result: list[ProfileDetails] = []
            for prof in sorted_profiles:
                cids = creds_for_profile.get(prof.name, [])
                creds_meta: list[CredentialMetadata] = []
                for cid in cids:
                    crec = cred_map.get(cid)
                    if crec is not None:
                        creds_meta.append(
                            CredentialMetadata(
                                id=crec.id,
                                name=crec.name,
                                provider=crec.provider,
                                keychain_reference=crec.keychain_reference,
                                enabled=crec.enabled,
                                status=crec.status,
                                quota_domain=crec.quota_domain,
                                profiles=sorted(profiles_for_cred.get(crec.id, [])),
                                created_at=crec.created_at,
                                updated_at=crec.updated_at,
                                last_used_at=crec.last_used_at,
                                last_tested_at=crec.last_tested_at,
                                last_successful_at=crec.last_successful_at,
                                last_failure_at=crec.last_failure_at,
                            )
                        )
                result.append(
                    ProfileDetails(
                        name=prof.name,
                        description=prof.description or "",
                        is_builtin=prof.is_builtin,
                        preferred_provider=prof.preferred_provider,
                        selection_strategy=prof.selection_strategy or "lru",
                        created_at=prof.created_at,
                        updated_at=prof.updated_at,
                        assigned_credential_ids=cids,
                        credentials=creds_meta,
                    )
                )
            return result

    def get_profile(self, profile_name: str) -> ProfileDetails:
        """Retrieve a single profile by name or raise ProfileNotFoundError."""
        clean_name = validate_profile_name(profile_name)
        for prof in self.list_profiles():
            if prof.name == clean_name:
                return prof
        raise ProfileNotFoundError(
            f"Profile '{clean_name}' does not exist.",
            hint="Run 'keypool profile list' to view available profiles or 'keypool profile create <name>' to create one.",
        )

    def create_profile(
        self,
        profile_name: str,
        *,
        description: str = "",
        preferred_provider: str | None = None,
    ) -> ProfileDetails:
        """Create a new empty profile. Newly created profiles never inherit credentials."""
        clean_name = validate_profile_name(profile_name)
        norm_provider = validate_provider_name(preferred_provider) if preferred_provider else None
        now = utc_now_iso()

        with self._db.session_scope() as session:
            existing = session.get(ProfileRecord, clean_name)
            if existing is not None:
                raise ProfileError(
                    f"Profile '{clean_name}' already exists.",
                    hint=f"Run 'keypool profile show {clean_name}' to inspect its assignments.",
                )
            session.add(
                ProfileRecord(
                    name=clean_name,
                    description=description.strip()
                    if description
                    else f"Custom profile '{clean_name}'",
                    is_builtin=False,
                    preferred_provider=norm_provider,
                    created_at=now,
                    updated_at=now,
                )
            )
        return self.get_profile(clean_name)

    def delete_profile(self, profile_name: str) -> ProfileDetails:
        """Delete a custom profile. Built-in profiles cannot be deleted."""
        clean_name = validate_profile_name(profile_name)
        details = self.get_profile(clean_name)
        if details.is_builtin or clean_name in DEFAULT_PROFILES:
            raise ProfileError(
                f"Cannot delete built-in profile '{clean_name}'.",
                hint=f"Default profiles ({', '.join(DEFAULT_PROFILES)}) are permanent.",
            )

        with self._db.session_scope() as session:
            rec = session.get(ProfileRecord, clean_name)
            if rec is not None:
                session.delete(rec)
        return details

    def assign_credential(
        self,
        profile_name: str,
        credential_id: str,
        *,
        priority: int = 100,
    ) -> ProfileDetails:
        """Explicitly assign a credential to a profile."""
        clean_profile = validate_profile_name(profile_name)
        clean_cred_id = validate_credential_id(credential_id)
        now = utc_now_iso()

        with self._db.session_scope() as session:
            prof = session.get(ProfileRecord, clean_profile)
            if prof is None:
                raise ProfileNotFoundError(
                    f"Profile '{clean_profile}' does not exist.",
                    hint="Run 'keypool profile list' to view available profiles.",
                )
            cred = session.get(CredentialRecord, clean_cred_id)
            if cred is None:
                raise CredentialNotFoundError(
                    f"Credential '{clean_cred_id}' not found.",
                    hint="Run 'keypool list' to view available credentials.",
                )

            existing = (
                session.execute(
                    select(ProfileAssignmentRecord).where(
                        ProfileAssignmentRecord.profile_name == clean_profile,
                        ProfileAssignmentRecord.credential_id == clean_cred_id,
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                existing.priority = priority
            else:
                session.add(
                    ProfileAssignmentRecord(
                        profile_name=clean_profile,
                        credential_id=clean_cred_id,
                        priority=priority,
                        assigned_at=now,
                    )
                )
            prof.updated_at = now

        return self.get_profile(clean_profile)

    def unassign_credential(self, profile_name: str, credential_id: str) -> ProfileDetails:
        """Remove a credential assignment from a profile."""
        clean_profile = validate_profile_name(profile_name)
        clean_cred_id = validate_credential_id(credential_id)
        now = utc_now_iso()

        with self._db.session_scope() as session:
            prof = session.get(ProfileRecord, clean_profile)
            if prof is None:
                raise ProfileNotFoundError(f"Profile '{clean_profile}' does not exist.")
            existing = (
                session.execute(
                    select(ProfileAssignmentRecord).where(
                        ProfileAssignmentRecord.profile_name == clean_profile,
                        ProfileAssignmentRecord.credential_id == clean_cred_id,
                    )
                )
                .scalars()
                .first()
            )
            if existing is None:
                raise ProfileError(
                    f"Credential '{clean_cred_id}' is not assigned to profile '{clean_profile}'."
                )
            session.delete(existing)
            prof.updated_at = now

        return self.get_profile(clean_profile)
