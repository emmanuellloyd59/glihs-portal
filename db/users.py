"""Portable, reviewed user lookup queries shared by SQLite and PostgreSQL.

This repository is deliberately connection-injected: callers own connection
lifecycle and may pass a DatabaseConnection facade for either backend.
"""
from collections.abc import Mapping


_USER_COLUMNS = """
    SELECT u.id, u.username, u.password_hash, u.role,
           u.is_super_admin, u.school_id
    FROM users AS u
    WHERE {lookup_column} = ? AND u.status = 'active'
      AND (
          (u.role = 'super_admin' AND u.is_super_admin = 1
           AND u.school_id IS NULL)
          OR (
              u.role <> 'super_admin' AND u.is_super_admin = 0
              AND EXISTS (
                  SELECT 1 FROM schools AS s
                  WHERE s.id = u.school_id AND s.status = 'active'
              )
          )
      )
"""


def _lookup_active_user(connection, column, value):
    """Return a row only when the account and its school are both active.

    The lookup column is selected from a fixed internal allow-list; user input
    is always bound as a parameter. This preserves the existing security rule
    that malformed super-admin and inactive-school accounts cannot log in.
    """
    if column not in {"id", "username"}:
        raise ValueError("unsupported user lookup column")
    if value is None:
        return None
    return connection.execute(
        _USER_COLUMNS.format(lookup_column=f"u.{column}"),
        (value,),
    ).fetchone()


def get_active_user_by_id(connection, user_id):
    """Fetch an active, correctly-scoped account by primary key."""
    return _lookup_active_user(connection, "id", user_id)


def get_active_user_by_username(connection, username):
    """Fetch an active, correctly-scoped account by exact username."""
    return _lookup_active_user(connection, "username", username)


def user_row_to_mapping(row):
    """Normalize a DB row to a plain mapping for stable caller construction."""
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    # Supports tuple-like DB-API rows only when cursor metadata is available
    # through a mapping-style row factory; fail clearly rather than guessing.
    raise TypeError("user lookup requires mapping-style database rows")
