"""Project-level configuration, workspace discovery, and profile binding (Section 9)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from keypool.core.exceptions import InvalidArgumentError, StorageError
from keypool.storage.database import DatabaseManager
from keypool.storage.models import ProjectRecord, utc_now_iso


class ProjectContext(BaseModel):
    """Resolved project workspace context."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    root_dir: str
    profile_name: str | None = None
    provider_overrides: dict[str, str] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root_dir": self.root_dir,
            "profile_name": self.profile_name,
            "provider_overrides": dict(self.provider_overrides),
            "model_aliases": dict(self.model_aliases),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectManager:
    """Manages project workspaces and contextual configuration overrides."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    @staticmethod
    def _parse_dict(raw: str | None) -> dict[str, str]:
        if not raw:
            return {}
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                return {str(k): str(v) for k, v in val.items()}
        except Exception:  # noqa: S110
            pass
        return {}

    @classmethod
    def _record_to_context(cls, rec: ProjectRecord) -> ProjectContext:
        return ProjectContext(
            id=rec.id,
            name=rec.name,
            root_dir=rec.root_dir,
            profile_name=rec.profile_name,
            provider_overrides=cls._parse_dict(rec.provider_overrides),
            model_aliases=cls._parse_dict(rec.model_aliases),
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    def register_project(
        self,
        *,
        name: str,
        root_dir: Path | str,
        profile_name: str | None = None,
        provider_overrides: dict[str, str] | None = None,
        model_aliases: dict[str, str] | None = None,
    ) -> ProjectContext:
        """Register a directory workspace as a KEYPOOL-managed project."""
        clean_name = name.strip()
        if not clean_name:
            raise InvalidArgumentError("Project name cannot be empty.")

        resolved_path = Path(root_dir).expanduser().resolve()
        if not resolved_path.exists() or not resolved_path.is_dir():
            raise InvalidArgumentError(
                f"Directory '{resolved_path}' does not exist or is not a directory."
            )

        norm_root = str(resolved_path)
        now = utc_now_iso()
        prov_json = json.dumps(provider_overrides or {}) if provider_overrides else None
        alias_json = json.dumps(model_aliases or {}) if model_aliases else None

        with self._db.session_scope() as session:
            existing = session.execute(
                select(ProjectRecord).where(ProjectRecord.root_dir == norm_root)
            ).scalar_one_or_none()

            if existing is not None:
                existing.name = clean_name
                existing.profile_name = (
                    profile_name.strip().lower() if profile_name else existing.profile_name
                )
                if provider_overrides is not None:
                    existing.provider_overrides = prov_json
                if model_aliases is not None:
                    existing.model_aliases = alias_json
                existing.updated_at = now
                session.flush()
                rec = existing
            else:
                proj_id = f"proj_{secrets.token_hex(4)}"
                rec = ProjectRecord(
                    id=proj_id,
                    name=clean_name,
                    root_dir=norm_root,
                    profile_name=profile_name.strip().lower() if profile_name else None,
                    provider_overrides=prov_json,
                    model_aliases=alias_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(rec)
                session.flush()

            # Attempt to create .keypool directory in project root
            try:
                dot_dir = resolved_path / ".keypool"
                dot_dir.mkdir(parents=True, exist_ok=True)
                cfg_path = dot_dir / "config.toml"
                if not cfg_path.exists():
                    cfg_content = (
                        f"# KEYPOOL Project Configuration\n"
                        f'project_id = "{rec.id}"\n'
                        f'name = "{rec.name}"\n'
                        f'profile = "{rec.profile_name or "development"}"\n'
                    )
                    cfg_path.write_text(cfg_content, encoding="utf-8")
            except Exception:  # noqa: S110
                pass

            return self._record_to_context(rec)

    def list_projects(self) -> list[ProjectContext]:
        """List all registered projects ordered by creation time."""
        with self._db.session_scope() as session:
            records = (
                session.execute(select(ProjectRecord).order_by(ProjectRecord.created_at.desc()))
                .scalars()
                .all()
            )
            return [self._record_to_context(r) for r in records]

    def get_project(self, project_id_or_name: str) -> ProjectContext | None:
        """Fetch project by ID or friendly name."""
        clean = project_id_or_name.strip()
        with self._db.session_scope() as session:
            rec = session.get(ProjectRecord, clean)
            if rec is None:
                rec = session.execute(
                    select(ProjectRecord).where(ProjectRecord.name == clean)
                ).scalar_one_or_none()
            if rec is None:
                return None
            return self._record_to_context(rec)

    def remove_project(self, project_id: str) -> bool:
        """Remove a registered project."""
        clean = project_id.strip()
        with self._db.session_scope() as session:
            rec = session.get(ProjectRecord, clean)
            if rec is None:
                return False
            session.delete(rec)
            return True

    def discover_project(self, start_path: Path | str | None = None) -> ProjectContext | None:
        """Walk up directory tree from start_path to discover registered project or .keypool config."""
        current = Path(start_path or Path.cwd()).expanduser().resolve()
        registered = self.list_projects()
        reg_by_dir = {Path(p.root_dir).resolve(): p for p in registered}

        # Check ancestors
        for p in [current, *current.parents]:
            if p in reg_by_dir:
                return reg_by_dir[p]
            # Check for .keypool/config.toml
            cfg_file = p / ".keypool" / "config.toml"
            if cfg_file.is_file():
                # Attempt to register/load
                try:
                    name = p.name
                    return self.register_project(name=name, root_dir=p)
                except StorageError:
                    pass
        return None
