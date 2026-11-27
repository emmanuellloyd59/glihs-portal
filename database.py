"""Database backend configuration and SQLite connection factory.

PostgreSQL selection is deliberately rejected until its driver, migrations,
query dialect and integration tests are implemented. This module is the
configuration boundary shared by the application and backend tests.
"""
from dataclasses import dataclass
import os
import sqlite3
from pathlib import Path
from typing import Mapping, Optional, Union


DatabasePath = Union[str, Path]


class DatabaseConfigurationError(RuntimeError):
    """Raised when database configuration is invalid or unsupported."""


@dataclass(frozen=True)
class DatabaseSettings:
    """Validated database settings with secrets excluded from repr output."""

    backend: str
    database_path: Optional[str] = None
    database_url: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"DatabaseSettings(backend={self.backend!r}, "
            f"database_path={self.database_path!r}, "
            f"database_url={'configured' if self.database_url else None!r})"
        )


def load_database_settings(
    default_sqlite_path: DatabasePath,
    environ: Optional[Mapping[str, str]] = None,
) -> DatabaseSettings:
    """Validate explicit backend selection without ever silently falling back.

    Environments with no backend or URL retain the existing SQLite default.
    PostgreSQL remains unavailable in this build and is rejected before any
    connection attempt. A configured URL is never ignored.
    """
    values = os.environ if environ is None else environ
    backend = values.get("DATABASE_BACKEND", "").strip().lower()
    database_url = values.get("DATABASE_URL", "").strip()
    database_path = values.get("DATABASE_PATH", "").strip()

    if not backend:
        if database_url:
            raise DatabaseConfigurationError(
                "DATABASE_URL is configured, but this application build does not yet "
                "support PostgreSQL. Set up a PostgreSQL-capable build; no SQLite "
                "fallback will be attempted."
            )
        backend = "sqlite"

    if backend not in {"sqlite", "postgresql"}:
        raise DatabaseConfigurationError(
            "DATABASE_BACKEND must be either 'sqlite' or 'postgresql'."
        )

    if backend == "sqlite":
        if database_url:
            raise DatabaseConfigurationError(
                "DATABASE_URL cannot be used with DATABASE_BACKEND=sqlite. "
                "Remove DATABASE_URL or select a PostgreSQL-capable build."
            )
        return DatabaseSettings(
            backend="sqlite",
            database_path=database_path or str(default_sqlite_path),
        )

    if not database_url:
        raise DatabaseConfigurationError(
            "DATABASE_BACKEND=postgresql requires DATABASE_URL."
        )
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise DatabaseConfigurationError(
            "DATABASE_URL must use a PostgreSQL URL scheme."
        )
    raise DatabaseConfigurationError(
        "The PostgreSQL backend is not implemented in this application build. "
        "No SQLite fallback will be attempted."
    )


def connect_sqlite(database_path: DatabasePath, timeout: float = 10.0) -> sqlite3.Connection:
    """Return a row-aware SQLite connection with foreign keys enforced."""
    connection = sqlite3.connect(database_path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
