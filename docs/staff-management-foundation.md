# Staff Management Foundation

## Purpose

The staff module extends the platform's global user identity and canonical school-membership model. It does not recreate accounts or replace password hashes. A user may hold separate staff memberships in multiple schools, each governed by that school's own membership and authorization scope.

## Core model

- `users`: authentication identity, account status, and role/authorization.
- `school_memberships`: canonical tenant membership and authorization scope. `staff_memberships` is a one-to-one HR extension linked to that canonical membership; it does not grant or duplicate school access.
- `staff_profiles`: employee number, contact details, emergency contact and profile notes.
- `staff_employment`: job title, employment type/status, dates, department, supervisor, campus/work location and responsibilities summary.
- `staff_departments`: tenant-owned organizational units.
- `staff_qualifications`: qualifications, awarding institutions, field, date and verification status.
- `staff_responsibilities`: class, subject, department, leadership and administrative responsibilities.
- `teacher_class_assignments`: existing canonical school-scoped authorization for teacher access to class/subject student work; shown on the staff record rather than duplicated.
- `staff_documents`: metadata and SHA-256 digest for private employee files stored outside the public static directory.
- `staff_lifecycle_events`: append-only operational history for onboarding/offboarding events.

## Tenant and authorization rules

1. A staff manager must be an authorized school leader and must enter an active school context.
2. Every staff profile, membership, employment row, qualification, responsibility, document and lifecycle event is scoped by `school_id`.
3. Each staff record references a canonical `school_memberships` row. Database triggers verify the linked user, school and staff-eligible membership role; a user can hold staff memberships in multiple schools without changing the legacy `users.school_id` field.
4. Composite foreign keys keep profile, employment, department and responsibility references within the same school. Subject references are validated against the same school's subject catalogue.
5. Detail pages, uploads, downloads and lifecycle operations resolve the target by both school ID and staff identity. Cross-school records return not found.
6. Offboarding marks the membership inactive, employment terminated, account suspended, revokes active teacher class/subject assignments, and records the event. Existing records are retained.
7. Onboarding/reactivation restores membership and account access but deliberately does not silently restore old teaching permissions. An administrator must review and reassign those permissions.

## Migration and preservation

The migration is additive and idempotent. Existing school staff accounts are linked to staff memberships and receive profile/employment shells. Existing account IDs, usernames, roles, statuses, password hashes, and teacher assignments are not reset. The migration does not fabricate qualifications, employee numbers, employment dates, or departments.

## Private documents

Staff documents accept PDF, PNG and JPEG files up to 5 MiB. The application uses a server-generated storage key, verifies the file signature, records the original filename and SHA-256 digest, and stores bytes below the private instance directory, never under public static assets. Downloads require an authenticated staff manager and a matching school-owned membership. Archiving hides a document from normal lists and downloads while retaining its private file for the school's retention policy. Production deployments must persist the private instance volume and apply backups, retention controls, encryption-at-rest, and malware scanning appropriate to the school's policies.

## Lifecycle

- Onboarding: activate the membership and employment, activate the linked school account, and append an onboarding event.
- Employment changes: update job title, type, department, supervisor, work location, dates and employment state.
- Offboarding: end employment and membership, suspend login access, revoke teaching assignments and append an offboarding event.
- Existing account suspension/reactivation in the staff directory synchronizes the staff membership and employment status.

## Future extension seams (not implemented)

- Leave management: leave policies, balances, requests, approvals, calendars, and accrual rules. Leave is not the same as terminating or suspending employment.
- Payroll: country/currency-aware pay schedules, compensation components, deductions, statutory tax rules, payslips, approvals and accounting integration. No salary or bank data is collected in this foundation.
- Performance management: configurable review cycles, goals, feedback, moderation and restricted appraisal access. Performance notes must not be placed in general profile notes.
- Cross-school person records: each school retains an independent staff record linked to its own school membership. A future person/identity layer may optionally support consent-based cross-school employment verification without exposing one school's private HR records to another.

## Operational security notes

Staff profiles and employment information are sensitive. Keep access restricted to authorized school leaders, audit changes, avoid placing sensitive details in logs, and review retention and access policies before production rollout. This foundation does not itself implement jurisdiction-specific employment, privacy, safeguarding, or records-retention compliance.
