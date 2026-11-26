# CMHS Digital Campus — Prompt 02 Authorization and Teacher Least-Privilege Audit

**Audit date:** 2026-11-26  
**Development branch:** `version-4/reconciled-main-2026-11-26`  
**Pull request:** #49 (draft)  
**Scope:** All Flask routes registered by the main application and its route-registration modules, role boundaries, direct HTTP actions, school scoping, file access, exports, and current automated-test evidence.

## Executive result

The repository registers **30 HTTP routes across five Python modules**: `app.py`, `homework.py`, `staff_attendance.py`, `school_performance.py`, and `demo_staff.py`. This count corrects the earlier preliminary inventory, which counted only the 25 decorators located directly in `app.py` and omitted five routes registered by helper modules.

The application has server-side role checks on the protected operational routes, with more granular action checks inside several shared GET/POST endpoints. Tenant-scoped routes use the active school context and school-qualified queries; several data relationships also have database-level ownership guards. The current test suite contains substantial direct-route, role, cross-school and data-minimization tests. Two additional tests were added in this pass for librarian/counselor least privilege; CI must confirm them before this audit can be marked complete.

This is an engineering source review, not a penetration test, legal compliance assessment, or production certification.

## Authoritative role catalogue

The application currently recognizes these roles:

- Platform Super Administrator
- School Administrator
- Principal
- Vice Principal
- Registrar / Student Records Officer
- Admissions Officer
- Academic Administrator
- Attendance Officer
- Teacher
- Finance Administrator
- Librarian / Learning Resources Officer
- School Counselor
- Parent / Guardian
- Student

The public staff-request workflow intentionally excludes parent and student roles. Student and parent access is created through a separately verified account-linking workflow.

## Route inventory and authorization

| Route | Authentication | Authorization and data boundary |
|---|---|---|
| `GET /` | Public | Public landing page; no school records |
| `GET /healthz` | Public | Minimal readiness response; does not expose database paths or credentials |
| `GET /features` | Public | Public feature overview |
| `GET,POST /login` | Public | Password verification, active-account and active-school checks, login throttling and failure auditing |
| `GET /dashboard` | Login required | Role-specific dashboard; platform super-admin must explicitly select an active school before tenant modules |
| `GET,POST /admin/select-school` | Login required | Platform super-admin invariants required; only active schools can be selected |
| `POST /logout` | Login required | POST-only logout and audit event |
| `GET,POST /admin/request-access` | Public | Pending staff requests only; role allowlist excludes student/parent; requests do not grant login access |
| `GET,POST /admin/users` | Login required | Platform super-admin only; manages staff requests and verified student/parent account links across explicitly selected school records |
| `GET,POST /academics` | Login required | Academic view allowlist; grade/attendance write actions use a narrower entry-role allowlist; teachers must hold active class/subject assignment; attendance officers may record attendance but not grades |
| `GET,POST /timetable` | Login required | View: super-admin, school admin, principal, vice principal, academic admin, registrar, teacher. Manage: super-admin, school admin, principal, vice principal, academic admin. Teachers see their own assigned lessons |
| `GET /my-report-cards` | Login required | Student only; active linked student record and published reports for that student |
| `GET,POST /report-cards` | Login required | View: super-admin, school admin, principal, vice principal, academic admin, teacher, registrar. Publish/unpublish: super-admin, school admin, principal, vice principal, academic admin. Teacher and registrar are preview-only |
| `GET,POST /students/register` | Login required | Super-admin, school admin, principal, vice principal, registrar, admissions officer, academic admin; active school context required |
| `GET /students/<student_id>` | Login required | Student-record role allowlist; school-scoped lookup; private guardian, birth-date and notes fields limited to principal/registrar |
| `GET,POST /students/<student_id>/edit` | Login required | Principal and registrar only; school-scoped lookup and update |
| `GET /students` | Login required | Student-record role allowlist; school-scoped results; teachers limited to assigned classes; private fields omitted for non-principal/registrar roles |
| `GET,POST /student-finances` | Login required | Super-admin, school admin, principal, registrar, finance admin; school-scoped ledger and balance controls |
| `GET,POST /learning-materials` | Login required | School context required. Publishing roles: content publishers plus librarian and teacher. Other roles do not receive unrestricted resource visibility |
| `GET /learning-materials/<material_id>/download` | Login required | School-scoped record; audience, archive and class-assignment rules checked before file delivery; teachers constrained to assigned classes |
| `POST /learning-materials/<material_id>/archive` | Login required | Materials-manage roles only (content managers plus librarian); school-scoped mutation and audit |
| `GET,POST /announcements` | Login required | School-scoped visibility by audience. Create/edit roles: super-admin, school admin, principal, vice principal, academic admin, registrar |
| `POST /announcements/<announcement_id>/archive` | Login required | Content-manage roles only; school-scoped mutation and audit |
| `GET,POST /finance/payments` | Login required | Super-admin, school admin, principal, registrar, finance admin; school-scoped student lookup, serialized ledger/balance update and transactional audit |
| `GET /parent/children` | Login required | Parent only; returns explicitly linked active children and their authorized school-scoped report, attendance and finance summaries |
| `GET,POST /assignments` | Login required | Staff roles: super-admin, school admin, principal, vice principal, academic admin, registrar, teacher; students may view/submit only for their linked active record and class. Teacher creation/grading is constrained to assigned class/subject and teacher-authored work |
| `GET,POST /staff` | Login required | Staff management: super-admin, school admin, principal, vice principal. Assignable/manageable roles narrow by leadership level; no school-crossing account changes |
| `GET /attendance-report` | Login required | Super-admin, school admin, principal, vice principal, registrar, academic admin, attendance officer, teacher; teachers limited to classes with active assignments |
| `GET /school-performance` | Login required | Leadership roles receive authorized aggregate views; attendance officer receives attendance metrics; finance admin receives financial metrics only; all queries use selected school |
| `POST /staff/demo-team` | Login required | Platform super-admin invariants, explicit confirmation and selected active school required; provisions fictional demo accounts only |

## Role-specific boundaries

### Teachers

- May view and enter academic information only within active assigned class/subject permissions.
- May view only their assigned timetable lessons and attendance-report classes.
- May create and grade homework only for their own authorized class/subject assignments.
- May preview report cards but cannot publish or unpublish them.
- Cannot create official student records, edit official student profiles, manage staff accounts, access finance workflows, or publish/archive school-wide announcements.
- Teacher-facing student queries omit private guardian and birth-date fields at the SQL result level.

### Attendance officers

- May access the academic and attendance reporting workflows needed for attendance duties.
- May record attendance but are excluded from grade-entry permissions.
- Do not receive finance, student-record editing, staff-management or school-wide leadership privileges.

### Finance administrators

- May access finance/payment workflows and financial dashboard metrics.
- Cannot access academic grading, student-record administration, or school-wide performance metrics outside the finance subset.
- Cannot use teacher, registrar or leadership permissions merely because they can access financial records.

### Librarians

- May publish/manage learning resources under the resource-specific role policy.
- Are not staff managers, student-record administrators, academic grade-entry users, finance users, or school-wide announcement publishers.
- Two direct-route tests have been added to verify the restricted workflows and allowed resource-page access.

### Counselors

- The role exists in the staff catalogue but currently has no dedicated counselor module.
- It is intentionally excluded from student-record, finance, academic, staff-management, attendance-report, homework and school-performance route allowlists.
- A future counseling workflow must define confidential case-note access, guardian visibility, safeguarding escalation and retention rules before counselor access is expanded. Do not infer access from the role label alone.

### Parents and students

- Parent accounts require identity/relationship verification and explicit school-scoped child links.
- Student accounts require a verified active student record; a student can access only their own published reports, class homework and linked learning resources.
- Unlinked accounts do not gain access by supplying a student ID in the request.

### Platform Super Administrator

- Global access requires all three invariants: role is `super_admin`, platform flag is true, and the user has no assigned school.
- Tenant modules require explicit selection of an active school.
- A school-assigned account cannot become a platform super-admin merely by changing a role or flag.

## Existing automated evidence

The current test inventory documents 176 tests before this pass. It includes:

- Teacher direct-route denials for student registration/edit, finance, announcement publication/archive and unassigned academic data.
- Teacher assignment scoping, revocation, timetable visibility and homework restrictions.
- Student and parent account linking, ownership and relationship restrictions.
- Registrar preview-only report-card permissions and principal publication controls.
- Attendance-officer attendance-only permissions.
- Finance dashboard data minimization and cross-school payment denial.
- School-to-school object access denial for student records, grades, attendance, reports, finance, parent links and homework.
- Atomic audit rollback for financial, academic, registration and homework mutations.
- Session invalidation for suspended accounts and suspended schools.

Two new direct-route tests were committed to expand coverage for librarian and counselor roles. The post-change CI result is pending and must be checked before counting those tests as passed.

## Remaining Prompt 02 verification work

- [ ] Confirm CI passes on Python 3.11 and 3.14 after the new specialist-role tests.
- [ ] Verify the exact Flask URL map against this 30-route source inventory in a test, so newly registered routes cannot silently escape the audit.
- [ ] Expand negative direct-route tests for every role on sensitive mutation endpoints, including resource archive, timetable create/delete, staff actions, demo provisioning and report publication.
- [ ] Add/confirm cross-school object-ID tests for resource download/archive, announcement archive, timetable deletion, homework submission/grade and parent-linked reports/finance.
- [ ] Review export/download responses for school, role and field-level data minimization.
- [ ] Add a formal permission matrix artifact that is machine-readable and used by tests where practical.
- [ ] Review counselor access policy before implementing any counseling feature.

## Release and safety boundaries

No live account was used to submit forms. No live student or financial records were read or changed. No production database, Render configuration, paid infrastructure, merge or deployment was changed.

**Prompt 02 status: IN PROGRESS.** Do not begin Prompt 03 or any Version 5 work until the outstanding authorization verification tasks are complete and the test evidence is recorded.

