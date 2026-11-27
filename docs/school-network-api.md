# School Network API and administration

The initial School Network API is additive and versioned under /api/v1. Browser registration and administration pages use the same validation and lifecycle rules.

## Institution registration

- GET /school-network/register — public registration form.
- POST /school-network/register — submit institution registration using a CSRF-protected browser form.
- POST /api/v1/schools — submit a JSON registration request; returns 202 with pending status.
- GET /api/v1/schools — platform owner receives the platform list; a school administrator receives only their own active school.
- GET /api/v1/schools/{school_id} — platform owner or authorized administrator of that school.
- PATCH /api/v1/schools/{school_id} — update profile fields within the caller's authorized school scope.

Required registration fields include institution name, identity slug (or a generated slug), classification, ownership classification, responsible owner/authority, city, country code and at least one institutional contact method. Slugs are globally unique. Email, URL, field lengths and classification values are validated. Group-affiliated registration requires an existing active school group.

## Lifecycle administration

- POST /api/v1/schools/{school_id}/review — platform-owner-only lifecycle transition with JSON action.
- GET /platform/schools — platform-owner institution review and management interface.
- POST /platform/schools/{school_id}/review — CSRF-protected platform UI action.

Allowed transitions:
- pending → under_review
- pending or under_review → approved
- pending or under_review → rejected
- approved → active
- active → suspended
- suspended → active
- rejected or suspended → archived

New registrations are persisted with legacy-compatible schools.status='suspended' and lifecycle pending. Approval does not activate access. Activation updates both fields. Suspension immediately makes legacy school-context checks fail closed.

## School groups, campuses and contacts

- POST /api/v1/school-groups — platform owner creates a group.
- GET /api/v1/school-groups — platform owner lists groups.
- GET /api/v1/schools/{school_id}/campuses — list campuses within one school.
- POST /api/v1/schools/{school_id}/campuses — create a campus within one school.
- PATCH /api/v1/schools/{school_id}/campuses/{campus_id} — update a campus only when both IDs belong together.
- POST /api/v1/schools/{school_id}/contacts — add an institution or campus contact.
- GET /school-network/profile — school administrator's own profile and campus workspace; platform owners may supply an explicit school_id.

A group is not a tenant. Each school remains the owner of its own records and campuses. A campus foreign key is paired with its school ID to prevent cross-school campus references. Each school may have one main campus.

## Authorization and errors

- API endpoints require authentication except the public institution registration endpoint.
- Platform-owner checks require the existing role, platform flag and null school assignment together.
- School administrators and principals are resolved from the authenticated user's existing users.school_id; client-supplied school IDs never grant access.
- School-scoped operations require an active school and active lifecycle.
- Cross-school resource access is concealed with 404; forbidden role or lifecycle operations return 403; invalid payloads return 400 or 422; invalid lifecycle transitions return 409.
- Cookie-authenticated writes remain under the application's Flask-WTF CSRF protection.

## Migration and limitations

The migration adds profile/lifecycle columns to the existing schools table and creates groups, group memberships, campuses, contacts and network audit tables. It does not replace school IDs, rename the legacy CMHS slug, rewrite users, or move student/academic/finance records.

This foundation does not yet implement verified custom domains, a new global identity/membership model, full route-by-route isolation across every legacy feature, PostgreSQL row-level security, automated tenant provisioning, or production data migration. The existing development-preview and fictional-data restrictions remain in force.
