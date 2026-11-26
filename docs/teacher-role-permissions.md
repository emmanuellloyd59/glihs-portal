# Teacher Role — Least-Privilege Access Specification

Status: proposed access policy; not yet enforced in application code.
Scope: CMHS Digital Campus staff role `teacher`.

## Objective

Give a teacher only the functions required to teach their assigned classes and subjects. Access must be enforced on the server for every page, API/action, file download, and submitted record—not merely hidden in dashboard templates.

## Target teacher workspace

| Capability | Target permission | Boundary |
|---|---|---|
| Teacher dashboard | View | Personal teaching summary and assigned work only |
| Personal profile | View/update limited fields | No role, account status, or school assignment changes |
| Teaching timetable | View | Own assigned lessons only |
| Assigned classes | View | Only classes explicitly assigned to the teacher |
| Student roster | View | Only students in assigned classes; basic identity and class details |
| Attendance | Record/view | Only assigned classes; prevent edits outside the permitted correction window |
| Grades and assessments | Create/view/update own permitted entries | Only assigned classes and subjects; audit all changes |
| Report-card preparation | Preview | Assigned students only; no publish/unpublish authority by default |
| Homework and assignments | Create/view/manage own assignments | Only assigned classes; grading limited to own assignments |
| Learning materials | Upload/view | Only assigned classes or approved school-wide resources; no archive/delete authority |
| Announcements | Read | Publishing should require an authorized school leader by default |
| Student support notes | No access by default | Sensitive counseling and safeguarding records stay restricted |

## Explicitly prohibited for the teacher role

- Finance, fees, payments, receipts, balances, refunds, and financial reports.
- Staff administration, account creation, approvals, suspensions, and role changes.
- School settings, school-wide configuration, and tenant administration.
- Editing student legal identity, date of birth, guardian contacts, emergency contacts, enrollment, or official student records.
- Viewing unrelated classes or students outside assigned teaching responsibilities.
- Publishing/unpublishing official report cards without explicit delegated authority.
- Archiving or deleting school-wide announcements and learning resources.
- Viewing audit logs, private safeguarding/counseling records, or other staff members' private data.

## Findings from the current main branch audit

The following are observed in the repository as inspected on 2026-11-26:

1. `/academics` includes teachers in both the academic-view and academic-entry role sets. Its student query is school-wide for active students; class/subject assignment scoping is not evident in this route. This is broader than the target policy.
2. `/students` includes teachers in `STUDENT_RECORD_ROLES`. The list query intentionally omits birth dates and guardian contacts for teachers, which is a useful privacy control, but the roster is not yet shown to be limited to assigned classes.
3. Student-record editing is restricted to principal and registrar, not teachers. Preserve this restriction.
4. Teachers can view the timetable, while timetable management is restricted to leadership/academic administration. Preserve this separation; additionally scope teacher views to their own lessons.
5. Teachers can view report-card tools, but the publish/unpublish role set excludes teachers. Preserve the publication restriction and scope previews to assigned students.
6. Teachers are currently included in homework staff roles and can create assignments for any valid school class supplied by the request. The grading action is better restricted: teachers can grade submissions only for assignments they created. Add an assignment-to-teacher-class authorization check before creation and retain creator-only grading.
7. Teachers are currently included in learning-material publishing. Upload requests accept a class level, but the role check does not itself prove the teacher is assigned to that class. Narrow uploads to assigned classes or an approved shared-resource workflow; keep archive/delete restricted to managers.
8. Teachers are currently included in announcement publishing, although archive/manage roles exclude them. Make teachers read-only by default unless a school leader grants a narrowly scoped publishing permission.
9. Finance access is restricted to administrative/finance roles and does not include teachers. Preserve this restriction.

## Required implementation and test sequence

1. Map every teacher-visible route, POST action, download endpoint, and dashboard link.
2. Establish authoritative teacher-to-class and teacher-to-subject assignments. Never trust class or subject IDs supplied by the browser without checking the authenticated teacher's assignment.
3. Apply school and assignment scope to every read and write query.
4. Restrict report-card publication, student-profile editing, finance, staff administration, announcements management, and resource archiving to their designated roles.
5. Add positive tests for permitted assigned-class work and negative tests for unassigned classes, unrelated students, cross-school records, forged IDs, and direct POST requests.
6. Verify dashboard links and server-side permissions agree.
7. Run the complete test suite and review results before proposing any merge or deployment.

## Safety and release constraints

- This specification does not authorize changes to production data, credentials, hosting configuration, or the live deployment.
- Do not reset, delete, or replace existing student, staff, or school records.
- Keep changes isolated on this feature branch until reviewed.
- Use fictional test data only. Do not use real student or guardian information in tests.
- Do not merge or deploy without the project owner's explicit approval.
