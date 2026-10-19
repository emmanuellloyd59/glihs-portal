# Multi-school isolation implementation plan

## Status

**Planning only — multi-school isolation is not implemented or verified.** The current application must be treated as a single-school demonstration. Do not onboard multiple schools or store real student data using this branch.

## Architecture decision required

Before adding tenant fields, choose and document one of these approaches:

1. **Separate database per school:** a central platform registry maps each authenticated school account to an approved database identifier. Database paths must be generated from trusted server-side configuration, never accepted directly from a request parameter or user-controlled hostname. Connections, backups, migrations, and restore operations must be scoped to the selected school.
2. **Shared database with tenant IDs:** every school-owned row has a non-null school ID, and all reads, writes, updates, deletes, joins, exports, reports, and background tasks enforce that scope. Composite foreign keys or equivalent database constraints should prevent linking a row from one school to a parent row in another school.

Do not mix approaches casually. Review expected school count, hosting, backups, operational skill, and cost before choosing.

## Required implementation stages

### Stage 1 — Inventory and threat model
- Enumerate every route, table, template, export, and job that reads or changes school-owned data.
- Classify global platform administration separately from school administration.
- Identify every route accepting a student, grade, attendance, finance, report-card, user, or file identifier.
- Record expected school roles and allowed actions.
- Use fictional test records only.

### Stage 2 — Schema and migration
- Add a school/tenant registry with a stable immutable identifier, display name, status, and timestamps.
- Define a controlled migration for existing single-school records. Do not guess which tenant owns records; explicitly map existing data to a designated legacy school during a reviewed migration.
- Preserve existing IDs, passwords, grade history, attendance, finance rows, report-card publication state, and audit history.
- Make migration repeatable and test it against a copy of a legacy database.
- Back up before migration and prove restore into a separate disposable database.

### Stage 3 — Authentication and authorization
- Resolve the user's school membership from trusted database state at login/session load.
- Separate platform-level super-administrator authority from school-level administrator authority.
- Never trust a school ID, role, or user ID submitted in a form, URL, cookie, or hidden field as authorization.
- Check authorization on the server for every request, including direct URLs and POST operations.
- Prevent school admins from creating or editing platform-level administrators or changing their own privileges.

### Stage 4 — Data isolation
- Enforce tenant scope in every SQL query and mutation, not only in navigation or templates.
- Ensure child rows (grades, attendance, finances, report cards, guardian links, timetable entries, notifications, audit events) cannot reference a parent row owned by another school.
- Scope search, counts, dropdown options, dashboards, report generation, and exports.
- Use non-enumerable not-found behavior where appropriate to avoid confirming another school's record exists.
- Do not rely on globally unique admission numbers or usernames as a substitute for tenant authorization.

### Stage 5 — Automated isolation tests
Create at least two schools, two school administrators, teachers, students, guardians, and records for each school. Verify:
- School A users cannot list, view, search, edit, delete, export, or infer School B records.
- Changing IDs in URLs and POST bodies never crosses the school boundary.
- Grade, attendance, finance, report-card, guardian, timetable, staff, file, and notification operations are isolated.
- Cross-school foreign-key references are rejected.
- Suspended users lose access.
- Only a deliberately authorized platform operator can perform approved cross-school administration, and those actions are audited.
- Existing single-school migration preserves all records and maps them to the intended school.

### Stage 6 — Operational and release gates
- Run all automated tests and CI.
- Exercise critical paths in a real browser at desktop and mobile widths.
- Verify durable database storage, backups, restore, migrations, monitoring, and rollback.
- Review privacy, retention, data export, incident response, and school contract language.
- Obtain explicit owner approval before merging or deploying.

## Definition of done

Multi-school support is complete only when the selected architecture is implemented end-to-end, every school-owned route and data operation is scoped, migration and recovery are proven, cross-school isolation tests pass, and the deployment has passed a documented security review. A school selector, branding setting, or `school_id` column alone is not multi-tenant security.
