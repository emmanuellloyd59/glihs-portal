"""Database backend connection factories.

This module is the backend-specific connection boundary for CMHS Digital
Campus. The SQLite factory preserves the application's existing row and
foreign-key behavior. PostgreSQL is intentionally not implemented here yet;
the application must not select it until query and migration compatibility
has been completed and integration-tested.
"""
import sqlite3
from pathlib import Path
from typing import Union


DatabasePath = Union[str, Path]


def connect_sqlite(database_path: DatabasePath, timeout: float = 10.0) -> sqlite3.Connection:
    """Return a row-aware SQLite connection with foreign keys enforced."""
    connection = sqlite3.connect(database_path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
