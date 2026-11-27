# Student Information Management System

## Scope

This module extends the existing `students` table and existing student pages. It does not replace the legacy records, account links, grades, attendance, report cards or fee history. All new APIs use the active school context and scoped permissions.

## Tenant boundary and identifiers

- Every admission, guardian, history, transfer, withdrawal, disciplinary and document record carries `school_id` and `student_id`.
- SQLite triggers reject related records whose student belongs to a different school.
- Student admission numbers and student identifiers are unique case-insensitively within a school, not globally. Before changing the old identifier index, the migration checks for same-school duplicates and fails closed for manual review.
- Existing student primary keys, identifiers, account links, grades, attendance and finance history are preserved.
- Identifier patterns are configurable per school. Supported placeholders are `{year}` and `{sequence}` (including numeric formats such as `{sequence:05d}`). Sequence allocation occurs in the caller's database transaction.

## Workflows

1. Admissions: draft/submitted applications, review states, duplicate screening and conversion of accepted applications into enrolled student records.
2. Student profiles: existing record pages plus paginated, filterable API search and profile retrieval.
3. Guardians: multiple contacts per student, relationship, verification, primary/emergency flags and consent status.
4. Academic history: prior institutions, years, grades/classes, result summaries and verification.
5. Transfers and withdrawals: requested/approved/completed transfer records; withdrawals create a permanent history record and change the student's status without deleting the student.
6. Discipline: restricted incident, severity, action and follow-up records.
7. Documents: validated PDF/PNG/JPEG upload, metadata, SHA-256 integrity hash, school-scoped listing and authorized download.
8. Reporting and data exchange: school-scoped status/class/admissions summary, CSV import (2 MB / 1,000-row limit) and CSV export.

## API outline

- `GET /api/v1/students` — search/filter/paginate.
- `GET /api/v1/students/{id}` — profile with field-level redaction.
- `GET /api/v1/students/duplicate-check?full_name=...&date_of_birth=...` — possible matches for manual identity review.
- `GET|PUT /api/v1/students/identifier-config` — read or configure the school identifier pattern.
- `GET|POST /api/v1/admissions`; `POST /api/v1/admissions/{id}/review`; `POST /api/v1/admissions/{id}/convert`.
- `GET|POST /api/v1/students/{id}/guardians`; `PUT /api/v1/student-guardians/{id}`; `DELETE /api/v1/student-guardians/{id}` (archive).
- `GET|POST /api/v1/students/{id}/academic-history`.
- `POST /api/v1/students/{id}/transfers`; `POST /api/v1/student-transfers/{id}/review` (completion of an outbound transfer marks the source-school record transferred); `POST /api/v1/students/{id}/withdraw`.
- `GET|POST /api/v1/students/{id}/disciplinary-records`.
- `GET|POST /api/v1/students/{id}/documents`; `GET /api/v1/student-documents/{id}/download`; `DELETE /api/v1/student-documents/{id}` (archive, never hard-delete).
- `GET /api/v1/students/report/summary`; `GET /api/v1/students/export.csv`; `POST /api/v1/students/import.csv`.

## Permission model

The permission engine distinguishes student record access, record changes, admissions, guardian management, sensitive profile fields, transfers, discipline, documents, import and export. Teachers receive scoped academic record visibility but not guardian details, documents, discipline, admissions, or bulk data access by default. Guardian and date-of-birth fields are omitted from API responses unless `student.view_sensitive` is granted. Disciplinary records require separate discipline permissions. Existing role assignments remain editable through the scoped authorization engine.

All operations are authenticated, school-scoped and audited. A foreign-tenant student ID returns not found rather than disclosing that the record exists.

## Migration and recovery

Migration creates new tables and triggers idempotently, adds school-scoped case-insensitive indexes, and preserves existing rows. It refuses to proceed with identifier duplicates within a school. It does not delete or rewrite student data. Execute against a verified backup and disposable copy before production approval. No production migration is run by this PR.

## Current implementation limits / launch gates

- The API and database foundation is implemented; the legacy server-rendered pages have not yet been redesigned to expose every new workflow.
- Document bytes currently use the configured application instance directory. Production rollout must connect durable private object storage, malware scanning, retention/legal-hold controls and encrypted backup/restore before storing real student documents.
- CSV import currently performs a bounded insert/skip operation; a reviewable dry-run, per-row error report and asynchronous large-file job are future enhancements.
- Duplicate screening returns possible same-name/date-of-birth matches for human verification; it deliberately does not auto-merge or hard-block legitimate twins/name matches.
- Guardian verification, consent evidence, transfer-to-another-tenant handoff, country-specific retention and privacy notices, full audit review, rate limits and PostgreSQL/RLS validation remain pre-launch work.
