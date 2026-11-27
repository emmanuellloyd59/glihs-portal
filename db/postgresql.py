"""PostgreSQL connection primitives.

These primitives are intentionally not wired into the Flask application yet.
The application still contains SQLite-specific migrations and SQL statements;
selecting PostgreSQL at application startup remains blocked until those are
ported and integration-tested.
"""
from database import DatabaseConfigurationError


class HybridRow(dict):
    """Mapping row that also supports positional indexing like sqlite3.Row."""

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def hybrid_dict_row(cursor):
    """Psycopg row factory supporting both named and positional access."""
    columns = tuple(column.name for column in cursor.description)

    def make_row(values):
        return HybridRow(columns, values)

    return make_row


def connect_postgresql(database_url, timeout=10.0):
    """Open a Psycopg 3 connection without changing application backend selection."""
    if not isinstance(database_url, str) or not database_url.startswith(
        ("postgresql://", "postgres://")
    ):
        raise DatabaseConfigurationError("A valid PostgreSQL DATABASE_URL is required.")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    try:
        import psycopg
    except ImportError as exc:
        raise DatabaseConfigurationError(
            "PostgreSQL support requires the psycopg package."
        ) from exc

    return psycopg.connect(
        database_url,
        connect_timeout=max(1, int(timeout)),
        row_factory=hybrid_dict_row,
    )
