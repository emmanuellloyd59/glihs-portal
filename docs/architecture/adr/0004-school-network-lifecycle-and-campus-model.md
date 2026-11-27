# ADR-0004: School Network institutional lifecycle and campus ownership

- **Status:** Accepted
- **Date:** 2026-11-27
- **Related backlog:** NET-001, NET-002, TEN-001, SEC-001
- **Depends on:** ADR-0002 (school tenant boundary)

## Context

The Digital Education Ecosystem must serve independent institutions and school groups without making the current CMHS Digital Campus the permanent platform identity. Existing school records and school IDs are already referenced by users, students, academic records, finance and audit data. The current SQLite schema has a minimal schools table and does not yet model institutional profiles, school groups, campuses, contacts or a complete institution approval lifecycle.

## Decision

1. A school institution is the tenant. A campus is a child organizational unit and never a separate tenant by default.
2. A school group is an organizational grouping above multiple school institutions. Group membership does not merge tenant data or grant cross-school access. Each member school may have multiple campuses.
3. Each school has a globally unique stable slug/identity. Existing integer school IDs and slugs are preserved.
4. Institutional profile data includes legal name, classification, ownership type and responsible owner/authority, registration number, description, website, physical location, country, time zone and contact channels.
5. New registrations enter pending lifecycle state and remain operationally suspended. Platform review and approval are separate from activation. Only a platform owner may approve, reject, activate, suspend, reactivate or archive an institution.
6. School administrators may manage only their own active school's profile, campuses and contacts. Platform-owner access is separate and explicitly checked. Client-supplied school IDs never establish authority.
7. Platform identity is neutral. Institution branding is resolved from the authenticated school context; CMHS remains an institution-specific record/configuration.
8. Schema evolution is additive and idempotent. No production migration or existing record rewrite is authorized by this ADR.

## Lifecycle

pending → approved → active → suspended → active

Pending/under-review registrations may be rejected. Rejected or suspended institutions may be archived. Invalid transitions return a conflict. Existing legacy schools.status remains synchronized (active or suspended) for compatibility with current authorization code.

## Consequences

The model supports independent schools, school groups and multiple campuses while preserving school-level data ownership. The existing users.school_id remains the compatibility authorization source for this foundation; a future global identity and membership redesign must be separately migrated and tested. Domain verification, tenant-wide legacy route audit, database row-level security and production provisioning remain separate release gates.

## Implementation and verification

The initial implementation adds profile and lifecycle columns, school groups, group memberships, campuses, contacts and a dedicated network audit table. It provides public pending registration, versioned JSON endpoints, platform-owner review screens, school profile/campus screens and tests for lifecycle, group membership and cross-school denial.

The existing CMHS school record remains in place and receives only additive metadata columns. No production database was modified. A passing feature test suite does not by itself establish production readiness or complete isolation across every legacy route.
