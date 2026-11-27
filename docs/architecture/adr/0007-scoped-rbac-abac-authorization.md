# ADR-0007: Scoped RBAC and ABAC authorization

- **Status:** Implemented on feature branch; CI/security review pending
- **Date:** 2026-11-27
- **Related backlog:** AUTH-001, IDN-001, TEN-001, SEC-001

## Context

The Digital Education Ecosystem is multi-school, multi-campus and multi-country. A global identity may hold several memberships. Role-only checks and template-only visibility are insufficient: permissions must be tenant-scoped, resource-aware, configurable and auditable without weakening the identity/session model.

## Decision

Adopt a centralized scoped authorization service combining role-based access control (RBAC) and attribute-based access control (ABAC).

- Scopes: platform, organization (school group), school and campus.
- Role baselines are stored in `authorization_role_permissions`.
- Explicit user/role grants and denials are stored in `authorization_grants`.
- Authorization scope inheritance follows platform → organization → school → campus.
- Any matching explicit deny wins over inherited allows.
- ABAC verifies authenticated account status, active membership, school lifecycle, campus ownership, resource tenant, campus, resource owner, assigned user and optional grant conditions.
- Unknown permissions and missing context fail closed.
- School managers can delegate only permissions they hold; platform/organization and high-impact membership/role/configuration/campus permissions are not delegable through ordinary school grants.
- Authorization decisions and policy mutations are appended to `authorization_audit`.

Existing `users`, password hashes, account IDs, account status and membership/session mechanisms are preserved. Additional membership roles not present in the legacy users CHECK constraint are represented in `school_memberships.role` and mapped to a compatible legacy account role only when creating the global account.

## Consequences

Positive:
- Backend routes and services can share one permission contract.
- Role baselines are configurable by platform policy administrators.
- School/campus-specific grants and denials can be applied without changing global roles.
- Organization-wide grants can be inherited by member schools but are controlled by platform governance.
- Explicit deny, least privilege, active membership checks and audit are centrally consistent.

Trade-offs:
- Existing legacy routes still require a systematic migration to named permission checks.
- Role policy changes are platform-governed; school managers use attenuated scoped grants rather than changing global role baselines.
- SQLite is the current implementation; PostgreSQL RLS remains a production follow-up.

## Verification

Added tests for role defaults, cross-tenant access, vertical/horizontal escalation, explicit deny, inactive membership, campus isolation, organization inheritance, unauthorized policy mutation and audit events. CI has not yet been verified for the final PR head.
