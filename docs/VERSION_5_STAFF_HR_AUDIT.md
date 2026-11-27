# Version 5 — Prompt 06 Staff and Human Resource Management Audit

**Date:** 2026-11-26  
**Development branch:** `version-5/multi-school-management-2026-11-26`  
**Prompt source:** `Pasted text(6).txt`, Prompt 06 — Complete School Administration and Human Resource Management.

## Prompt 06 scope

Develop a comprehensive school staff management system including:
- Staff registration and verification.
- Staff profiles and employment records.
- Department management.
- Position and responsibility management.
- Teacher class and subject assignments.
- Staff attendance.
- Leave applications and approvals.
- Account activation and suspension.
- Password recovery.
- Staff activity history.
- Role and permission management.
- Separate administrative experiences for authorized school leadership.
- Teacher self-service limited to permitted professional information and assigned academic responsibilities.
- Auditable history for consequential staff-management actions.

## Existing implementation verified

| Capability | Existing state | Gap |
|---|---|---|
| Staff registration | Public staff access request creates pending account; admin approval activates it | Verification evidence is currently represented mainly by approver and approval timestamp |
| Staff profiles | Basic user identity fields: username, full name, role, status and school | No dedicated staff profile or employment history |
| Departments | Not found | No school-scoped department records |
| Positions/responsibilities | Roles exist as account roles | No position catalogue or employment responsibilities |
| Teacher assignments | Active class/subject assignment model exists and is independent of timetable rows | Needs to remain integrated with staff HR and role changes |
| Staff attendance | No staff attendance workflow found | Existing attendance module records student attendance only |
| Leave | Not found | No requests, approvals, balances or history |
| Account activation/suspension | Pending/active/suspended states and admin actions exist | All staff-management entry points must maintain assignment history |
| Password recovery | No password recovery route or UI found | No reset-token workflow |
| Staff activity history | Audit log exists | No staff-focused activity history view |
| Role/permissions | Role allow-lists are hard-coded in route modules | No school-level permission matrix or audited permission management |
| Leadership experiences | Staff Management and platform account administration pages exist | No complete HR workspaces for departments, attendance, leave and staff self-service |
| Teacher self-service | Teacher assignment restrictions exist | No dedicated self-service profile, attendance and leave workspace |

## Security and design decisions

1. Preserve the existing user account and teacher-assignment model; do not replace or silently reassign existing teacher class/subject records.
2. Keep employment records separate from login credentials. Do not put employment details, leave reasons or attendance notes into the `users` authentication row.
3. Scope every HR record to `school_id` and enforce user/actor ownership at the database and route layers.
4. Teachers may update only explicitly permitted contact/profile fields and may request leave or record their own attendance. They cannot change their role, employment status, department, position, salary or assignments.
5. Leave approval must be performed by an authorized different person; self-approval is prohibited.
6. Staff attendance corrections, leave decisions, employment changes and permission changes must be audited.
7. Password recovery must use high-entropy, hashed, expiring, single-use tokens. No plaintext token or password may be stored in audit logs.
8. No email/SMS provider is configured for this workflow. If recovery tokens are issued, an authorized administrator must share them privately out-of-band; do not invent an email delivery capability.
9. Role permission overrides must never exceed the maximum permissions assigned to that role by the platform policy. A school administrator cannot grant platform-level privileges or modify their own administrator authority.
10. All tests use fictional accounts and disposable databases. No production data, Render configuration or live accounts may be modified.

## Proposed implementation modules

- `staff_profiles`: school-scoped employee profile and verification metadata.
- `staff_departments` and `staff_positions`: school-scoped organizational structure.
- `staff_employment_records`: append-only employment event history.
- `staff_attendance`: separate staff attendance, not student attendance.
- `staff_leave_requests`: school-scoped requests and leadership decisions.
- `staff_password_reset_requests`: admin-reviewed, expiring, single-use reset tokens.
- `school_role_permissions`: audited per-school overrides within a fixed role permission ceiling.
- Staff-focused routes and separate templates for profile, directory, structure, attendance, leave, activity and permission administration.

## Acceptance gates

- Existing staff access-request, approval, teacher-assignment and account lifecycle tests continue to pass.
- School A cannot read or mutate School B's HR data through direct URLs, manipulated IDs, forms, searches or approvals.
- A teacher can access only their own self-service profile/attendance/leave records and assigned academic responsibilities.
- No staff member can approve their own leave or alter protected employment fields.
- Attendance duplicates, overlapping leave requests and duplicate employee numbers are rejected.
- Reset tokens expire, are single-use, are stored only as hashes and cannot be used for another account or school.
- Employment, attendance, leave, account and permission changes have attributable audit events.
- Run the full regression suite on Python 3.11 and 3.14 before proceeding to Prompt 07.
