# International school registration and onboarding

## Scope

This workflow extends the existing School Network and Flask-Login implementation. It does not replace the schools table, school IDs, users table, authentication/session system, or existing school lifecycle endpoints.

## Applicant journey

1. Open `/school-network/onboarding` and select a country.
2. Country profile and service catalogue are loaded from the platform API.
3. Save an incomplete draft. The applicant receives a private resume token in the response and a separate one-time email-verification token through the notification outbox.
4. Verify the applicant email using `POST /api/v1/onboarding/applications/{reference}/verify-email`.
5. Reopen or update a draft using its reference and `X-Onboarding-Token` header.
6. Submit only after the required fields and requested services validate. Submission creates a pending, inactive school record and requested-service records.
7. Platform staff review the school through the existing School Network lifecycle controls.
8. After the school is active, the platform owner can invite a school administrator. The invitation token is single-use, hashed at rest and expires after 72 hours.
9. The invited administrator sets a password and receives an account scoped to that school.
10. The active school administrator completes configuration after an active administrator and institutional contact exist.

## API

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | `/school-network/onboarding` | Public | International registration wizard |
| GET | `/api/v1/onboarding/countries` | Public | Active country profiles and service catalogue |
| POST | `/api/v1/onboarding/applications` | Public, rate limited | Create resumable draft |
| GET | `/api/v1/onboarding/applications/{reference}` | Resume token | Retrieve draft/status |
| PUT | `/api/v1/onboarding/applications/{reference}` | Resume token | Update draft |
| POST | `/api/v1/onboarding/applications/{reference}/verify-email` | Email verification token | Verify applicant email |
| POST | `/api/v1/onboarding/applications/{reference}/submit` | Resume token + verified email | Validate and create pending school |
| GET | `/api/v1/onboarding/applications` | Platform owner | Review queue |
| PUT | `/api/v1/onboarding/countries/{country_code}` | Platform owner | Configure country-specific requirements |
| GET | `/api/v1/onboarding/schools/{school_id}/progress` | Platform owner or scoped school admin | Read onboarding progress |
| POST | `/api/v1/onboarding/schools/{school_id}/invite-administrator` | Platform owner, active school only | Issue administrator invitation |
| GET | `/school-network/invitations/accept` | Public, invitation token in URL fragment | Password setup page |
| POST | `/api/v1/onboarding/invitations/accept` | Invitation token | Create school-scoped administrator account |
| POST | `/api/v1/onboarding/schools/{school_id}/configuration/complete` | Platform owner or school admin | Complete post-activation setup |
| POST | `/api/v1/onboarding/notifications/dispatch` | Platform owner | Dispatch queued email |

## Country configuration

Country profiles are records, not branches in application code. The country choice list is reference data; it does not imply legal requirements. Platform owners can set:
- country name and ISO alpha-2 code;
- administrative-region label and whether it is required;
- whether an institutional registration/license number is required;
- a constrained `requirements.required_fields` list for supported fields.

Supported configurable required fields are `registration_number`, `region`, `city`, `owner_name`, `contact_phone` and `website_url`. Unsupported field keys are ignored by validation rather than evaluated as arbitrary expressions or executable regular expressions. Country profile updates increment a version.

The wildcard profile supplies neutral defaults for other valid country codes. Country-specific rules must be configured by platform operations after confirming the applicable jurisdictional requirements.

## Data and security controls

- Draft applications are stored separately; no school row is created until verified submission.
- Resume and invitation tokens are cryptographically random; only SHA-256 token hashes are stored in their respective token columns.
- Email verification uses a separate one-time token hash; successful verification consumes it.
- Resume tokens are returned once to the applicant's browser and must be stored privately. They are never written to application audit events.
- Public draft creation is rate limited by an HMAC of source address and normalized email; a honeypot field provides a low-cost bot signal.
- Duplicate detection compares normalized institution name or registration number within the same country and returns a non-disclosing response.
- New schools remain `status='suspended'` and `lifecycle_status='pending'` until the existing platform review and activation process completes.
- Administrator invitations are allowed only for active schools, expire after 72 hours, and are single-use. Existing usernames are not silently overwritten.
- New administrator accounts use the existing users table, password hashing, role model and school_id tenant scope.
- Every major application/invitation/configuration event is recorded in `onboarding_events`; school creation is also covered by School Network audit.
- Email is placed in a database outbox. SMTP delivery is enabled only when `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` and optional `SMTP_USERNAME` / `SMTP_PASSWORD` are configured. Without SMTP, notifications remain queued; the system does not claim they were sent.
- SMTP dispatch is platform-owner-only, processes bounded batches and caps retries at five attempts.

## Operational configuration

Set SMTP values in the hosting provider's secret/environment settings; never commit credentials. Use TLS via STARTTLS. Monitor failed outbox rows and avoid exposing outbox contents through public APIs. A scheduled worker or an operator-triggered dispatch can call the platform-owner dispatch endpoint.

## Limitations and production gates

- This iteration uses the repository's current SQLite connection layer. PostgreSQL compatibility and RLS remain a separate approved workstream.
- Country profiles are configurable data, not a legal compliance engine. A qualified local review is required before requiring jurisdiction-specific documents or asserting regulatory compliance.
- The outbox dispatcher is synchronous and bounded; production scale should move delivery to a background worker with idempotent provider delivery keys.
- CAPTCHA/managed bot challenge, domain ownership verification, document upload/scanning, external registry verification, and formal ownership verification are not implemented here.
- Browser review, accessibility, SMTP integration testing with a real provider, security review and a production migration/restore rehearsal remain required.
- Do not merge, deploy, or migrate production data without the required review and explicit approval.


Implementation tracking: draft PR #54 is open for review. Automated verification is being run against the feature branch; no merge or deployment is implied by the PR.
