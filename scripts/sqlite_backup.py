"""Create, verify, and restore SQLite backups without importing the web application.

Use only on a trusted host. Backups contain sensitive school records and must be
stored outside the public web root with restricted filesystem permissions.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _database_integrity(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Database file does not exist: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            detail = result[0] if result else "no integrity result"
            raise ValueError(f"SQLite integrity check failed: {detail}")
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        if not tables:
            raise ValueError("Database contains no application tables.")
    finally:
        connection.close()


def copy_database(source: str | Path, destination: str | Path, *, overwrite: bool = False) -> Path:
    """Make an online SQLite copy and atomically publish it after integrity checks."""
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("Source and destination must be different files.")
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(
            f"{source_path.as_uri()}?mode=ro", uri=True, timeout=10
        )
        target_connection = sqlite3.connect(temporary_path, timeout=10)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
            result = target_connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                detail = result[0] if result else "no integrity result"
                raise ValueError(f"Copied database failed integrity check: {detail}")
            tables = target_connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            if not tables:
                raise ValueError("Copied database contains no application tables.")
        finally:
            target_connection.close()
            source_connection.close()
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            # Some filesystems do not support POSIX permission modes.
            pass
        if destination_path.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination_path}")
        os.replace(temporary_path, destination_path)
        return destination_path
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def verify_database(path: str | Path) -> Path:
    """Raise an error unless the SQLite file exists and passes integrity checks."""
    database_path = Path(path).expanduser().resolve()
    _database_integrity(database_path)
    return database_path


def _default_database() -> Path:
    return Path(os.environ.get("DATABASE_PATH", "instance/glihs.db")).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="create a timestamped SQLite backup")
    backup_parser.add_argument("--source", type=Path, default=_default_database())
    backup_parser.add_argument("--directory", type=Path, default=Path(os.environ.get("BACKUP_DIR", "backups")))
    backup_parser.add_argument("--overwrite", action="store_true", help="allow replacing the exact output file")
    verify_parser = subparsers.add_parser("verify", help="verify a SQLite database or backup")
    verify_parser.add_argument("path", type=Path)
    restore_parser = subparsers.add_parser("restore", help="restore a verified backup to a destination")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("destination", type=Path)
    restore_parser.add_argument("--overwrite", action="store_true", help="replace an existing destination")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = args.directory / f"glihs-{timestamp}.sqlite3"
            output = copy_database(args.source, destination, overwrite=args.overwrite)
            print(f"Backup created and verified: {output}")
        elif args.command == "verify":
            output = verify_database(args.path)
            print(f"Database integrity OK: {output}")
        else:
            verify_database(args.backup)
            output = copy_database(args.backup, args.destination, overwrite=args.overwrite)
            print(f"Backup restored and verified: {output}")
        return 0
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
