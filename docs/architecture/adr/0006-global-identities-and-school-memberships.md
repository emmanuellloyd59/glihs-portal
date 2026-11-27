# ADR-0006: Global platform identities and school-scoped memberships

- **Status:** Implemented in feature branch; acceptance and production rollout pending
- **Date:** 2026-11-27
- **Decision owners:** Digital Education Ecosystem engineering
- **Related backlog:** IDN-001, TEN-001, SEC-001

## Context

The legacy portal stores one `school_id` and one `role` on each user row. This prevents one person from securely working across multiple schools and conflates platform identity with school authorization. Existing records, user IDs, password hashes, account statuses and route contracts must remain usable.

## Decision

1. A row in `users` represents the global platform identity and retains its stable primary key, credentials and account status.
2. `school_memberships` represents authorization within one school and optional campus, with a role and lifecycle status. A school identifier supplied by a client is never proof of membership.
3. A request's `current_user.school_id`, `current_user.campus_id` and `current_user.role` are resolved from the authenticated identity's selected, active membership. Existing route contracts continue to use these server-derived attributes.
4. Legacy non-platform users are backfilled from `users.school_id` and `users.role`. A compatibility bridge materializes memberships for legacy account-creation routes on first authenticated use. It does not rewrite credentials or account status.
5. Membership switching verifies ownership by the authenticated user, active membership state, active school and (when present) active campus. The session is cleared and rebound to the verified membership. Each request reloads and revalidates the membership.
6. Platform-owner accounts remain separate platform-level identities and must explicitly select a school using the existing platform workflow. A school membership is not a platform-owner grant.
7. Invitations use random, single-use bearer tokens stored only as SHA-256 hashes, expire after seven days, and are delivered only through configured SMTP. Parent/student invitations require an active linked student record. Acceptance requires the invited email account or creates a new account after email possession has been demonstrated through the invitation.
8. Recovery tokens are random, single-use, hashed and expire after 30 minutes. Password recovery increments `users.auth_version`, invalidating previously issued authenticated sessions on their next request.
9. Membership revocation is audited and takes effect on the next request. The last active school administrator cannot be revoked through this workflow.

## Data model

- `users`: global identity; existing `id`, `password_hash`, `status`, and legacy school/role columns retained; additive `email` and `auth_version`.
- `school_memberships`: identity, school, optional campus, role, status, primary indicator, invitation/acceptance metadata.
- `membership_invitations`: email, hashed token, target school/campus/role, optional linked student, expiry and acceptance state.
- `identity_recovery_tokens`: hashed, expiring, single-use password recovery.
- `identity_security_events`: membership and recovery audit trail.

## Security properties

- Client-supplied school IDs do not create access.
- A membership ID is usable only when it belongs to the authenticated user.
- Inactive, revoked, suspended or cross-school memberships cannot be selected.
- School and campus lifecycle are rechecked at authentication/context resolution.
- Session cookies remain HTTP-only, secure according to deployment configuration, SameSite=Lax, and short-lived; membership switching clears prior session state.
- Password recovery increments the account authentication version, invalidating older sessions.
- Existing CSRF protection continues to apply to state-changing browser/API requests.

## Compatibility and migration

The migration is additive and idempotent. It does not rebuild the users table or modify existing user IDs, password hashes, account status, or legacy school assignments. Existing school-scoped routes continue receiving a verified active school context. Legacy account creation continues to work through a first-authenticated-use membership bridge.

## Verification

Automated tests cover legacy credential preservation, multiple school memberships with different roles, cross-user membership identifier guessing, revoked membership selection, membership listing isolation, and independent sessions for the same identity. Full CI, browser review, invitation/recovery email delivery, and production migration rehearsal are required before acceptance.

## Limitations and follow-up

- SMTP delivery requires deployment configuration (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`); no secret or hosting setting is changed by this PR.
- Invitation and recovery landing endpoints expose the one-time token to the recipient and provide API instructions; a polished account-portal form and delivery monitoring remain follow-up work.
- Platform operator delegation beyond the existing platform-owner/super-admin role is not yet a separate least-privilege grant model.
- Full PostgreSQL compatibility/RLS and audit of every legacy route's use of the active campus remain outstanding.
- Existing student-account schema has a single `students.account_user_id`; cross-school student transfers or multiple concurrent student enrollments need a separate student-enrollment identity design.
- Recovery request throttling, notification outbox/retry, MFA, and device/session management UI remain future hardening items.
