"""Backup and isolated-restore tests; all databases are disposable fixtures."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db_safety import create_verified_backup, restore_backup_to_new_database


class DatabaseSafetyTests(unittest.TestCase):
    def _make_database(self, path):
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE schools (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                school_id INTEGER NOT NULL REFERENCES schools(id),
                admission_number TEXT NOT NULL UNIQUE
            );
            INSERT INTO schools VALUES (1, 'Fictional CMHS Test School');
            INSERT INTO students VALUES (7, 1, 'BACKUP-007');
        """)
        connection.commit()
        connection.close()

    def test_backup_and_restore_preserve_records_and_relationships(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.db"
            backup = Path(temp_dir) / "backup.db"
            restored = Path(temp_dir) / "restored" / "restored.db"
            self._make_database(source)

            create_verified_backup(source, backup)
            restore_backup_to_new_database(backup, restored)

            connection = sqlite3.connect(restored)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT id,school_id,admission_number FROM students"
                    ).fetchone(),
                    (7, 1, "BACKUP-007"),
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()

    def test_backup_and_restore_refuse_to_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.db"
            backup = Path(temp_dir) / "backup.db"
            restored = Path(temp_dir) / "restored.db"
            self._make_database(source)
            create_verified_backup(source, backup)
            with self.assertRaises(FileExistsError):
                create_verified_backup(source, backup)
            restored.write_bytes(b"do not overwrite")
            with self.assertRaises(FileExistsError):
                restore_backup_to_new_database(backup, restored)
            self.assertEqual(restored.read_bytes(), b"do not overwrite")

    def test_backup_refuses_database_with_foreign_key_violations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "broken.db"
            backup = Path(temp_dir) / "must-not-remain.db"
            connection = sqlite3.connect(source)
            connection.executescript("""
                CREATE TABLE parent (id INTEGER PRIMARY KEY);
                CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));
                PRAGMA foreign_keys=OFF;
                INSERT INTO child VALUES (404);
            """)
            connection.commit()
            connection.close()

            with self.assertRaises(sqlite3.DatabaseError):
                create_verified_backup(source, backup)
            self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
