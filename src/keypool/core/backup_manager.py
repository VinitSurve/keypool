"""Authenticated encrypted backup and restore engine (Section 19)."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from keypool.core.credential_manager import CredentialManager
from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.storage.database import DatabaseManager
from keypool.storage.keychain import SecretStore
from keypool.storage.models import (
    BudgetRecord,
    CredentialRecord,
    HealthState,
    ModelAliasRecord,
    ProfileAssignmentRecord,
    ProfileRecord,
    ProjectRecord,
    utc_now_iso,
)

_BACKUP_MAGIC = b"KPBACKUP"
_BACKUP_VERSION = 2
_PBKDF2_ITERATIONS = 600_000
_SALT_LEN = 16
_NONCE_LEN = 12


class RestoreReport(BaseModel):
    """Summary of items restored from an encrypted backup."""

    model_config = ConfigDict(extra="forbid")

    credentials_restored: int = 0
    profiles_restored: int = 0
    projects_restored: int = 0
    budgets_restored: int = 0
    warnings: list[str] = Field(default_factory=list)


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


class BackupManager:
    """Creates and restores authenticated AES-256-GCM encrypted backup bundles."""

    def __init__(
        self,
        db: DatabaseManager,
        secret_store: SecretStore,
        credential_manager: CredentialManager,
    ) -> None:
        self._db = db
        self._secret_store = secret_store
        self._cred_manager = credential_manager

    def create_backup(
        self,
        target_path: Path | str,
        *,
        password: str,
        include_secrets: bool = True,
        provider: str | None = None,
    ) -> int:
        """Create an encrypted backup bundle and return the total number of credentials archived."""
        if not password or len(password) < 8:
            raise InvalidArgumentError("Backup encryption password must be at least 8 characters.")

        dest = Path(target_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        bundle: dict[str, Any] = {
            "version": _BACKUP_VERSION,
            "created_at": utc_now_iso(),
            "credentials": [],
            "profiles": [],
            "assignments": [],
            "projects": [],
            "budgets": [],
            "aliases": [],
        }

        with self._db.session_scope() as session:
            cred_stmt = select(CredentialRecord)
            if provider:
                cred_stmt = cred_stmt.where(CredentialRecord.provider == provider.strip().lower())
            creds = session.execute(cred_stmt).scalars().all()

            for c in creds:
                secret_val: str | None = None
                if include_secrets:
                    with contextlib.suppress(Exception):
                        secret_val = self._secret_store.get_secret(c.keychain_reference)

                bundle["credentials"].append(
                    {
                        "id": c.id,
                        "name": c.name,
                        "provider": c.provider,
                        "keychain_reference": c.keychain_reference,
                        "enabled": c.enabled,
                        "status": c.status,
                        "quota_domain": c.quota_domain,
                        "tags": c.tags,
                        "priority": c.priority,
                        "description": c.description,
                        "expires_at": c.expires_at,
                        "rotation_interval_days": c.rotation_interval_days,
                        "last_rotated_at": c.last_rotated_at,
                        "secret_fingerprint": c.secret_fingerprint,
                        "secret_value": secret_val,
                    }
                )

            # Export profiles and assignments
            profiles = session.execute(select(ProfileRecord)).scalars().all()
            for p in profiles:
                bundle["profiles"].append(
                    {
                        "name": p.name,
                        "description": p.description,
                        "is_builtin": p.is_builtin,
                        "preferred_provider": p.preferred_provider,
                        "selection_strategy": p.selection_strategy,
                    }
                )

            assignments = session.execute(select(ProfileAssignmentRecord)).scalars().all()
            for a in assignments:
                bundle["assignments"].append(
                    {
                        "profile_name": a.profile_name,
                        "credential_id": a.credential_id,
                        "priority": a.priority,
                    }
                )

            # Export projects
            projects = session.execute(select(ProjectRecord)).scalars().all()
            for pr in projects:
                bundle["projects"].append(
                    {
                        "id": pr.id,
                        "name": pr.name,
                        "root_dir": pr.root_dir,
                        "profile_name": pr.profile_name,
                        "provider_overrides": pr.provider_overrides,
                        "model_aliases": pr.model_aliases,
                    }
                )

            # Export budgets
            budgets = session.execute(select(BudgetRecord)).scalars().all()
            for b in budgets:
                bundle["budgets"].append(
                    {
                        "id": b.id,
                        "name": b.name,
                        "scope_type": b.scope_type,
                        "scope_target": b.scope_target,
                        "amount_limit": b.amount_limit,
                        "currency": b.currency,
                        "period": b.period,
                        "soft_limit_percent": b.soft_limit_percent,
                        "hard_limit_enabled": b.hard_limit_enabled,
                    }
                )

            # Export aliases
            aliases = session.execute(select(ModelAliasRecord)).scalars().all()
            for al in aliases:
                bundle["aliases"].append(
                    {
                        "alias": al.alias,
                        "target": al.target,
                        "description": al.description,
                    }
                )

        plaintext = json.dumps(bundle).encode("utf-8")
        salt = os.urandom(_SALT_LEN)
        nonce = os.urandom(_NONCE_LEN)
        key = _derive_key(password, salt)

        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=_BACKUP_MAGIC)

        # Header: MAGIC (8B) + VERSION (2B) + SALT (16B) + NONCE (12B) + CIPHERTEXT
        header = _BACKUP_MAGIC + _BACKUP_VERSION.to_bytes(2, "big") + salt + nonce
        dest.write_bytes(header + ciphertext)

        return len(bundle["credentials"])

    def restore_backup(
        self,
        backup_path: Path | str,
        *,
        password: str,
        overwrite_existing: bool = True,
    ) -> RestoreReport:
        """Decrypt an encrypted backup bundle and restore metadata and Keychain secrets."""
        source = Path(backup_path).expanduser().resolve()
        if not source.is_file():
            raise InvalidArgumentError(f"Backup file '{source}' does not exist.")

        raw_data = source.read_bytes()
        min_len = len(_BACKUP_MAGIC) + 2 + _SALT_LEN + _NONCE_LEN + 16
        if len(raw_data) < min_len:
            raise SecurityError("Backup file is truncated or corrupted.")

        magic = raw_data[: len(_BACKUP_MAGIC)]
        if magic != _BACKUP_MAGIC:
            raise SecurityError("Unrecognized backup file format or magic header mismatch.")

        offset = len(_BACKUP_MAGIC)
        version = int.from_bytes(raw_data[offset : offset + 2], "big")
        if version > _BACKUP_VERSION:
            raise SecurityError(
                f"Backup format version {version} is newer than supported version {_BACKUP_VERSION}."
            )
        offset += 2
        salt = raw_data[offset : offset + _SALT_LEN]
        offset += _SALT_LEN
        nonce = raw_data[offset : offset + _NONCE_LEN]
        offset += _NONCE_LEN
        ciphertext = raw_data[offset:]

        key = _derive_key(password, salt)
        aesgcm = AESGCM(key)

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=_BACKUP_MAGIC)
        except InvalidTag as err:
            raise SecurityError(
                "Decryption failed: incorrect password or corrupted backup archive.",
                hint="Verify that the correct password was provided.",
            ) from err

        bundle = json.loads(plaintext.decode("utf-8"))
        report = RestoreReport()
        now = utc_now_iso()

        with self._db.session_scope() as session:
            # 1. Restore profiles
            for p_dict in bundle.get("profiles", []):
                p_name = p_dict["name"]
                existing_p = session.get(ProfileRecord, p_name)
                if existing_p is None:
                    session.add(
                        ProfileRecord(
                            name=p_name,
                            description=p_dict.get("description"),
                            is_builtin=p_dict.get("is_builtin", False),
                            preferred_provider=p_dict.get("preferred_provider"),
                            selection_strategy=p_dict.get("selection_strategy", "lru"),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    report.profiles_restored += 1

            # 2. Restore credentials and secrets
            for c_dict in bundle.get("credentials", []):
                cid = c_dict["id"]
                secret_val = c_dict.get("secret_value")
                keychain_ref = c_dict["keychain_reference"]

                if secret_val:
                    try:
                        self._secret_store.set_secret(
                            keychain_ref, secret_val, allow_overwrite=overwrite_existing
                        )
                    except Exception as k_err:
                        report.warnings.append(f"Failed to restore secret for {cid}: {k_err}")

                existing_c = session.get(CredentialRecord, cid)
                if existing_c is not None and overwrite_existing:
                    existing_c.name = c_dict["name"]
                    existing_c.enabled = c_dict.get("enabled", True)
                    existing_c.tags = c_dict.get("tags", "[]")
                    existing_c.priority = c_dict.get("priority", 100)
                    existing_c.description = c_dict.get("description")
                    existing_c.secret_fingerprint = c_dict.get("secret_fingerprint")
                    existing_c.updated_at = now
                    report.credentials_restored += 1
                elif existing_c is None:
                    session.add(
                        CredentialRecord(
                            id=cid,
                            name=c_dict["name"],
                            provider=c_dict["provider"],
                            keychain_reference=keychain_ref,
                            enabled=c_dict.get("enabled", True),
                            status=HealthState.UNTESTED.value,
                            quota_domain=c_dict.get("quota_domain"),
                            tags=c_dict.get("tags", "[]"),
                            priority=c_dict.get("priority", 100),
                            description=c_dict.get("description"),
                            expires_at=c_dict.get("expires_at"),
                            rotation_interval_days=c_dict.get("rotation_interval_days"),
                            last_rotated_at=c_dict.get("last_rotated_at", now),
                            secret_fingerprint=c_dict.get("secret_fingerprint"),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    report.credentials_restored += 1

            # 3. Restore assignments
            for a_dict in bundle.get("assignments", []):
                p_name = a_dict["profile_name"]
                c_id = a_dict["credential_id"]
                existing_a = session.execute(
                    select(ProfileAssignmentRecord).where(
                        ProfileAssignmentRecord.profile_name == p_name,
                        ProfileAssignmentRecord.credential_id == c_id,
                    )
                ).scalar_one_or_none()
                if existing_a is None:
                    session.add(
                        ProfileAssignmentRecord(
                            profile_name=p_name,
                            credential_id=c_id,
                            priority=a_dict.get("priority", 100),
                            assigned_at=now,
                        )
                    )

            # 4. Restore projects
            for pr_dict in bundle.get("projects", []):
                pr_id = pr_dict["id"]
                existing_pr = session.get(ProjectRecord, pr_id)
                if existing_pr is None:
                    session.add(
                        ProjectRecord(
                            id=pr_id,
                            name=pr_dict["name"],
                            root_dir=pr_dict["root_dir"],
                            profile_name=pr_dict.get("profile_name"),
                            provider_overrides=pr_dict.get("provider_overrides"),
                            model_aliases=pr_dict.get("model_aliases"),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    report.projects_restored += 1

            # 5. Restore budgets
            for b_dict in bundle.get("budgets", []):
                b_id = b_dict["id"]
                existing_b = session.get(BudgetRecord, b_id)
                if existing_b is None:
                    session.add(
                        BudgetRecord(
                            id=b_id,
                            name=b_dict["name"],
                            scope_type=b_dict["scope_type"],
                            scope_target=b_dict.get("scope_target"),
                            amount_limit=float(b_dict["amount_limit"]),
                            currency=b_dict.get("currency", "USD"),
                            period=b_dict.get("period", "monthly"),
                            soft_limit_percent=float(b_dict.get("soft_limit_percent", 80.0)),
                            hard_limit_enabled=b_dict.get("hard_limit_enabled", False),
                            created_at=now,
                        )
                    )
                    report.budgets_restored += 1

            # 6. Restore aliases
            for al_dict in bundle.get("aliases", []):
                al_name = al_dict["alias"]
                existing_al = session.get(ModelAliasRecord, al_name)
                if existing_al is None:
                    session.add(
                        ModelAliasRecord(
                            alias=al_name,
                            target=al_dict["target"],
                            description=al_dict.get("description"),
                            created_at=now,
                            updated_at=now,
                        )
                    )

        return report
