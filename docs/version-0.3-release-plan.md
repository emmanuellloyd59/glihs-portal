# Version 0.3 — Multi-school isolation and release gates

## Status

**Implementation in progress. Not production-ready.**

The schema foundation is implemented in `app.py`: an idempotent `schools` table, additive `school_id` columns/indexes, server-loaded school ownership on user objects, and audit events associated with the actor's school. The migration helpers were updated to preserve `school_id` if legacy user/grade tables must be rebuilt. Core student registration, listing, detail/edit, academic records, report cards, and finance queries now include school ownership checks. Tenant-scoped routes now fail closed when the authenticated user has no school assignment instead of silently falling back to School 1. Student account linking and audit logging no longer silently assign unscoped records to School 1. New student, grade, attendance, report-card, finance, and staff-request records are assigned to a school. Fictional two-school tests cover student visibility, academic listing visibility, grade/attendance write rejection, missing-school-context denial, and per-school admission-number uniqueness. The current automated suite passes 71 tests on Python 3.11 and 3.14. It includes fictional cross-school checks for student visibility, academic listings, grade/attendance writes, report-card and finance isolation, active-session denial for suspended schools, plus an in-memory legacy student-migration preservation test. These results are useful evidence, but they do not replace a production-like migration/recovery rehearsal or a full route-by-route tenant audit.

**Critical limitation:** this is still not release-ready. Staff/user-management and account-linking paths need a complete tenant-policy review, schema columns remain nullable for compatibility, staff onboarding still defaults pending requests to the legacy school, and no passing CI or local test run has been confirmed. A migration now replaces the legacy global admission-number constraint with `UNIQUE(school_id, admission_number)`, preserving known student fields and IDs; the helper has a disposable in-memory legacy-schema regression test; the full startup migration still needs rehearsal against a production-like legacy database before release. Some route/query paths and all mutations need a full audit, not just spot fixes. Do not onboard a second school, store real student records, merge, or deploy until the full two-school suite, migration/recovery, and regression gates pass.

## Version 0.3 goals

1. Enforce school ownership at the data-access boundary for all school-owned records.
2. Preserve the existing single-school installation and its data through a tested, reversible migration.
3. Keep platform-wide super-administration separate from school-level roles.
4. Add cross-school authorization tests using two fictional schools.
5. Complete regression, migration, backup/restore, and browser checks before release.

## Architecture decision

Use a shared relational database with explicit tenant ownership, provided every tenant-owned read and write is scoped and verified. Do not rely on hidden navigation links, role names, or client-submitted school IDs for authorization.

- Create a `schools` table with stable primary keys and active/suspended status.
- Add a non-null `school_id` foreign key to school-owned users, students, grades, attendance, report cards, fees, and school audit events.
- Assign existing single-school data to one seeded legacy school during migration. Preserve primary keys, password hashes, and record relationships.
- Keep platform super-admin accounts platform-scoped; school staff accounts must belong to exactly one school.
- Derive the active school from the authenticated server-side user/session, never from an untrusted form field or query parameter.
- Enforce school ownership in the database query and in the referenced-record validation for every operation. A student ID from another school must be treated as not found/unauthorized.
- Replace globally unique school-local identifiers (such as admission numbers) with uniqueness scoped to the school, using a database constraint such as `UNIQUE(school_id, admission_number)`. Preserve global uniqueness only where intentional, such as a username if that is the chosen login policy.
- Use parameterized SQL and explicit transaction boundaries. Never concatenate a client-provided school identifier into SQL.
- Add migration backups and a documented rollback/recovery procedure before running a migration against production data.

## Required route and query inventory

Audit every query and mutation for these areas, including dashboard counts, search, joins, exports, and reports:

- Student registration, listing, search, update, and status changes
- Grade creation, listing, editing, and duplicate checks
- Attendance creation, listing, and duplicate checks
- Report-card build, publish/unpublish, and student self-service
- Fee/finance reads and writes
- Parent/student account-to-student linking
- Staff/user creation, approval, suspension, and role changes
- Audit logs, notifications, timetable, and future modules

Every join between tenant-owned records must verify compatible school ownership; filtering only the first table is not sufficient protection.

## Mandatory automated acceptance tests

Create two schools, School A and School B, with separate administrators, teachers, students, grades, attendance, report cards, and fee records.

- School A staff can read and mutate only School A records.
- School B staff can read and mutate only School B records.
- Guessing another school's student ID, grade ID, report-card ID, or finance ID never reveals or changes that record.
- A cross-school student ID submitted when recording grades or attendance is rejected and writes no data.
- Search, dashboard counts, joins, report-card generation, and student self-service never leak another school's data.
- School-local admission numbers can repeat in different schools but cannot duplicate within one school.
- A school staff member cannot create a platform super-admin or change their own school ownership.
- A suspended school or user cannot continue accessing protected data.
- The legacy migration preserves record counts and relationships; rerunning it is safe and does not duplicate data.
- Backup restoration is tested against a disposable copy before production use.

## Release gates

- [ ] All application routes and SQL queries audited for tenant scoping
- [ ] Schema migration tested against a copy of a legacy database
- [ ] Two-school negative-access test suite passes
- [x] Full test suite passes on supported Python versions (71 tests on Python 3.11 and 3.14; GitHub Actions)
- [ ] Browser checks pass on desktop and mobile
- [ ] Report cards verified in print/PDF preview
- [ ] Backup restore and rollback rehearsal succeeds
- [ ] Code review completed and pull request approved
- [ ] Staging deployment verified before production deployment

## Scope control

Version 0.3 must first deliver secure multi-school foundations. Parent portals, staff workflows, timetables, notifications, and additional finance features must not be advertised as production-ready until each module has server-side permissions, tenant-scoped queries, and automated tests.

**Do not merge or deploy this planning document as proof that tenant isolation is implemented.**