# ADR-0005: Tenant-owned School Configuration Centre

- Status: Accepted for incremental implementation
- Date: 2026-11-27
- Decision owners: Digital Education Ecosystem platform architecture

## Context

Schools need independent identity, branding, contact details, academic calendars, grading rules, regional preferences, enabled modules and operational policies. Multi-campus institutions also need selective campus-level overrides. Configuration must never cross school boundaries, and existing school/campus data must survive adoption.

## Decision

1. A school organization is the configuration owner and tenant boundary.
2. Store one versioned organization configuration document per school, keyed by the existing schools.id.
3. Store sparse campus override documents keyed by (school_id, campus_id) with a composite foreign key to the campus owned by that school.
4. Effective campus configuration is the organization configuration recursively overlaid by that campus's explicit overrides. Omitted sections inherit organization defaults.
5. Module availability is organization-scoped in this iteration; campus overrides cannot grant or remove module entitlements.
6. Validate known sections and field types server-side. Validate IANA time zones, language tags, currency-code format, colors, URLs, calendar dates and grading ranges.
7. Every successful change increments a monotonic version and writes an immutable JSON snapshot plus an audit event in the same database transaction.
8. Use optimistic version checks for organization updates to prevent stale writes.
9. Keep legacy schools fields synchronized for existing routes and templates; the versioned configuration document is the new settings source.
10. Apply organization branding to the authenticated school's shared application shell.

## Migration and compatibility

- Add tables only; do not drop, rename, or rewrite school, campus, user, student, academic, or finance records.
- Seed an initial organization snapshot from the current schools name, website, address, country, time zone and contact fields.
- Seed campus override snapshots from existing campus names, addresses, country and contact fields.
- Preserve requested services from onboarding as initial enabled modules when the service-request table exists.
- Migration is idempotent: existing configuration documents and version histories are never replaced.
- Existing campus records remain canonical for campus identity and location.

## Security

- Resolve tenant scope from the authenticated user's server-side school membership. A supplied school or campus ID is only a selector, never authority.
- Platform owners must explicitly select a school.
- Cross-school campus references return not found.
- School and campus configuration queries always include school_id.
- Campus composite foreign keys prevent cross-school ownership references.
- Configuration values are data, not executable code. Logo URLs are restricted to HTTPS or local /static/ assets.
- Module configuration does not replace subscription checks, authorization, or feature-level access controls.

## Consequences and limitations

- JSON documents allow country-specific and future configuration sections without a schema migration for every setting, while the API validates supported fields.
- Campus overrides are sparse and require clients to resolve effective settings through the API.
- Logo URL support is implemented; managed file upload/object storage is a future integration.
- The current application database remains SQLite. PostgreSQL RLS and production migration rehearsal remain separate gates.
- Configuration history is immutable; a future restore workflow must create a new version rather than mutate history.

## Verification

- Migration idempotence and legacy value preservation.
- Organization A cannot read or update organization B configuration.
- Campus overrides cannot reference another school's campus or alter organization module entitlements.
- Invalid currency/time zone/calendar/grading values are rejected.
- Version conflicts reject stale writes.
- Full regression suite on supported Python versions.

## Related backlog

- CONF-001 — Centralized School Configuration Centre.
- NET-001 — School Network tenant foundation.
- NET-003 — International school onboarding.
