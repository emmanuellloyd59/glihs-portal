# ADR-0002: School is the tenant boundary

- **Status:** Accepted as product/security policy; implementation incomplete
- **Date:** 2026-11-27
- **Related backlog:** TEN-001, SEC-001, NET-002

## Context

The ecosystem must serve independent schools while allowing each school to manage its own campuses, users, students, academic records, finance, learning and communication. The current application has school ownership foundations and two-school regression tests, but the repository's release documentation explicitly states that tenant isolation and all mutation paths are not yet fully audited or production-ready.

## Decision

A school organization is the default tenant and owns its campuses and school data. A campus is a child organizational unit, not a separate tenant by default. Platform-level identity and administration are distinct from school memberships and school-level roles.

All access to school-owned data must be scoped using authenticated, server-derived school context. Client-submitted school/campus/resource identifiers are untrusted selectors and must be checked against the active membership and authorization policy. Every query, join, aggregate, export, file download, background task and mutation must preserve school ownership. Cross-school references must be rejected.

Platform support or super-administrator access is separate from school administration and must be purpose-bound, least-privilege, time-limited and audited before any support-impersonation feature is implemented.

## Alternatives considered

- Treat each campus as a separate tenant: rejected as the default because schools may operate multiple campuses under shared governance, users and academic structures.
- Trust client-supplied school IDs: rejected because it permits insecure direct-object references and tenant spoofing.
- Rely only on navigation visibility or role names: rejected because authorization must be enforced at the server/data boundary.
- Claim database row-level security is already active: rejected; it must not be claimed until the database strategy, policies, connection context and ordinary-role tests prove it.

## Consequences

The model supports multi-campus schools and independent school boundaries. It requires composite ownership validation, careful membership modeling and extensive cross-tenant tests. Existing legacy `users.school_id` and role fields may remain as compatibility fields during transition; do not remove them until a separately tested migration and code cutover is approved.

## Compatibility and migration

Preserve current school and user IDs, student/account links, academic and finance records. Use additive migrations and explicit backfills. Existing single-school data must map deterministically to its existing school. No production migration is authorized by this ADR.

## Verification and acceptance

For every school-owned resource, test school A and school B reads and writes, guessed object IDs, cross-school joins, list/search/aggregate/export/download paths, missing school context, suspended memberships/schools and platform-versus-school role boundaries. Production readiness additionally requires migration rehearsal, backup restoration and route-by-route audit.
