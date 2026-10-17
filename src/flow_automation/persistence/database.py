from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from flow_automation.persistence.models import Base

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
SQLITE_BUSY_TIMEOUT_MS = 5000


class DatabaseVersionError(RuntimeError):
    """Raised when a database schema cannot be safely upgraded by this version."""


class Database:
    """Owns a SQLite engine and creates short-lived transaction-scoped sessions."""

    def __init__(self, url: str | URL) -> None:
        if isinstance(url, str) and not url.startswith("sqlite"):
            raise ValueError("Flow Automation Tool persistence currently requires SQLite.")
        if isinstance(url, URL) and url.drivername != "sqlite":
            raise ValueError("Flow Automation Tool persistence currently requires SQLite.")

        connect_args: dict[str, object] = {"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000}
        if isinstance(url, str) and url.endswith(":memory:"):
            connect_args["check_same_thread"] = False
        if isinstance(url, URL) and url.database == ":memory:":
            connect_args["check_same_thread"] = False

        self.engine: Engine = create_engine(
            url,
            connect_args=connect_args,
            future=True,
        )
        event.listen(self.engine, "connect", self._configure_sqlite_connection)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @classmethod
    def from_path(cls, path: Path) -> Database:
        resolved_path = path.expanduser().resolve()
        if str(resolved_path) != ":memory:":
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            URL.create(
                "sqlite",
                database=str(resolved_path),
            )
        )

    @staticmethod
    def _configure_sqlite_connection(
        dbapi_connection: sqlite3.Connection,
        _: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    def initialize(self) -> None:
        """Apply known schema upgrades in order; never downgrade a database."""
        try:
            with self.engine.begin() as connection:
                version = int(
                    connection.exec_driver_sql("PRAGMA user_version").scalar_one()
                )
                if version > SCHEMA_VERSION:
                    raise DatabaseVersionError(
                        f"Database schema version {version} is newer than supported "
                        f"version {SCHEMA_VERSION}."
                    )
                if version < 1:
                    Base.metadata.create_all(connection)
                    connection.exec_driver_sql("PRAGMA user_version=1")
        except SQLAlchemyError:
            logger.exception("SQLite schema initialization failed.")
            raise

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Commit on success and roll back the whole operation on any exception."""
        with self._session_factory() as session:
            with session.begin():
                yield session

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> Database:
        self.initialize()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.close()
