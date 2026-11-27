"""Fail-closed database backend configuration tests."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from database import (
    DatabaseConfigurationError,
    DatabaseSettings,
    load_database_settings,
)


class DatabaseBackendConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.default_path = Path("/tmp/cmhs-test.sqlite3")

    def settings(self, **overrides):
        values = {}
        values.update(overrides)
        return load_database_settings(self.default_path, values)

    def test_legacy_environment_defaults_to_sqlite(self):
        settings = self.settings()
        self.assertEqual(settings.backend, "sqlite")
        self.assertEqual(settings.database_path, str(self.default_path))
        self.assertIsNone(settings.database_url)

    def test_explicit_sqlite_backend_accepts_custom_path(self):
        settings = self.settings(
            DATABASE_BACKEND="sqlite",
            DATABASE_PATH="/tmp/explicit.sqlite3",
        )
        self.assertEqual(settings.backend, "sqlite")
        self.assertEqual(settings.database_path, "/tmp/explicit.sqlite3")

    def test_sqlite_backend_rejects_database_url(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "cannot be used"):
            self.settings(
                DATABASE_BACKEND="sqlite",
                DATABASE_URL="postgresql://db.example/cmhs",
            )

    def test_unknown_backend_is_rejected(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "must be either"):
            self.settings(DATABASE_BACKEND="mysql")

    def test_postgresql_backend_requires_database_url(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "requires DATABASE_URL"):
            self.settings(DATABASE_BACKEND="postgresql")

    def test_postgresql_backend_rejects_unsupported_build_without_fallback(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "not implemented"):
            self.settings(
                DATABASE_BACKEND="postgresql",
                DATABASE_URL="postgresql://db.example/cmhs",
            )

    def test_database_settings_repr_does_not_expose_url_secret(self):
        settings = DatabaseSettings(
            backend="postgresql",
            database_url="postgresql://user:secret@db.example/cmhs",
        )
        self.assertNotIn("secret", repr(settings))
        self.assertIn("configured", repr(settings))

    def test_database_url_never_silently_falls_back_to_sqlite(self):
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment.pop("DATABASE_BACKEND", None)
        environment["SECRET_KEY"] = "test-only-secret-key"
        environment["DATABASE_URL"] = "postgresql://invalid.example/cmhs_test"
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not yet support PostgreSQL", result.stderr)
        self.assertNotIn("sqlite3.OperationalError", result.stderr)


if __name__ == "__main__":
    unittest.main()
