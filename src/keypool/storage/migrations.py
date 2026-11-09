"""Schema versioning, migrations, and default profile seeding for KEYPOOL SQLite database."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from keypool.config.defaults import DEFAULT_PROFILES
from keypool.core.exceptions import StorageError
from keypool.storage.models import (
    Base,
    CatalogModelRecord,
    ProfileRecord,
    SchemaMigrationRecord,
    utc_now_iso,
)
from keypool.version import SCHEMA_VERSION

MigrationCallable = Callable[[Session], None]

_BUILTIN_PROFILE_DESCRIPTIONS: dict[str, str] = {
    "testing": "Isolated profile for automated tests, CI runs, and integration verification.",
    "development": "Local development and interactive experimentation profile.",
    "production": "Production-grade execution profile with strict credential isolation.",
}


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


def _safe_add_column(session: Session, table_name: str, col_name: str, col_type: str) -> None:
    bind = session.get_bind()
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns(table_name)}
    if col_name not in cols:
        session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))


def _migration_v1(session: Session) -> None:
    """Initial v1 schema baseline and built-in profile seeding."""
    _seed_builtin_profiles(session)


def _migration_v2(session: Session) -> None:
    """Schema v2: project awareness, budgets, model catalog, credential tags & intelligence."""
    # Add columns to credentials table
    _safe_add_column(session, "credentials", "tags", "VARCHAR(255) NOT NULL DEFAULT '[]'")
    _safe_add_column(session, "credentials", "priority", "INTEGER NOT NULL DEFAULT 100")
    _safe_add_column(session, "credentials", "description", "TEXT")
    _safe_add_column(session, "credentials", "expires_at", "VARCHAR(40)")
    _safe_add_column(session, "credentials", "rotation_interval_days", "INTEGER")
    _safe_add_column(session, "credentials", "last_rotated_at", "VARCHAR(40)")
    _safe_add_column(session, "credentials", "secret_fingerprint", "VARCHAR(64)")

    # Add columns to profiles table
    _safe_add_column(
        session, "profiles", "selection_strategy", "VARCHAR(32) NOT NULL DEFAULT 'lru'"
    )

    # Add columns to usage_events table
    _safe_add_column(session, "usage_events", "cost_usd", "FLOAT")
    _safe_add_column(session, "usage_events", "project_id", "VARCHAR(64)")
    _safe_add_column(session, "usage_events", "input_tokens", "INTEGER")
    _safe_add_column(session, "usage_events", "output_tokens", "INTEGER")

    # Seed model catalog
    seed_models = [
        ("openai", "gpt-4o", "GPT-4o", 128000, 16384, "text,vision,tool_calling", 2.50, 10.00),
        (
            "openai",
            "gpt-4o-mini",
            "GPT-4o Mini",
            128000,
            16384,
            "text,vision,tool_calling",
            0.15,
            0.60,
        ),
        ("openai", "o1", "o1 Reasoning", 200000, 100000, "text,reasoning", 15.00, 60.00),
        (
            "anthropic",
            "claude-3-5-sonnet-latest",
            "Claude 3.5 Sonnet",
            200000,
            8192,
            "text,vision,tool_calling",
            3.00,
            15.00,
        ),
        (
            "anthropic",
            "claude-3-5-haiku-latest",
            "Claude 3.5 Haiku",
            200000,
            8192,
            "text,tool_calling",
            0.80,
            4.00,
        ),
        (
            "groq",
            "llama-3.1-8b-instant",
            "Llama 3.1 8B Instant",
            128000,
            8192,
            "text,tool_calling",
            0.05,
            0.08,
        ),
        (
            "groq",
            "llama-3.3-70b-versatile",
            "Llama 3.3 70B Versatile",
            128000,
            32768,
            "text,tool_calling",
            0.59,
            0.79,
        ),
        (
            "gemini",
            "gemini-2.5-flash",
            "Gemini 2.5 Flash",
            1000000,
            8192,
            "text,vision,audio,tool_calling",
            0.075,
            0.30,
        ),
        ("deepseek", "deepseek-chat", "DeepSeek-V3", 64000, 8192, "text,tool_calling", 0.14, 0.28),
        (
            "mistral",
            "mistral-small-latest",
            "Mistral Small",
            32000,
            8192,
            "text,tool_calling",
            0.20,
            0.60,
        ),
    ]
    now = utc_now_iso()
    for prov, mid, dname, ctx, out, mods, inp_p, out_p in seed_models:
        existing = session.execute(
            select(CatalogModelRecord).where(
                CatalogModelRecord.provider == prov, CatalogModelRecord.model_id == mid
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                CatalogModelRecord(
                    provider=prov,
                    model_id=mid,
                    display_name=dname,
                    context_window=ctx,
                    max_output_tokens=out,
                    modalities=mods,
                    input_price_per_1m=inp_p,
                    output_price_per_1m=out_p,
                    is_deprecated=False,
                    updated_at=now,
                )
            )


MIGRATIONS: list[tuple[int, str, MigrationCallable]] = [
    (1, "Initial schema: credentials, profiles, health, models, and usage tables", _migration_v1),
    (
        2,
        "Schema v2: project awareness, budgets, model catalog, credential tags & intelligence",
        _migration_v2,
    ),
]


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

            # Always ensure built-in profiles are present
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
