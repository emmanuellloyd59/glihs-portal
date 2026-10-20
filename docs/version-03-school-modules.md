# Version 0.3 school operations — implementation notes

This branch adds a first usable school-operations workspace to the existing Flask application.

## Implemented in this branch

- Timetable entry creation and removal for academic administrators.
- Timetable conflict checks for overlapping class, teacher, or room bookings.
- Homework assignment creation for teaching staff, including due dates and optional HTTP(S) resource links.
- School announcements with role restrictions and audience selection.
- Staff profiles with job title, subjects, assigned classes, and work contact, editable only by authorized administrators.
- Homework text submissions for active student accounts linked to a verified student record; submissions are restricted to assignments matching that student's class.
- Teachers can review submissions they created; academic administrators can review all submissions.
- Operational counts and navigation to existing grades, attendance, report cards, and finance pages.
- CSRF-protected forms (using the app's existing Flask-WTF configuration), role checks, validation, audit events, and automated tests.

## Not yet production-ready / still required

- Parent portal: no parent role/account onboarding or guardian self-service is enabled. Guardian links must not be used until role migration, identity verification, access scoping, and negative authorization tests are implemented.
- Staff management: this branch provides a staff account overview and reuses existing account approval controls; it does not yet implement a full staff profile/assignment module.
- Finance: links to existing fee tracking; it does not add invoice/receipt/refund accounting or reconciliation.
- Learning materials: text submissions and teacher feedback are implemented in this branch. File uploads, plagiarism checks, offline/mobile sync, and richer rubric grading are not implemented.
- Notifications: announcements are stored and displayed in-app; email/SMS/push delivery and read receipts are not implemented.
- Analytics: counts are operational summaries only, not a validated reporting/exports suite.
- Multi-school isolation: explicitly NOT implemented. The legacy students, grades, attendance, finance, user, and report-card queries are still single-school. Do not onboard multiple schools or real personal data.
- Production persistence and recovery: verify a durable database, backup, and successful restore separately before deployment.

## Test

Run: python -m unittest discover -s tests -v

The branch should not be merged or deployed until the full CI suite passes and reviewers inspect migration, access control, and hosting persistence.
