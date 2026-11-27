"""Fail-closed database backend configuration tests."""
import os
import subprocess
import sys
import unittest
from pathlib import Path


class DatabaseBackendConfigurationTests(unittest.TestCase):
    def test_database_url_never_silently_falls_back_to_sqlite(self):
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
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
