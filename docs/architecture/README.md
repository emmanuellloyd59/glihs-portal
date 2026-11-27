# Architecture Decision Records

ADRs capture decisions that materially affect system structure, data ownership, security, integrations or operational behavior.

## Status meanings

- **Proposed:** under discussion; not a binding implementation direction.
- **Accepted:** approved architectural direction; implementation may still be pending.
- **Implemented:** code and tests demonstrate the decision is in effect for the stated scope.
- **Superseded:** replaced by a later ADR; retain the record and link the replacement.

## Register

| ADR | Decision | Status | Scope |
|---|---|---|---|
| [ADR-0001](adr/0001-modular-monolith-and-incremental-evolution.md) | Retain Flask and evolve as a modular monolith | Accepted | Application architecture and extraction strategy |
| ADR-0002 | Tenant boundary and school-scoped authorization | Accepted as product/security policy; implementation incomplete | School/campus ownership and access |
| ADR-0003 | API compatibility and versioning | Accepted as a future API standard; implementation pending | External/mobile integrations |
| [ADR-0004](adr/0004-school-network-lifecycle-and-campus-model.md) | Institution lifecycle, school groups and campus ownership | Accepted; initial implementation in progress | School Network and tenant administration |
| [ADR-0005](adr/0005-tenant-owned-school-configuration.md) | Tenant-owned organization defaults and campus overrides | Accepted; implementation in progress | Branding, academic, regional, module and operational settings |
| [ADR-0006](adr/0006-global-identities-and-school-memberships.md) | Global platform identities and school-scoped memberships | Implemented on feature branch; acceptance pending | Multi-school accounts, invitations, membership switching and session security |

## When an ADR is required

Create or update an ADR for framework/database replacement, modular boundary changes with broad impact, new infrastructure, tenant/identity model changes, public API compatibility changes, data retention or security model changes, and changes to release/rollback strategy.

Small local implementation choices do not require an ADR.

## ADR template

Copy this structure into `docs/architecture/adr/NNNN-short-title.md`:

1. **Status and date**
2. **Context and problem**
3. **Decision**
4. **Alternatives considered**
5. **Consequences** (positive, negative, operational and security)
6. **Compatibility and migration**
7. **Verification and acceptance**
8. **Related backlog IDs and superseding ADRs**

ADRs are append-only in intent: do not rewrite history to hide a prior decision. If a decision changes, create a new ADR and mark the old one superseded.

| [ADR-0007](adr/0007-scoped-rbac-abac-authorization.md) | Scoped RBAC and ABAC authorization | Implemented on feature branch; CI/security review pending | Platform, organization, school and campus permissions with explicit deny and audit |
