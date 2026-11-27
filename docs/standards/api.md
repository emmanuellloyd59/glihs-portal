# API Standards

These standards apply when the repository exposes or expands APIs. They do not require creating an API layer before a consumer or integration needs one.

## Contract design

- Use resource-oriented nouns and predictable HTTP methods.
- Keep existing HTML routes stable. New external/client APIs should use an explicit prefix such as `/api/v1/`; do not silently convert existing page routes into APIs.
- Use JSON request and response bodies with documented field names, types, required/optional status and constraints.
- Use stable public identifiers. Do not expose internal database assumptions or rely on sequential IDs as authorization tokens.
- Return consistent errors with a machine-readable code, safe message and request/correlation identifier where available. Never return stack traces, SQL, file paths or secret values.
- Use appropriate HTTP status codes: 200/201 for successful reads/creation, 202 for accepted asynchronous work, 204 for successful no-content operations, 400 for malformed input, 401 for unauthenticated requests, 403 for forbidden actions, 404 where resource concealment is appropriate, 409 for conflicts, 422 for semantically invalid input, and 429 for rate limiting when implemented.
- Validate payload size, types, formats, enum values and ownership server-side.

## Authentication and authorization

- Reuse the application's established authenticated identity and authorization policy; do not invent a second authentication mechanism for each module.
- A submitted `school_id`, campus ID, role or owner ID is a selector/input, never proof of permission.
- Resolve school scope from authenticated server-side membership/context and validate every referenced record belongs to that scope.
- Apply authorization to list, detail, create, update, delete/archive, export, download and bulk endpoints.
- Protect sensitive endpoints against CSRF when cookie-based browser authentication is used. If token-based non-browser clients are introduced, document the threat model and token lifecycle.
- Apply least privilege, safe pagination limits, rate limits for abuse-prone operations and audit events for sensitive mutations.

## Compatibility and versioning

- Version externally consumed contracts under `/api/v1` when first introduced.
- Add optional fields compatibly; do not repurpose fields or change types in place.
- For breaking changes, introduce a new version, document a deprecation window and migration guide, and test both versions during transition where feasible.
- Do not promise a deprecation schedule until consumers and support capacity are known.
- Keep API schema documentation beside the implementation and include representative success/error examples.

## Operational requirements

- Use pagination for potentially large collections; cap page size.
- Define filtering/sorting allowlists rather than accepting arbitrary SQL field names.
- Make retryable mutation semantics explicit. Use idempotency keys for payment, enrollment, import or other operations where duplicate submission could cause harm.
- Set timeouts and payload limits at the application/hosting boundary where supported.
- Do not log passwords, tokens, student/guardian sensitive details or full payment credentials.
- Add contract tests for authorization, validation, pagination, compatibility and tenant isolation.
