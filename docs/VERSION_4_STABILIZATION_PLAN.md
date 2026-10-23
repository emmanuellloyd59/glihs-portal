# CMHS Digital Campus Version 4 — Stabilization Plan

## Scope and safety rules

Version 4 builds on the existing Version 3 application. It must not be a rewrite.

- Keep the CMHS Digital Campus name and branding.
- Do not upgrade Render or create billable infrastructure as part of this work.
- Do not reset, replace, or bulk-import into the live database.
- Preserve existing school, student, staff, and financial records.
- Keep all database reads and writes scoped to the active school; do not weaken tenant isolation.
- Use fictional/demo accounts only for demonstrations and never represent them as real staff.
- Make code changes on this Version 4 branch, run available tests, review the diff, and use a pull request before merging.
- Do not claim production readiness based only on pages returning HTTP 200.

## Phase 1 — Baseline and access reliability

1. Review the current login lookup, password-hash verification, account status checks, and school context requirements.
2. Review the demo-staff activation and password-recovery path; confirm credentials are shown only in the authorized one-time result page.
3. Inspect role guards and dashboard links for Super Admin, school admin, principal, vice principal, registrar, finance, teacher, student, and parent roles.
4. Run the existing test suite and record actual results before changing behavior.
5. Add regression tests for invalid credentials, inactive users, unauthorized role access, and cross-school access wherever missing.
6. Verify student registration and financial-record paths without changing production data.

## Phase 2 — Student and finance integrity

1. Confirm registration dates, class levels, annual fees, partial payments, unpaid balances, and payment history are represented consistently.
2. Verify totals and balances with automated tests.
3. Ensure all student and finance queries are scoped to the selected school.
4. Preserve the bundled fictional student CSV; do not import it into production without first verifying current database contents and an approved recovery plan.

## Phase 3 — Usability and responsive design

1. Keep CMHS Digital Campus branding consistent across all pages.
2. Review dashboard navigation and role-specific shortcuts.
3. Improve mobile layouts for registration, student records, staff management, and finance.
4. Check form validation, error messages, accessibility, and print layouts.

## Phase 4 — Release gate

Before merge/deploy:
- Run the full available automated test suite.
- Review all changed files and security-sensitive changes.
- Confirm tenant-isolation tests pass.
- Confirm no secrets or real personal data were committed.
- Confirm no database reset, import, or paid infrastructure change is included.
- Deploy only after checks pass, then verify the live health and key routes.

## Known infrastructure limitation

The current Render service is on the Free plan and has no persistent disk or connected PostgreSQL database configured. This plan deliberately does not change infrastructure. Database persistence remains a separately approved task; records on ephemeral storage may not survive restarts or redeployments.
