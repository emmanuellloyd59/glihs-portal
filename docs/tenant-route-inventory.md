# Multi-school isolation: Stage 1 route and data inventory

## Status

Stage 1 inventory started on `feature/tenant-isolation-stage-1`. This document is not a claim that multi-school isolation has been implemented. The application remains a single-school demonstration; do not onboard multiple schools or use real student data.

## Current schema observed in Version 0.2

The current schema contains these school-owned or school-related tables:

- `students`: student identity, admission number, class, guardian/emergency contacts, enrollment data, status, and linked student account.
- `grades`: student-linked assessment scores, academic year, term, and recorder.
- `attendance`: student-linked daily status and notes.
- `report_cards`: student, academic year, term, publication status, and publisher.
- `student_finances`: student-linked fees, payments, notes, and updater.
- `users`: school staff/student accounts and approval state; usernames are globally unique.
- `audit_log`: actors, actions, target usernames, and details.

No school registry or enforced school ownership field is present in the inspected schema. Existing uniqueness constraints are global (for example, student admission number) rather than school-scoped.

## Route and query inventory observed

| Route / workflow | Data accessed | Isolation requirement |
| --- | --- | --- |
| `/login`, `/logout`, `/dashboard` | User accounts, audit events | Resolve account membership from trusted database state; distinguish platform and school roles. |
| `/admin/request-access`, `/admin/users` | Users, students, audit events | Requests must belong to a school; school admins may manage only their own school; platform admin powers must be explicit. |
| `/students`, `/students/register`, `/students/<id>`, `/students/<id>/edit` | Students, finances, grades, attendance | Scope list/search/detail/create/edit and all joins to the authenticated user's school. |
| `/academics` | Students, grades, attendance, report cards, users | Scope student selectors, inserts, reads, duplicate checks, and recorder references to one school. |
| `/my-report-cards` | Linked student, report cards, grades | Verify both account and student belong to the same school; show only that student's published records. |
| `/report-cards` | Students, grades, report cards, users | Scope student selector, grade existence checks, preview, publish, and unpublish operations. |
| `/student-finances` | Students, finance records, users | Scope the student and financial record before any insert or update; never accept cross-school student IDs. |
| `/healthz` | Database readiness | Return only generic service status; no tenant information. |
| `/features`, public home page | Public content | No private school records should be exposed. |

This is an initial inventory based on the current source review, not a guarantee that every route, template, SQL statement, or future workflow has been enumerated. Expand it before implementing tenant-aware queries.

## Safe implementation order

1. Choose and document one architecture: separate database per school or shared database with tenant-aware constraints. Do not combine them casually.
2. Define platform operator versus school administrator authority and the trusted membership lookup.
3. Create a tested, repeatable migration that maps existing demo data to one explicitly designated legacy school without losing IDs or history.
4. Add tenant ownership and enforce it across every route, SQL read/write, join, report, dropdown, and audit operation.
5. Add two-school adversarial tests for direct URLs, altered form IDs, searches, grade entry, attendance, finance, report-card publication, account linking, and administrator actions.
6. Run the complete test suite, inspect CI, test migration on a database copy, and prove backup/restore before any merge or deployment.

## Required tests before claiming isolation

- School A cannot list, search, view, modify, or infer School B students.
- School A cannot add grades/attendance, publish reports, or alter finances for School B students by changing a submitted ID.
- School A administrators cannot view or modify School B users, link another school's student account, or change platform administrator privileges.
- Student account linking and report-card reads verify same-school ownership on the server.
- Tenant constraints reject cross-school child/parent relationships.
- Migration preserves existing records and is safe to run more than once.
- The full suite and CI pass after the final tenant-aware code changes.

## Release guardrail

Do not add a school selector or `school_id` column and call that multi-school support. Do not merge or deploy tenant-related changes until route-level authorization, database constraints, migration, two-school tests, and recovery checks all pass and the project owner approves.
