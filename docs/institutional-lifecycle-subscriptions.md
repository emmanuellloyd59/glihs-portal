# Institutional lifecycle and subscription foundation

## Boundaries and safety

This is an additive foundation. It does not enroll existing schools, change legacy
`schools.status` or `schools.lifecycle_status`, collect money, contact a payment
provider, or automatically suspend an institution. Startup creates billing tables
only. Institutions without an explicit lifecycle row remain `legacy_unmetered`
and keep their existing access.

Institutional lifecycle and subscription status are separate state machines.
Only a platform-authorized, reasoned lifecycle operation changes the institutional
state. An overdue invoice never writes a suspension or archive state. Records,
student data, finance history, files and audit history are retained in every state.

## Institutional states

| State | Meaning | Access policy |
|---|---|---|
| trial | Approved evaluation period | Plan entitlements until trial end; expiry requires explicit review |
| active | Institution operating normally | Plan entitlements |
| restricted | Billing or policy restriction | Read-only access; exports and writes can be separately gated |
| suspended | Platform-enforced temporary stop | Essential account, billing and support access only |
| cancelled | Service cancelled, retention period applies | Read-only/export and billing history |
| archived | Institution formally archived | No normal application access; records retained under retention policy |

The current access resolver is deliberately non-mutating. A past-due subscription
uses its recorded grace deadline: before it, normal plan access remains; after it,
the resolver returns read-only. A platform owner must explicitly change institutional
state to suspend or archive. Production middleware must call this resolver at each
module/API boundary before enforcing entitlements; this PR exposes the policy API
but does not yet wrap every legacy route.

## Plans, entitlements and usage

Plans are versionable business configuration identified by a stable code. Each plan
contains billing period (monthly, quarterly, annually or custom), integer minor-unit
price, ISO 4217 currency, trial/grace days, service entitlement map and usage limits.
Entitlements are keys such as `school_website`, `school_erp`, `learning_platform`,
`finance`, `communications`, `analytics_ai`, `marketplace`. Limits are named
non-negative quotas (campuses, active students, staff seats, storage bytes, monthly
messages, API requests). Enforcement should fail closed for a disabled entitlement;
quota counters/reservations are a follow-on before commercial launch.

## Provider-neutral billing

The core stores provider key and opaque provider references only. Provider adapters
must translate provider events into validated internal operations; provider SDKs,
webhook secrets, payment tokens and provider-specific status values do not belong
in core tables. No provider adapter or payment collection is enabled here.

Every write operation must require a caller-supplied idempotency key and persist a
canonical request hash and result in the same transaction. Reusing a key with a
different payload returns conflict. Provider events use unique (provider_key,
event_ref) identity and payload hashes; verify signatures in the adapter before
recording. Duplicate delivery must return the recorded outcome without replaying
state changes. Invoice/payment reconciliation is ledger-first; never infer payment
from a browser redirect.

## Roles and APIs

Platform administrators with `platform.manage_schools` can manage plans, assign
subscriptions, inspect billing state and change institutional lifecycle with a
mandatory reason. School users with `school.view_dashboard` can inspect their own
subscription only; tenant IDs are checked by the authorization engine.

- `GET /api/v1/platform/subscription-plans`
- `POST /api/v1/platform/subscription-plans`
- `POST /api/v1/platform/schools/{school_id}/subscription` (Idempotency-Key required)
- `GET /api/v1/platform/schools/{school_id}/subscription`
- `GET /api/v1/schools/{school_id}/subscription`
- `GET /api/v1/schools/{school_id}/subscription/access`
- `POST /api/v1/platform/schools/{school_id}/lifecycle` (reason required)

## Recovery and operational controls

1. Take and verify a database backup before a billing correction or schema rollout.
2. Reconcile provider event IDs, invoice IDs, amounts and currency against the immutable
   internal operation record; never replay an event with a changed payload.
3. Correct a failed operation through a compensating, audited operation; do not edit
   ledger history in place.
4. Restore from backup only after a point-in-time impact review; preserve audit and
   provider references, then reconcile events received after the backup.
5. Re-enable access only through an authorized, reasoned lifecycle/subscription
   transition after verifying payment state and school ownership.

## Explicit non-goals / launch gates

No payment provider integration, payment collection, automated invoice issuance,
tax/VAT calculation, dunning email, automated suspension, quota metering, production
data migration, or production state change is enabled. Before launch: enforce
entitlements across every service route, implement quota reservation/rollback,
add provider adapter signature verification and reconciliation, test PostgreSQL,
test backup/restore, define country-specific taxes/retention, and conduct a
platform-owner and school-admin browser review.
