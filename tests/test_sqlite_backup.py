"""Tests for safe SQLite backup and recovery helpers."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.sqlite_backup import copy_database, verify_database


class SQLiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "live.sqlite3"
        connection = sqlite3.connect(self.source)
        connection.execute("CREATE TABLE fictional_records (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
        connection.execute("INSERT INTO fictional_records (label) VALUES ('fictional recovery check')")
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_backup_preserves_records_and_verifies(self):
        backup = self.root / "backups" / "copy.sqlite3"
        copy_database(self.source, backup)
        verify_database(backup)
        connection = sqlite3.connect(backup)
        row = connection.execute("SELECT label FROM fictional_records").fetchone()
        connection.close()
        self.assertEqual(row[0], "fictional recovery check")

    def test_restore_to_new_destination_preserves_records(self):
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restored.sqlite3"
        copy_database(self.source, backup)
        copy_database(backup, restored)
        verify_database(restored)
        connection = sqlite3.connect(restored)
        count = connection.execute("SELECT COUNT(*) FROM fictional_records").fetchone()[0]
        connection.close()
        self.assertEqual(count, 1)

    def test_refuses_to_overwrite_existing_destination_by_default(self):
        destination = self.root / "existing.sqlite3"
        destination.write_bytes(b"keep existing content")
        with self.assertRaises(FileExistsError):
            copy_database(self.source, destination)
        self.assertEqual(destination.read_bytes(), b"keep existing content")

    def test_corrupt_source_is_rejected_without_replacing_destination(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        destination = self.root / "safe.sqlite3"
        destination.write_bytes(b"existing")
        with self.assertRaises(sqlite3.DatabaseError):
            copy_database(corrupt, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), b"existing")

    def test_source_and_destination_must_differ(self):
        with self.assertRaises(ValueError):
            copy_database(self.source, self.source)

    def test_verify_rejects_missing_database(self):
        with self.assertRaises(FileNotFoundError):
            verify_database(self.root / "missing.sqlite3")


if __name__ == "__main__":
    unittest.main()
