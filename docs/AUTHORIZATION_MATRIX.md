# CMHS Digital Campus — Authorization Matrix

## Scope

This matrix documents the Version 4 authorization contract. Authorization is enforced on the server for every state-changing request and every school-scoped read. Navigation visibility is a usability aid only; it is never an access-control boundary.

The authenticated account role and school assignment are loaded from the database. Tenant-scoped routes call the school-context guard, which rejects missing, suspended, or invalid school contexts. Platform super administrators must explicitly select an active school before using school-scoped workflows.

## Role boundaries

| Role | Permitted operational scope | Explicit restrictions |
|---|---|---|
| Platform Super Administrator (`super_admin`) | Platform-level account and school administration; school-scoped operations only after explicit school selection | No implicit tenant context; malformed platform flags do not grant global access |
| School Administrator (`school_admin`) | School operations and administrative workflows | Cannot administer another school or gain platform-global access |
| Principal | School leadership, student records, report publication, approved financial oversight | Tenant-bound; cannot act outside own school |
| Vice Principal | Approved academic, attendance, timetable and student workflows | No platform-global access; financial access remains restricted by route policy |
| Registrar | Student records, registration, approved report viewing and specified finance workflows | Cannot publish report cards unless separately authorized by policy |
| Admissions Officer | Student registration/admissions workflows | No financial or platform administration |
| Academic Administrator | Academic entry, academic operations, student registration and approved content workflows | No finance or platform administration |
| Attendance Officer | Attendance entry and permitted attendance views | Attendance-only write actions; cannot enter grades or manage finance |
| Teacher | Assigned class and subject academic activity; assigned-class student learning context; permitted learning-material uploads | Cannot create official student records, edit student identity records, manage staff/accounts, access finance, publish school-wide announcements, or access unassigned classes/subjects |
| Finance Administrator | School financial workflows and finance-specific dashboard metrics | No unrelated academic/private student data or cross-school access |
| Librarian | Learning-resource upload and management within the school | No student-record, finance, staff-account or platform administration |
| Counselor | Only workflows explicitly granted by a route policy | No implied access to sensitive student records merely from role name |
| Parent | Published school-wide/student-facing information and explicitly linked child information | No access to unrelated children, staff functions or finance administration |
| Student | Own linked student record's published learning information and permitted school-wide resources | No access to another student's record, staff workflows, grade entry or finance administration |

## Sensitive workflow rules

- **Student records:** registration and editing are separate permissions. Teachers may not create or edit official records. Private guardian/contact fields are returned only to roles explicitly authorized by the route.
- **Grades:** a teacher must have an active assignment for the student's class *and* the exact subject before grade entry or access to that subject's grade details.
- **Attendance:** teacher attendance entry requires an active assignment for the student's class. Attendance Officers may record attendance but cannot submit grade actions.
- **Report cards:** student accounts see only their own published report. Viewing report summaries and publishing/unpublishing are separate permissions.
- **Finance:** financial pages and payment mutations are role-gated and school-scoped. Teacher responses must not contain financial records.
- **Learning materials:** teacher uploads and downloads are restricted to assigned classes (except explicitly school-wide resources); archived resources are restricted to managers.
- **Announcements:** publishing, editing and archiving are restricted to the content-publishing/management roles. Teachers can read authorized staff-facing announcements but cannot publish school-wide notices.
- **Staff and account administration:** staff management and platform account administration are separate from academic teaching permissions.
- **Tenant isolation:** direct object identifiers, form fields, search, reports, downloads and exports must not bypass the authenticated user's school boundary.

## Verification evidence and remaining work

The automated authorization suite includes direct-post denial, teacher assignment and revocation, school suspension, cross-school student/report/finance isolation, parent-child linking, role-flag consistency, and data minimization tests. Version 4 adds direct-route regressions proving teachers cannot read or write financial workflows, publish/archive school announcements, or edit official student records.

This matrix is a policy baseline, not a claim that every possible workflow for every listed role has been implemented. The Counselor role, complete parent/student workflow coverage, exhaustive route-to-permission mapping, and browser-level verification remain items for the broader Version 4 release gate. No legal or regulatory compliance certification is implied.
