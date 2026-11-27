# Authorization and Permission Model

## Purpose

The Digital Education Ecosystem uses one global platform identity with school memberships and a separate scoped authorization engine. Authentication proves who the user is; authorization decides what that identity may do, for which school/campus, and against which resource.

All decisions are enforced in Python backend code and service entry points. Template visibility is presentation only and is never an access-control boundary.

## Scope hierarchy and inheritance

The scope hierarchy is:

1. **Platform** — global platform operations and policy governance.
2. **Organization** — a school group and its member institutions.
3. **School** — one institution's records, staff, finance and academic operations.
4. **Campus** — a campus-specific subset of one school.

An authorization evaluation builds the applicable scope chain from the target school and campus. An organization scope is resolved through `school_group_memberships`; school and campus IDs are verified against their parent relationships. An allow at an ancestor scope is inherited by its descendant scopes. A more specific explicit deny overrides every inherited allow. A platform permission cannot be granted at a lower scope. Organization-level grants are controlled by platform policy administrators.

A user must have a valid active school membership for tenant operations. A school ID supplied by a client never creates membership or authority. Campus memberships cannot be used against another campus. Platform administrators must use the existing explicit school-selection workflow before acting on tenant-scoped operations.

## RBAC and ABAC

- **RBAC:** configurable role-permission baselines, inherited role capabilities and scoped user/role grants.
- **ABAC:** authenticated account status, active membership, school lifecycle, campus assignment, resource school/campus, resource owner, assigned user and optional grant conditions.
- **Default deny:** unknown permissions, missing identity, missing membership, inactive memberships, suspended institutions, invalid scopes and unmatched policies deny access.
- **Explicit deny:** a matching deny from any applicable role, user or scope takes precedence over an allow.
- **Least privilege:** no wildcard permission is assigned to ordinary school roles; each role receives only its defined permission set.
- **Delegation attenuation:** school managers may delegate only permissions they themselves hold. Platform/organization administration and high-impact membership, role, configuration and campus administration permissions cannot be delegated through ordinary school grants.

## Supported roles and baseline intent

| Role | Baseline access |
|---|---|
| Platform administrator (`super_admin`) | Platform school lifecycle, global policies, operator administration and all explicitly selected tenant operations |
| School owner | Full administration of the owned school, including configuration, staff, academic and finance workflows |
| School administrator | School profile/configuration, campuses, memberships, staff, academic and finance operations |
| Principal | School leadership, staff, academic oversight, learning and approved finance operations |
| Vice principal | Delegated academic, attendance and assignment operations; no automatic school-owner or platform authority |
| Registrar | Student/academic records, attendance and results administration |
| Teacher | Assigned academic/attendance work, learning materials and homework; assignment/class checks remain enforced |
| Finance officer | Finance viewing, fee administration, payment recording and permitted exports; no refund by default |
| Student | Own student profile, own academic/learning access and assignment submission |
| Parent | Linked-child academic, communication and child-finance information only |
| External service provider | Explicitly assigned campus service permissions only |

Additional operational roles such as admissions officer, academic administrator, attendance officer, librarian and counselor remain supported with narrow baselines.

The school owner, finance officer and external service provider are membership roles. The legacy `users.role` CHECK constraint remains unchanged: new memberships map those roles to a compatible legacy account role while the authenticated school role is always resolved from the active membership. Existing user IDs, password hashes and account status are preserved.

## Permission catalog

The catalog is defined centrally in `authorization.py` and grouped by platform, organization, school and campus scope. It includes school profile/configuration/campus/membership/staff permissions; academic records, attendance, results and class permissions; finance view, fee, payment, refund and export permissions; learning/course/material/assignment permissions; student/parent self-service permissions; communications; audit access; and platform/organization governance.

Unknown permission strings are rejected. New permissions must be registered in the catalog and assigned explicitly to roles or scoped grants.

## Configuration and APIs

- `GET /api/v1/authorization/me` — effective permissions for the current authenticated school/campus context.
- `GET /api/v1/authorization/roles/{role}/permissions` — platform-policy administrator view of a role's configured permission set.
- `PUT /api/v1/authorization/roles/{role}/permissions` — platform-policy administrator updates a role baseline (allow or deny).
- `POST /api/v1/authorization/grants` — create an expiring or conditional user/role grant at an authorized scope.
- `DELETE /api/v1/authorization/grants/{grant_id}` — revoke a grant; revocation is audited.
- `GET /api/v1/authorization/audit` — view authorization decisions and policy changes within the caller's permitted scope.

Role baseline configuration is platform-governed. School administrators may create attenuated school/campus user or role grants only inside their authorized school and within their own effective permissions. Organization grants require platform governance and a valid school-group record.

## Data model

- `authorization_role_permissions`: configurable role/permission/scope/effect baseline.
- `authorization_grants`: user or role grants, scope, allow/deny effect, JSON ABAC conditions, expiry and revocation metadata.
- `authorization_audit`: allow/deny decisions, role policy changes, grant creation and revocation.
- Existing `school_memberships`, `school_group_memberships`, `schools` and `campuses` remain the sources of tenant and campus relationships.

Schema creation and baseline seeding are additive and idempotent. No production data is rewritten.

## Backend enforcement

The central `has_permission` and `require_permission` services are integrated into school network and campus APIs, School Configuration Centre reads/writes, membership invitation/revocation, staff management, attendance reporting, onboarding administration, finance payments, parent-linked children, learning materials, homework assignments, announcements and school performance views.

Existing SQL predicates remain in place as a second boundary: school-owned rows are queried with school IDs, student/parent links are verified, and teacher work is constrained by active class/subject assignments. Permission checks do not replace resource ownership or domain validation.

## Audit and operations

Every `require_permission` decision records actor, school/campus, permission, allow/deny result and bounded request attributes. Role policy and grant mutations append audit records. Grant revocation is soft-revocation; audit records are retained. No access tokens, passwords or invitation secrets are written to authorization audit rows.

## Security tests

`tests/test_authorization.py` covers least-privilege role defaults, horizontal cross-tenant access, vertical staff-administration escalation, explicit-deny precedence, inactive-membership revocation, unauthorized role-policy changes, cross-school grant attempts, protected administrative permission delegation, campus-bound provider access, organization-to-school inheritance and authorization audit records.

## Remaining security gates

- Run the complete Python test matrix and review all failures.
- Audit every legacy route and service not yet migrated to named permissions, especially grade/report-card, attendance, fee/finance, exports, file downloads and platform operator workflows.
- Add browser-level role walkthroughs and accessibility review.
- Add production migration rehearsal, backups/restore validation and PostgreSQL row-level security before production use.
- Add MFA, rate-limited policy mutation, device/session controls and operational alerting as subsequent security hardening.

This implementation is a draft feature branch, not a production authorization certification.
