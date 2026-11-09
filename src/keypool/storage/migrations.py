"""Schema versioning, migrations, and default profile seeding for KEYPOOL SQLite database."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from keypool.config.defaults import DEFAULT_PROFILES
from keypool.core.exceptions import StorageError
from keypool.storage.models import Base, ProfileRecord, SchemaMigrationRecord, utc_now_iso
from keypool.version import SCHEMA_VERSION

MigrationCallable = Callable[[Session], None]

_BUILTIN_PROFILE_DESCRIPTIONS: dict[str, str] = {
    "testing": "Isolated profile for automated tests, CI runs, and integration verification.",
    "development": "Local development and interactive experimentation profile.",
    "production": "Production-grade execution profile with strict credential isolation.",
}


def _migration_v1(session: Session) -> None:
    """Initial v1 schema baseline and built-in profile seeding."""
    _seed_builtin_profiles(session)


MIGRATIONS: list[tuple[int, str, MigrationCallable]] = [
    (1, "Initial schema: credentials, profiles, health, models, and usage tables", _migration_v1),
]


def _seed_builtin_profiles(session: Session) -> None:
    """Ensure the three default profiles (testing, development, production) exist."""
    now = utc_now_iso()
    for profile_name in DEFAULT_PROFILES:
        existing = session.get(ProfileRecord, profile_name)
        if existing is None:
            session.add(
                ProfileRecord(
                    name=profile_name,
                    description=_BUILTIN_PROFILE_DESCRIPTIONS.get(profile_name, ""),
                    is_builtin=True,
                    preferred_provider=None,
                    created_at=now,
                    updated_at=now,
                )
            )


def apply_migrations(engine: Engine) -> int:
    """Initialize tables and apply any pending migrations inside a transaction."""
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session, session.begin():
            current_records = (
                session.execute(
                    select(SchemaMigrationRecord).order_by(SchemaMigrationRecord.version)
                )
                .scalars()
                .all()
            )
            applied_versions = {rec.version for rec in current_records}

            for version, description, migration_fn in MIGRATIONS:
                if version not in applied_versions:
                    migration_fn(session)
                    session.add(
                        SchemaMigrationRecord(
                            version=version,
                            applied_at=utc_now_iso(),
                            description=description,
                        )
                    )

            # Always ensure built-in profiles are present even if DB already at v1
            _seed_builtin_profiles(session)

        return get_current_schema_version(engine)
    except Exception as exc:
        raise StorageError(f"Database migration failed: {exc}") from exc


def get_current_schema_version(engine: Engine) -> int:
    """Return the highest applied schema migration version, or 0 if uninitialized."""
    inspector = inspect(engine)
    if not inspector.has_table("schema_migrations"):
        return 0
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations"))
        val = result.scalar()
        return int(val) if val is not None else 0


def is_schema_up_to_date(engine: Engine) -> bool:
    """Return True if the database is at the current expected SCHEMA_VERSION."""
    return get_current_schema_version(engine) == SCHEMA_VERSION
