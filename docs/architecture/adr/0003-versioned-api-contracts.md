# ADR-0003: Version external APIs independently from HTML routes

- **Status:** Accepted as a future contract policy; implementation pending
- **Date:** 2026-11-27
- **Related backlog:** API-001

## Context

The ecosystem may eventually serve browser interfaces, mobile clients, school websites and third-party integrations. The current application primarily exposes Flask HTML routes and JSON health output; no complete public API platform has been established.

## Decision

Keep existing HTML routes and their endpoint behavior compatible. When a real external/client API consumer is introduced, create an explicit versioned API namespace (initially `/api/v1/`) with documented request/response schemas, consistent safe errors, authentication, authorization, validation, pagination, rate limits where needed and contract tests.

A client-supplied school identifier is never authorization. API operations use the same server-derived tenant context and permission rules as HTML workflows. Breaking contract changes require a new version and a documented consumer migration plan.

## Alternatives considered

- Convert existing HTML routes into JSON APIs: rejected because it breaks current browser workflows and mixes presentation with integration contracts.
- Add an API framework now without consumers: rejected as unnecessary tooling and maintenance.
- Expose database-shaped endpoints: rejected because internal schema changes should not automatically become public contract changes.

## Consequences

The approach preserves current user workflows and allows deliberate integration growth. It creates a responsibility to maintain versioned contracts and compatibility tests once external consumers depend on them.

## Compatibility and migration

No existing route is renamed or removed by this decision. API implementation must be additive and must reuse authentication, tenant and authorization policies. Document deprecation only when consumers and support capacity are known.

## Verification and acceptance

Test authentication, role authorization, school isolation, schema validation, pagination, error shape, idempotency for sensitive writes and compatibility across supported API versions. This ADR does not claim that a versioned API currently exists.
