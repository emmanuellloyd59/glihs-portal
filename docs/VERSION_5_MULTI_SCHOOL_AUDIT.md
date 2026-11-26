# Version 5 — Prompt 05 Multi-School Architecture Audit

**Audit date:** 2026-11-26  
**Base:** Version 4 draft branch `version-4/reconciled-main-2026-11-26`  
**Development branch:** `version-5/multi-school-management-2026-11-26`  
**Pull request:** [#50 — Version 5 multi-school management](https://github.com/emmanuellloyd59/glihs-portal/pull/50)

## Prompt 05 requirements

Transform the current multi-school foundation into a fully enforced multi-school management architecture with:
- Secure school registration and profiles.
- School activation and suspension.
- School administrators and delegated school-level administration.
- Academic calendars and school-specific configuration.
- Tenant-aware database access and authorization across all school-owned records.
- Cross-school isolation tests for direct URLs, manipulated IDs, search, exports, reports and administrative actions.
- Preservation of the existing single-school CMHS configuration.

## Implementation delivered

### School registration and profiles
- Extended the existing `schools` table additively with registration number, legal name, country, county/district, address, contact email/phone, website, IANA timezone and ISO-style currency code.
- Added platform-super-admin-only school registration, profile update, activation and suspension actions.
- New schools start suspended. Activation requires at least one active school-administrator assignment.
- The existing CMHS school remains ID 1 with its existing slug and data. The default school cannot be suspended through this workflow.
- School registration, profile, lifecycle and administrator changes write audit records in the same transaction.

### School administrators and delegated administration
- Added `school_admin_assignments` with active/revoked history and a single-active-assignment constraint.
- Existing active school-admin accounts are backfilled during the additive migration.
- Platform super-admins can provision and assign school administrators, assign existing eligible accounts, and revoke assignments.
- Every school-admin tenant request now requires an active assignment to that same school.
- Last active school administrators cannot be suspended, deleted/deactivated or revoked while their school remains active.
- Staff Management create, suspend, reactivate and role-change paths now maintain school-admin assignment history consistently.
- Database triggers reject invalid assignment actors, mismatched user/school ownership and cross-school settings/calendar actors.

### Academic calendars and school settings
- Added tenant-scoped `school_academic_calendars` with unique school/year/term records, ISO date validation, overlap protection and one active term per school.
- Added tenant-scoped `school_settings` with an allow-list of configuration keys, value validation and school-owner checks.
- School administrators can manage their own school profile/settings and calendars; approved academic leadership roles can manage calendars within their school.
- Calendar/settings records and actor attribution are protected by database guards.

## Cross-school verification

Existing Version 4 tests already exercise direct student URLs, manipulated student IDs, student search, grades, attendance, report cards, finance payments, homework and school-scoped administrative actions. The search regression confirms a School A teacher does not see a School B student in `/students?q=...`.

Version 5 adds tests for:
- School registration, suspended-by-default lifecycle and preservation of CMHS ID 1/slug.
- School administrator provisioning and assignment enforcement.
- Cross-school settings manipulation and direct calendar-ID update denial.
- Calendar overlap and active-term constraints.
- Existing-session denial after school suspension.
- Assignment revocation, unassigned administrator denial and last-administrator protection.
- Staff Management school-admin creation, suspension, reactivation and role transitions.

**Export limitation:** no CSV/export endpoint was found in `app.py` or `staff_attendance.py`; the CSV handling present is demo-student import. There is therefore no current export route to exercise for tenant isolation. Any future export feature must include school-scoped query constraints and cross-school regression tests before release.

## Automated verification

<badge>**Passed**</badge>

- Full suite: **186 tests**
- Python 3.11: passed
- Python 3.14: passed
- GitHub Actions run: [#45912090402](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45912090402)
- Tested commit: `98893fc6b54f5ff1c432a31e21e9dabb9cec0cf6`
- Runtime on Python 3.11: 125.612 seconds

The successful automated suite verifies the V5 branch state at the commit above. It does not substitute for authenticated browser testing or production deployment validation.

## Remaining acceptance work

- Complete a table-by-table and route-by-route tenant-isolation inventory, including background jobs, scheduled tasks, every search and every future export.
- Integrate school settings and academic calendar values into all legacy grading, fees, attendance and reporting calculations; the current V5 work stores and protects these values but does not claim every legacy module consumes them.
- Perform authenticated browser QA on desktop and mobile using a durable, isolated staging environment.
- Verify backup/restore and rollback procedures for the expanded school schema before any production migration.

## Safety constraints

- All schema changes are additive, transactional and repeatable.
- Automated tests use disposable databases and fictional schools/users.
- No production school records were reset, renamed, reassigned or seeded.
- No Render service settings, persistent storage, production database or paid infrastructure were changed.
- PR #50 remains a draft. No merge or deployment was performed.

## Acceptance matrix

| Requirement | Version 5 result | Status |
|---|---|---|
| School registration | Platform-admin-only, suspended-by-default workflow | Implemented and tested |
| School profiles | Registration/contact/location/timezone/currency fields | Implemented |
| Activation/suspension | Audited lifecycle and request-time enforcement | Implemented and tested |
| School administrators | Assignment history, delegated administration, last-admin safeguards | Implemented and tested |
| Academic calendars | Tenant-scoped terms, overlap and active-term guards | Implemented and tested |
| School-specific settings | Allow-listed tenant-scoped settings | Implemented; legacy module integration remains |
| Tenant-aware access | Existing V4 controls retained; V5 new records and school-admin paths guarded | Implemented; exhaustive inventory remains |
| Search isolation | Existing direct search regression retained | Tested |
| Export isolation | No export endpoint currently exists | Not applicable; future feature gate required |
| Report isolation | Existing cross-school report-card tests retained | Tested |
| Administrative actions | Assignment, suspension, revocation and role-transition tests | Tested |
| CMHS preservation | School ID 1 and slug preserved | Tested |

This audit records the implemented V5 foundation and its evidence. It is not a certification of regulatory compliance or production readiness.
