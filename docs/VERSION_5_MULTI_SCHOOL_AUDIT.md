# Version 5 — Prompt 05 Multi-School Architecture Audit

**Date:** 2026-11-26  
**Base:** Version 4 draft branch `version-4/reconciled-main-2026-11-26`  
**Development branch:** `version-5/multi-school-management-2026-11-26`

## Prompt 05 requirements

Transform the current multi-school foundation into a fully enforced multi-school management architecture with:
- Secure school registration and profiles.
- School activation and suspension.
- School administrators and delegated school-level administration.
- Academic calendars and school-specific configuration.
- Tenant-aware database access and authorization across all school-owned records.
- Cross-school isolation tests for direct URLs, manipulated IDs, search, exports, reports and administrative actions.
- Preservation of the existing single-school CMHS configuration.

## Existing implementation findings

### Existing foundation
- The `schools` table currently contains only `id`, `name`, `slug`, `status` (`active`/`suspended`) and `created_at`.
- Existing legacy data is assigned to the stable default CMHS school row (school ID 1); this migration path must remain non-destructive.
- `require_school_context()` revalidates active school status on protected requests. Ordinary users use their assigned `users.school_id`; platform super-admins must explicitly select an active school in session.
- Public staff access requests resolve a selected active school server-side and create pending accounts; requests do not grant access until approved.
- Platform super-admin account management can create student and parent accounts and link them to verified school records.
- The codebase already has school-scoped queries and database guards across core student, finance, academic, report-card, timetable, learning-resource, announcement and homework workflows.

### Missing or incomplete capabilities
- No complete platform-level school registration/profile/lifecycle interface exists.
- School profile data is limited to name and slug; no contact, location, registration, timezone or currency metadata is stored.
- School activation/suspension is represented by a status field and enforced by tenant context, but lacks a complete audited lifecycle workflow and administrator-facing interface.
- School administrator accounts can be requested/approved as users, but there is no separate assignment/history model or dedicated school administrator management workflow.
- There is no school academic calendar/term table or school-specific settings store.
- Existing multi-school isolation tests cover several direct-route and cross-school cases, but the new acceptance work must systematically cover every table and workflow, including search, exports, reports and platform-level actions.

## Proposed additive architecture

1. Extend `schools` with optional profile and configuration metadata while preserving existing IDs, slugs and status values.
2. Add a tenant-scoped `school_settings` store with a strict allow-list of setting keys.
3. Add `school_academic_calendars` with unique school/year/term records, date constraints and school-scoped actor ownership.
4. Add `school_admin_assignments` to retain assignment/revocation history and prevent a school administrator from being silently reassigned across tenants.
5. Add platform-super-admin-only school registration, profile update, activation and suspension workflows.
6. Add school-scoped school-administrator settings and academic-calendar workflows with explicit server-side role and tenant checks.
7. Add database guards and migration-time audits for all new relationships.
8. Add regression tests for lifecycle transitions, admin assignment, school suspension, settings/calendar tenant isolation, direct object IDs and preservation of the existing CMHS school.

## Safety constraints

- All schema changes must be additive, transactional and repeatable.
- All tests use temporary databases and fictional schools/users.
- Never reset, rename, reassign or seed production school records.
- Never change Render service settings, persistent storage, production databases or paid infrastructure.
- No merge or deployment without explicit owner approval.

## Acceptance matrix

| Requirement | Existing status | Version 5 work |
|---|---|---|
| School registration | Missing dedicated workflow | Implement platform-admin-only registration |
| School profiles | Name and slug only | Add contact/location/registration metadata |
| Activation/suspension | Basic status and request-time guard | Add audited lifecycle actions and tests |
| School administrators | User role exists; no assignment history | Add assignment model and delegated settings workflow |
| Academic calendars | Missing | Add school/year/term calendar |
| School-specific settings | Missing | Add allow-listed settings store |
| Tenant-aware access | Partial foundation exists | Inventory and extend coverage to all new records/workflows |
| Cross-school tests | Existing partial coverage | Add lifecycle, search, report, export and direct-ID tests |
| CMHS preservation | Existing school ID 1 and records | Preserve exactly; verify through migration tests |

This audit is a starting point for implementation, not a certification that all multi-school controls already exist.
