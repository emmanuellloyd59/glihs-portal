#!/usr/bin/env python3
"""Safely import the bundled 250 fictional K2–Grade 12 demo students.

This script never creates users/passwords and never deletes existing records.
Run only against a disposable local/staging database, never production.
"""
import argparse
import csv
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data" / "demo_students_k2_grade12.csv"
VALID_LEVELS = {"K2", "K3", "K4", *(f"Grade {i}" for i in range(1, 13))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, help="Path to a disposable local/staging SQLite database")
    parser.add_argument("--school-slug", required=True, help="Existing active school's exact slug")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Optional input CSV path")
    parser.add_argument("--confirm-demo-data", action="store_true", help="Explicitly confirm fictional records should be imported")
    args = parser.parse_args()

    if not args.confirm_demo_data:
        parser.error("Refusing to import without --confirm-demo-data.")
    if os.environ.get("RENDER") or os.environ.get("APP_ENV", "").lower() == "production" or os.environ.get("FLASK_ENV", "").lower() == "production":
        parser.error("Refusing to run in a production environment. Use a disposable local/staging database.")
    if not args.school_slug.strip():
        parser.error("--school-slug cannot be blank.")

    with args.csv.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    if len(records) != 250:
        parser.error(f"Expected exactly 250 demo rows, found {len(records)}.")
    seen = set()
    for row in records:
        admission = row.get("admission_number", "")
        if not admission.startswith("DEMO-K12-") or admission in seen:
            parser.error(f"Invalid or duplicate demo admission number: {admission!r}")
        seen.add(admission)
        if row.get("class_level") not in VALID_LEVELS:
            parser.error(f"Invalid class level for {admission}: {row.get('class_level')!r}")
        if row.get("status") != "active" or not row.get("full_name", "").strip():
            parser.error(f"Invalid demo row: {admission!r}")

    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"Database file does not exist: {db_path}. Start the app once to initialize a disposable database.")
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        school = connection.execute(
            "SELECT id FROM schools WHERE slug = ? AND status = 'active'",
            (args.school_slug.strip(),),
        ).fetchone()
        if not school:
            parser.error("No active school matches that slug. No records were imported.")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(students)")}
        required = {"admission_number", "full_name", "class_level", "status", "school_id"}
        if not required.issubset(columns):
            parser.error("The students table is not initialized for the current school-scoped schema.")

        # Refuse any collisions before inserting so reruns never partially import or overwrite.
        collisions = connection.execute(
            "SELECT admission_number FROM students WHERE school_id = ? AND admission_number LIKE 'DEMO-K12-%'",
            (school["id"],),
        ).fetchall()
        if collisions:
            parser.error(f"This school already has {len(collisions)} DEMO-K12 records. No records were changed.")

        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """INSERT INTO students (admission_number, full_name, class_level, status, school_id)
               VALUES (?, ?, ?, ?, ?)""",
            [(r["admission_number"], r["full_name"], r["class_level"], r["status"], school["id"]) for r in records],
        )
        connection.commit()
        count = connection.execute(
            "SELECT COUNT(*) FROM students WHERE school_id = ? AND admission_number LIKE 'DEMO-K12-%'",
            (school["id"],),
        ).fetchone()[0]
        print(f"Imported {count} fictional demo students into school '{args.school_slug}'.")
        print("No login accounts were created. Existing student records were not modified or deleted.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
