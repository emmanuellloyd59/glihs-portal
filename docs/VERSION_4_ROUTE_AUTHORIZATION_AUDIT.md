# CMHS Digital Campus — Version 4 Route Authorization Audit

**Audit date:** 2026-11-26  
**Audited branch:** `version-4/reconciled-main-2026-11-26`  
**Pull request:** #49 (draft)  
**Scope:** Flask route inventory, authentication boundaries, tenant context, role gates, and existing automated-test evidence.

## Executive result

This is a source-level route inventory, not a security certification. The current `app.py` exposes 25 application routes. All non-public operational routes are decorated with `login_required`; public routes are limited to the home page, health check, feature overview, login, and staff-access request. School-scoped routes generally call `require_school_context()` and apply a school ID to database queries.

The audit confirms useful direct-route tests for teacher least privilege, finance restrictions, report publication, student-record editing, parent/child linking, teacher assignment scoping, and cross-school object access. It does **not** yet prove every role/action/object combination. Version 4 remains blocked from production pending the release gates in `VERSION_4_QA_AND_RELEASE_READINESS.md`.

## Route inventory

| Route | Authentication | Source-level authorization / scope | Existing test evidence and remaining checks |
|---|---|---|---|
| `GET /` | Public | Public landing page only | Home-page branding test |
| `GET /healthz` | Public | Minimal database readiness response; no credential/path detail | Health response and non-disclosure test |
| `GET /features` | Public | Public feature overview | Feature Center accuracy test |
| `GET,POST /login` | Public | Password verification, active-account checks, throttling | Invalid password, inactive account, rate-limit, session and audit tests |
| `GET /dashboard` | Login required | Role-aware dashboard; platform super-admin requires a valid selected active school for tenant context | Login requirement, teacher dashboard, finance metrics and role dashboard tests; complete role-by-role rendered-link review remains |
| `GET,POST /admin/select-school` | Login required | Platform super-admin invariants required; only active schools can be selected | Super-admin flag, school-selection and suspended-school tests |
| `POST /logout` | Login required | POST-only sign-out and audit event | GET logout rejection and session tests |
| `GET,POST /admin/request-access` | Public | Creates pending staff request; excludes student/parent self-assignment and validates selected active school | Pending request, role allowlist and school validation tests; abuse/rate-limit review remains |
| `GET,POST /admin/users` | Login required | Platform super-admin only | Non-super-admin denial, student/parent link lifecycle and account-management tests; full action-by-action platform-admin audit remains |
| `GET,POST /academics` | Login required | Academic role allowlist; school context; teacher class/subject assignment scoping | Teacher assignment, revocation, unassigned access, invalid grade/attendance, cross-school and direct-post denial tests |
| `GET,POST /timetable` | Login required | Timetable view allowlist; separate manage permission; school context | Teacher view-only, school-admin management, overlap/time validation tests; all role/action combinations remain to be enumerated |
| `GET /my-report-cards` | Login required | Student role only; own linked student record and published reports | Student own-report and parent/student isolation tests |
| `GET,POST /report-cards` | Login required | Report-card view allowlist; school context; teacher assignment scoping; publication permission is separate | Teacher preview/no-publish, registrar view/no-publish, principal publish/unpublish and term validation tests |
| `GET,POST /students/register` | Login required | Explicit registration role allowlist; school context | Teacher denial, school leadership/academic-admin access, validation and atomic audit tests |
| `GET /students/<student_id>` | Login required | Student-record role allowlist; school-scoped lookup; private profile fields limited to principal/registrar | Teacher private-field minimization and cross-school detail denial tests |
| `GET,POST /students/<student_id>/edit` | Login required | Principal/registrar only; school-scoped lookup | Teacher edit denial, registrar update and cross-school edit denial tests |
| `GET /students` | Login required | Student-record role allowlist; school-scoped query; teacher assignment filter and private-field minimization | Teacher list scoping, private-field minimization and cross-school tests |
| `GET,POST /student-finances` | Login required | Super-admin, school-admin, principal, registrar or finance-admin; school context; serialized write transaction | Finance role restriction, ledger integrity, overpayment, atomic audit rollback and teacher direct-route denial tests |
| `GET,POST /learning-materials` | Login required | School context; publishing role required for upload; teacher uploads constrained by assignments | Teacher unassigned upload denial and school/class scoping tests; test all manager/student/parent audience combinations |
| `GET /learning-materials/<material_id>/download` | Login required | School-scoped lookup; audience, archive and teacher-assignment checks | Teacher unassigned download denial and cross-school database guards; test archived and each audience/role combination |
| `POST /learning-materials/<material_id>/archive` | Login required | Materials-manage role only; school-scoped mutation and audit | Database guard and manager policy exist; add direct-route denial tests for every non-manager role and cross-school IDs |
| `GET,POST /announcements` | Login required | School context; content-publish role required for create/edit; audience-scoped visibility | Teacher publish denial, student audience visibility and cross-school guards; test each audience and edit action by role |
| `POST /announcements/<announcement_id>/archive` | Login required | Content-manage role only; school-scoped mutation and audit | Teacher archive denial and database guards; expand direct-route tests to other non-manager roles and cross-school IDs |
| `GET,POST /finance/payments` | Login required | Finance role allowlist; school context; serialized ledger/balance update | Finance access, receipt/balance, overpayment, cross-school payment denial and atomic audit rollback tests |
| `GET /parent/children` | Login required | Parent role only; returns explicitly linked children in the same school | Parent sees only linked children and private-data minimization tests |

## Verified authorization principles

1. **Authentication is not authorization.** Navigation visibility is not treated as a security boundary; server-side role checks protect operational routes and mutations.
2. **Teacher least privilege.** Teachers are limited to assigned classes/subjects for academic activity. They cannot create official student records, edit identity records, manage staff, access finance, or publish/archive school-wide announcements.
3. **School isolation.** Student, finance, academic, report-card, parent-link, timetable, learning-material and announcement records are scoped to the selected school. Database guards provide a second integrity boundary for supported relationships.
4. **Platform administration is explicit.** A platform super-admin must satisfy the role, platform flag and unassigned-school invariants, then explicitly select an active school before tenant workflows.
5. **Sensitive field minimization.** Teacher-facing student queries omit guardian/contact and birth-date fields at the SQL result level; private profile fields are restricted to authorized roles.
6. **State-changing actions are separately permissioned.** Viewing a timetable/report/material is not equivalent to managing, publishing, archiving or recording payments.

## Required next tests (Prompt 05 follow-through)

- [ ] Build a route/action/role matrix from Flask's registered URL map and compare it with this inventory.
- [ ] Add direct GET and POST denial tests for every restricted mutation route, including timetable management, resource upload/archive, announcement edit/archive, account management and finance.
- [ ] Add cross-school direct-object tests for student detail/edit, report publication, timetable IDs, resource download/archive, announcement edit/archive and payment targets.
- [ ] Verify parent and student object ownership for every endpoint, not only the dashboard and linked-child list.
- [ ] Verify every file download/export is authorized by role, school, audience, assignment and archive status.
- [ ] Verify missing, suspended or stale school context fails closed for every tenant route.
- [ ] Verify all unsafe methods require CSRF and all state changes write audit events atomically.
- [ ] Run the full Python 3.11 and 3.14 suites after additions; use only fictional test records.
- [ ] Complete authenticated browser testing on a separate durable staging environment before production approval.

## Explicit exclusions

This audit did not sign in to the live service, submit live forms, inspect or alter live student records, change Render configuration, create paid infrastructure, merge PR #49, or deploy code. It does not establish compliance with education, privacy, banking, or other regulations.

## Release decision

**Not production-approved.** The route inventory and existing tests are evidence of progress, not proof of complete security. Keep PR #49 as the sole consolidated Version 4 track. Do not merge or deploy until all critical authorization, staging, backup/restore, browser, monitoring and rollback gates pass and the project owner explicitly approves.
