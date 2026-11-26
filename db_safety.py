"""Verified SQLite backup helpers.

These helpers never overwrite an existing destination. Restore operations always
create a new database file so callers cannot accidentally replace a live database.
"""
from pathlib import Path
import sqlite3


def _verify_database(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"SQLite integrity_check failed: {integrity}")
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise sqlite3.DatabaseError(
                f"SQLite foreign_key_check found {len(foreign_key_issues)} issue(s)"
            )
    finally:
        connection.close()


def create_verified_backup(source_path, backup_path):
    """Create and verify a consistent SQLite backup without overwriting files."""
    source_path, backup_path = Path(source_path), Path(backup_path)
    if source_path.resolve() == backup_path.resolve():
        raise ValueError("Backup destination must differ from source database")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if backup_path.exists():
        raise FileExistsError(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(str(source_path), timeout=10)
    destination = None
    try:
        destination = sqlite3.connect(str(backup_path), timeout=10)
        source.backup(destination)
        destination.commit()
        destination.close()
        destination = None
        _verify_database(backup_path)
    except Exception:
        if destination is not None:
            destination.close()
        if backup_path.exists():
            backup_path.unlink()
        raise
    finally:
        source.close()
    return backup_path


def restore_backup_to_new_database(backup_path, destination_path):
    """Restore a verified backup to a new path; never replace an existing DB."""
    backup_path, destination_path = Path(backup_path), Path(destination_path)
    if backup_path.resolve() == destination_path.resolve():
        raise ValueError("Restore destination must differ from backup")
    _verify_database(backup_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(str(backup_path), timeout=10)
    destination = None
    try:
        destination = sqlite3.connect(str(destination_path), timeout=10)
        source.backup(destination)
        destination.commit()
        destination.close()
        destination = None
        _verify_database(destination_path)
    except Exception:
        if destination is not None:
            destination.close()
        if destination_path.exists():
            destination_path.unlink()
        raise
    finally:
        source.close()
    return destination_path
