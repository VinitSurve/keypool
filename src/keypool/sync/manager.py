"""Multi-device synchronization orchestrator and conflict resolution engine."""

from __future__ import annotations

import contextlib
import platform
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from keypool.cli.formatting import RuntimeContext
from keypool.core.alias_manager import ModelAliasManager
from keypool.core.budget_manager import BudgetManager
from keypool.core.project_manager import ProjectManager
from keypool.security.validation import ensure_secure_file
from keypool.storage.models import (
    CredentialRecord,
    DeviceRecord,
    ProfileAssignmentRecord,
    ProfileRecord,
    SyncLogRecord,
    utc_now_iso,
)
from keypool.sync.protocol import decrypt_sync_payload, encrypt_sync_payload


class SyncManager:
    """Manages enrolled devices, bundle exports, bundle imports, and state merging."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self.ctx = ctx
        self.db = ctx.db
        self.cred_mgr = ctx.credential_manager
        self.profile_mgr = ctx.profile_manager
        self.project_mgr = ProjectManager(ctx.db)
        self.budget_mgr = BudgetManager(ctx.db)
        self.alias_mgr = ModelAliasManager(ctx.db)

    def list_devices(self) -> list[dict[str, Any]]:
        """List all enrolled synchronization devices."""
        with self.db.session_scope() as session:
            rows = (
                session.execute(select(DeviceRecord).order_by(desc(DeviceRecord.registered_at)))
                .scalars()
                .all()
            )
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "device_fingerprint": r.device_fingerprint,
                    "is_current_device": r.is_current_device,
                    "is_revoked": r.is_revoked,
                    "registered_at": r.registered_at,
                    "last_synced_at": r.last_synced_at,
                }
                for r in rows
            ]

    def register_device(self, name: str) -> dict[str, Any]:
        """Enroll a new trusted device."""
        device_id = f"dev_{uuid.uuid4().hex[:12]}"
        fingerprint = uuid.uuid4().hex[:16]
        now = utc_now_iso()

        with self.db.session_scope() as session:
            rec = DeviceRecord(
                id=device_id,
                name=name.strip(),
                device_fingerprint=fingerprint,
                is_current_device=False,
                is_revoked=False,
                registered_at=now,
                last_synced_at=None,
            )
            session.add(rec)
            return {
                "id": rec.id,
                "name": rec.name,
                "device_fingerprint": rec.device_fingerprint,
                "is_current_device": False,
                "is_revoked": False,
                "registered_at": rec.registered_at,
            }

    def revoke_device(self, device_id: str) -> bool:
        """Revoke a previously enrolled synchronization device."""
        with self.db.session_scope() as session:
            rec = session.get(DeviceRecord, device_id.strip())
            if rec is not None:
                rec.is_revoked = True
                return True
            return False

    def export_sync_bundle(
        self,
        passphrase: str,
        output_file: Path | str | None = None,
    ) -> bytes:
        """Export all credentials, profiles, projects, budgets, and aliases into an encrypted bundle."""
        creds = self.cred_mgr.list_credentials()
        exported_creds: list[dict[str, Any]] = []

        for c in creds:
            secret = self.cred_mgr.retrieve_secret(c.id)
            if not secret:
                continue
            exported_creds.append(
                {
                    "provider": c.provider,
                    "name": c.name,
                    "secret": secret,
                    "quota_domain": c.quota_domain,
                    "tags": c.tags,
                    "priority": c.priority,
                    "description": c.description,
                    "expires_at": c.expires_at,
                    "rotation_interval_days": c.rotation_interval_days,
                    "fingerprint": c.secret_fingerprint,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                }
            )

        with self.db.session_scope() as session:
            # Profiles and assignments
            profiles_rows = session.execute(select(ProfileRecord)).scalars().all()
            profiles_data = [
                {
                    "name": p.name,
                    "description": p.description,
                    "selection_strategy": p.selection_strategy,
                }
                for p in profiles_rows
            ]

            assign_rows = session.execute(select(ProfileAssignmentRecord)).scalars().all()
            assign_data = [
                {
                    "profile_name": a.profile_name,
                    "credential_id": a.credential_id,
                    "priority": a.priority,
                }
                for a in assign_rows
            ]

        projects_data = [p.to_public_dict() for p in self.project_mgr.list_projects()]
        budgets_data = [b.to_public_dict() for b in self.budget_mgr.list_budgets()]
        aliases_data = self.alias_mgr.list_aliases()

        payload = {
            "protocol_version": 1,
            "exported_at": utc_now_iso(),
            "origin_node": platform.node(),
            "credentials": exported_creds,
            "profiles": profiles_data,
            "assignments": assign_data,
            "projects": projects_data,
            "budgets": budgets_data,
            "aliases": aliases_data,
        }

        bundle_bytes = encrypt_sync_payload(payload, passphrase)

        if output_file:
            path = Path(output_file).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle_bytes)
            ensure_secure_file(path)

        # Log audit entry
        with self.db.session_scope() as session:
            session.add(
                SyncLogRecord(
                    id=f"sync_{uuid.uuid4().hex[:10]}",
                    device_id="dev_current_host",
                    action="export",
                    credentials_synced=len(exported_creds),
                    conflicts_resolved=0,
                    timestamp=utc_now_iso(),
                )
            )

        return bundle_bytes

    def import_sync_bundle(
        self,
        bundle_bytes: bytes,
        passphrase: str,
    ) -> dict[str, Any]:
        """Decrypt, verify, and merge synchronization payload using Last-Write-Wins."""
        data = decrypt_sync_payload(bundle_bytes, passphrase)

        credentials_synced = 0
        conflicts_resolved = 0

        # Import credentials
        incoming_creds = data.get("credentials", [])
        for c in incoming_creds:
            fingerprint = c.get("fingerprint")
            secret = c.get("secret")
            if not secret:
                continue

            # Look up existing credential by fingerprint
            existing_cred = None
            if fingerprint:
                with self.db.session_scope() as session:
                    rec = session.execute(
                        select(CredentialRecord).where(
                            CredentialRecord.provider == c["provider"],
                            CredentialRecord.secret_fingerprint == fingerprint,
                        )
                    ).scalar_one_or_none()
                    if rec is not None:
                        existing_cred = rec

            if existing_cred is not None:
                # Conflict resolution: compare timestamps
                conflicts_resolved += 1
                if c.get("updated_at", "") > existing_cred.updated_at:
                    with self.db.session_scope() as session:
                        target_rec = session.get(CredentialRecord, existing_cred.id)
                        if target_rec is not None:
                            if c.get("name"):
                                target_rec.name = c["name"]
                            if c.get("priority") is not None:
                                target_rec.priority = c["priority"]
                            if c.get("description") is not None:
                                target_rec.description = c["description"]
                            if c.get("quota_domain") is not None:
                                target_rec.quota_domain = c["quota_domain"]
                            target_rec.updated_at = utc_now_iso()
            else:
                # Add new credential
                self.cred_mgr.add_credential(
                    provider=c["provider"],
                    name=c["name"],
                    secret=secret,
                    quota_domain=c.get("quota_domain"),
                    tags=c.get("tags"),
                    priority=c.get("priority", 100),
                    description=c.get("description"),
                    expires_at=c.get("expires_at"),
                    rotation_interval_days=c.get("rotation_interval_days"),
                    allow_duplicate=True,
                )
                credentials_synced += 1

        # Import Profiles
        for p in data.get("profiles", []):
            pname = p.get("name")
            if not pname:
                continue
            with contextlib.suppress(Exception):
                try:
                    self.profile_mgr.get_profile(pname)
                except Exception:
                    self.profile_mgr.create_profile(
                        profile_name=pname,
                        description=p.get("description", ""),
                        preferred_provider=p.get("preferred_provider"),
                    )

        # Import Projects
        for p in data.get("projects", []):
            if p.get("root_dir") and self.project_mgr.get_project(p["name"]) is None:
                with contextlib.suppress(Exception):
                    self.project_mgr.register_project(
                        name=p["name"],
                        root_dir=p["root_dir"],
                        profile_name=p.get("profile_name"),
                    )

        # Import Budgets
        for b in data.get("budgets", []):
            with contextlib.suppress(Exception):
                self.budget_mgr.set_budget(
                    name=b["name"],
                    amount_limit=float(b["amount_limit"]),
                    scope_type=b.get("scope_type", "global"),
                    scope_target=b.get("scope_target"),
                    currency=b.get("currency", "USD"),
                    period=b.get("period", "monthly"),
                    soft_limit_percent=float(b.get("soft_limit_percent", 80.0)),
                    hard_limit_enabled=bool(b.get("hard_limit_enabled", False)),
                )

        # Import Aliases
        for a in data.get("aliases", []):
            self.alias_mgr.set_alias(
                alias=a["alias"],
                target=a["target"],
                description=a.get("description"),
            )

        # Log audit entry
        with self.db.session_scope() as session:
            session.add(
                SyncLogRecord(
                    id=f"sync_{uuid.uuid4().hex[:10]}",
                    device_id="dev_current_host",
                    action="import",
                    credentials_synced=credentials_synced,
                    conflicts_resolved=conflicts_resolved,
                    timestamp=utc_now_iso(),
                )
            )

        return {
            "credentials_synced": credentials_synced,
            "conflicts_resolved": conflicts_resolved,
            "total_incoming": len(incoming_creds),
        }
