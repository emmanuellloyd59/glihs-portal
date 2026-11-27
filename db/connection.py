"""Small DB-API connection facade for staged SQLite/PostgreSQL query migration.

This adapter deliberately does not translate SQL dialect features, normalize
driver exceptions, or emulate SQLite-generated IDs. PostgreSQL use is limited
to query groups that have been explicitly reviewed for dialect compatibility.
"""
from collections.abc import Mapping

from db.sql import qmark_to_format


class DatabaseConnection:
    """Delegate DB-API lifecycle operations and adapt reviewed bind markers."""

    def __init__(self, connection, backend):
        if backend not in {"sqlite", "postgresql"}:
            raise ValueError("backend must be 'sqlite' or 'postgresql'")
        self._connection = connection
        self.backend = backend

    def execute(self, sql, parameters=()):
        parameters = () if parameters is None else parameters
        if self.backend == "postgresql":
            if isinstance(parameters, Mapping):
                raise TypeError(
                    "PostgreSQL adapter currently supports positional parameters only"
                )
            parameters = tuple(parameters)
            sql = qmark_to_format(sql, len(parameters))
        return self._connection.execute(sql, parameters)

    def executemany(self, sql, parameter_sets):
        if self.backend == "postgresql":
            rows = [tuple(row) for row in parameter_sets]
            if not rows:
                raise ValueError(
                    "PostgreSQL executemany requires at least one parameter set "
                    "so placeholder conversion can be validated"
                )
            parameter_count = len(rows[0])
            if any(len(row) != parameter_count for row in rows):
                raise ValueError("executemany parameter sets must have equal lengths")
            sql = qmark_to_format(sql, parameter_count)
            parameter_sets = rows
        return self._connection.executemany(sql, parameter_sets)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

    def cursor(self, *args, **kwargs):
        return self._connection.cursor(*args, **kwargs)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        # Preserve driver-specific capabilities while keeping the common
        # operations above explicit and testable.
        return getattr(self._connection, name)
