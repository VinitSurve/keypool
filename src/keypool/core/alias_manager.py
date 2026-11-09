"""Model routing aliases management."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from keypool.storage.database import DatabaseManager
from keypool.storage.models import ModelAliasRecord, utc_now_iso


class ModelAliasManager:
    """Manages friendly model aliases mapping to provider/model endpoints."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def list_aliases(self) -> list[dict[str, Any]]:
        """Return all configured model aliases."""
        with self._db.session_scope() as session:
            rows = (
                session.execute(select(ModelAliasRecord).order_by(ModelAliasRecord.alias))
                .scalars()
                .all()
            )
            return [
                {
                    "alias": r.alias,
                    "target": r.target,
                    "description": r.description or "",
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]

    def get_alias(self, alias: str) -> dict[str, Any] | None:
        """Lookup a specific alias by name."""
        clean_alias = alias.strip().lower()
        with self._db.session_scope() as session:
            r = session.get(ModelAliasRecord, clean_alias)
            if r is None:
                return None
            return {
                "alias": r.alias,
                "target": r.target,
                "description": r.description or "",
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }

    def set_alias(self, alias: str, target: str, description: str | None = None) -> dict[str, Any]:
        """Create or update a model alias."""
        clean_alias = alias.strip().lower()
        clean_target = target.strip()
        now = utc_now_iso()

        with self._db.session_scope() as session:
            existing = session.get(ModelAliasRecord, clean_alias)
            if existing is not None:
                existing.target = clean_target
                if description is not None:
                    existing.description = description
                existing.updated_at = now
            else:
                existing = ModelAliasRecord(
                    alias=clean_alias,
                    target=clean_target,
                    description=description,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)

            return {
                "alias": existing.alias,
                "target": existing.target,
                "description": existing.description or "",
                "created_at": existing.created_at,
                "updated_at": existing.updated_at,
            }

    def remove_alias(self, alias: str) -> bool:
        """Remove a model alias."""
        clean_alias = alias.strip().lower()
        with self._db.session_scope() as session:
            existing = session.get(ModelAliasRecord, clean_alias)
            if existing is not None:
                session.delete(existing)
                return True
            return False
