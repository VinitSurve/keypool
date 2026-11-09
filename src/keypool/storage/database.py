"""SQLite database engine, session management, security guards, and integrity checks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from keypool.config.settings import AppPaths
from keypool.core.exceptions import SecurityError, StorageError
from keypool.security.redaction import redact_text, register_secret_for_redaction
from keypool.security.validation import ensure_secure_directory, ensure_secure_file
from keypool.storage.migrations import apply_migrations, get_current_schema_version
from keypool.storage.models import (
    CredentialRecord,
    HealthObservationRecord,
    UsageEventRecord,
)


def _enforce_no_secret_in_cred_record(target: CredentialRecord) -> None:
    """Guard ensuring CredentialRecord never stores a raw secret in any column."""
    if not target.keychain_reference.startswith("keypool:"):
        register_secret_for_redaction(target.keychain_reference)
        raise SecurityError(
            "Invalid keychain_reference format: must start with 'keypool:'. Never store raw secrets in SQLite."
        )
    if target.last_error_message:
        target.last_error_message = redact_text(target.last_error_message)


@event.listens_for(CredentialRecord, "before_insert")
@event.listens_for(CredentialRecord, "before_update")
def _cred_before_save(_mapper: Any, _connection: Any, target: CredentialRecord) -> None:
    _enforce_no_secret_in_cred_record(target)


@event.listens_for(HealthObservationRecord, "before_insert")
@event.listens_for(HealthObservationRecord, "before_update")
def _health_obs_before_save(
    _mapper: Any, _connection: Any, target: HealthObservationRecord
) -> None:
    if target.sanitized_message:
        target.sanitized_message = redact_text(target.sanitized_message)


@event.listens_for(UsageEventRecord, "before_insert")
@event.listens_for(UsageEventRecord, "before_update")
def _usage_before_save(_mapper: Any, _connection: Any, target: UsageEventRecord) -> None:
    if target.summary:
        target.summary = redact_text(target.summary)


class DatabaseManager:
    """Manages SQLite connections, WAL configuration, transactions, and integrity checks."""

    def __init__(self, db_path: Path | None = None, *, auto_migrate: bool = True) -> None:
        resolved_path = db_path or AppPaths().db_path
        self.db_path = resolved_path.resolve()
        ensure_secure_directory(self.db_path.parent)

        self._engine: Engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 10.0, "check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(self._engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.close()

        self._session_factory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

        if auto_migrate:
            self.initialize()

    @property
    def engine(self) -> Engine:
        return self._engine

    def initialize(self) -> int:
        """Apply migrations and enforce 0600 file permissions on database files."""
        version = apply_migrations(self._engine)
        self._secure_db_files()
        return version

    def _secure_db_files(self) -> None:
        ensure_secure_file(self.db_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.db_path}{suffix}")
            if sidecar.exists():
                ensure_secure_file(sidecar)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Provide a transactional scope around a series of operations."""
        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
            self._secure_db_files()
        except Exception as exc:
            session.rollback()
            if isinstance(exc, (StorageError, SecurityError)):
                raise
            # Let domain exceptions propagate cleanly; wrap unexpected DB errors
            from keypool.core.exceptions import KeypoolError

            if isinstance(exc, KeypoolError):
                raise
            raise StorageError(f"Database transaction failed: {exc}") from exc
        finally:
            session.close()

    def check_integrity(self) -> tuple[bool, str]:
        """Run SQLite PRAGMA integrity_check and foreign_key_check."""
        if not self.db_path.exists():
            return False, f"Database file does not exist at {self.db_path}"
        try:
            with self._engine.connect() as conn:
                integrity_res = conn.execute(text("PRAGMA integrity_check;")).scalars().all()
                if integrity_res != ["ok"]:
                    return False, f"SQLite integrity check failed: {', '.join(integrity_res)}"
                fk_res = conn.execute(text("PRAGMA foreign_key_check;")).fetchall()
                if fk_res:
                    return False, f"SQLite foreign key violations detected: {len(fk_res)}"
            return True, "SQLite integrity_check and foreign_key_check passed (ok)"
        except Exception as exc:
            return False, f"Database integrity check error: {exc}"

    def get_schema_version(self) -> int:
        try:
            return get_current_schema_version(self._engine)
        except Exception as exc:
            raise StorageError(f"Could not read schema version: {exc}") from exc

    def close(self) -> None:
        """Dispose engine connections cleanly."""
        self._engine.dispose()
