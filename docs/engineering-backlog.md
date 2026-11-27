# Engineering Implementation Backlog

This is the traceability index for architecture and governance work. Status reflects repository evidence, not aspiration. Update each item in the same PR that changes its implementation or verification state.

## Status vocabulary

- **Planned:** scoped, not started.
- **In progress:** implementation underway.
- **Implemented:** code/documentation exists.
- **Verified:** required automated/manual evidence recorded.
- **Accepted:** acceptance criteria and required approvals complete.
- **Blocked:** dependency or risk prevents safe progress.

## Governance and architecture backlog

| ID | Capability | Work item | ADR | Implementation / documents | Required tests or evidence | Acceptance criteria | Status |
|---|---|---|---|---|---|---|---|
| GOV-001 | Governance | Establish governance index, contributor workflow and standards | ADR-0001 | `CONTRIBUTING.md`, `docs/engineering-governance.md`, `docs/standards/*` | Review links, verify commands and CI descriptions against repo | Standards reflect current Flask/SQLite/unittest/GitHub Actions workflow; no unsupported tooling mandated | Implemented; PR verification pending |
| ARC-001 | Architecture | Retain Flask modular monolith and define module dependency rules | ADR-0001 | `docs/architecture/adr/0001-*.md`, coding standards | Future import-boundary and route-compatibility tests | No new circular imports, global namespace injection or cross-module private-table access | Accepted direction; implementation pending |
| TEN-001 | School Network | Enforce school tenant boundary across all reads/writes/joins/exports | ADR-0002 | Existing `app.py`, tenant/security docs; future `core/tenant_context.py` | Two-school positive/negative tests for every resource and mutation | School A cannot read/change School B; missing scope fails closed | In progress; not production-approved |
| API-001 | Platform | Establish versioned API contracts when an external API is introduced | ADR-0003 | `docs/standards/api.md` | Contract, auth, validation, pagination and tenant tests | Existing HTML routes remain compatible; new external APIs documented/versioned | Standard defined; API implementation pending |
| DB-001 | Platform | Establish safe migration/recovery governance | — | `docs/standards/database-migrations.md` | Legacy fixtures, idempotence, FK/data reconciliation and restore rehearsal | No destructive production migration without backup, rehearsal and approval | Standard defined; process adoption pending |
| SEC-001 | Platform | Maintain deny-by-default security and tenant regression gates | ADR-0002 | `docs/standards/security-and-testing.md` | Existing `tests/test_access_control.py`; expand by feature | Every sensitive route/action has positive and negative authorization tests | In progress |
| ARC-002 | Application | Extract explicit application factory and dependency registration | ADR-0001 | Future `application/`; current `app.py` compatibility entry point | Startup, route map, login/session, full suite | Existing app startup and routes behave identically; no globals-based registration | Planned |
| ARC-003 | Intelligence Centre | Extract school-performance route/service/repository as first bounded capability | ADR-0001 | Current `school_performance.py`; future `modules/intelligence_centre/` | Dashboard roles, tenant scope, aggregates, route compatibility | Existing `/school-performance` preserved; no cross-school aggregate leakage | Planned |
| LRN-001 | Learning Platform | Extract homework and learning-material workflows incrementally | ADR-0001, ADR-0002 | `homework.py`, `app.py`; future `modules/learning_platform/` | Teacher assignment, student submission, grading, file access, tenant tests | Existing routes/workflows preserved; school/teacher scope enforced | Planned |
| ERP-001 | School ERP | Separate student, staff, academic, timetable and attendance capabilities | ADR-0001, ADR-0002 | Current `app.py`, `staff_attendance.py`; future `modules/school_erp/` | Existing direct-route authorization and two-school suite | No role escalation or tenant leakage; existing records/IDs preserved | Planned |
| FIN-001 | Education Finance | Isolate finance operations and strengthen transaction/audit boundaries | ADR-0001, ADR-0002 | Current `app.py`; future `modules/education_finance/` | Amount validation, permissions, reconciliation, duplicate and rollback tests | Historical aggregates preserved; no fabricated payment history; mutations audited | Planned |
| COM-001 | Communication | Extract announcements and add reusable notification interfaces when needed | ADR-0001, ADR-0002 | Current `app.py`; future `modules/communication/` | Publisher permissions, tenant scope, delivery/retry tests when channels exist | No cross-school messages; delivery status and privacy are explicit | Planned |
| NET-001 | School Network | Implement institution registration, unique identity, profiles, classifications, ownership, locations, contacts, school groups and campuses | ADR-0002, ADR-0004 | `school_network.py`, `templates/school_network/*`, `docs/school-network-api.md`, additive schools metadata and network tables | Registration validation, duplicate identity, lifecycle transitions, group membership, campus ownership, school-admin vs platform-owner tests | CMHS record/ID preserved; new schools remain inactive pending platform approval; school groups do not merge tenant boundaries | In progress; CI verified on feature branch; manual review pending |
| NET-002 | School Network | Complete school lifecycle, domain verification and membership boundaries | ADR-0002, ADR-0004 | Existing schools/users schema; School Network module | Lifecycle and school/campus boundary tests; domain verification and legacy route audit | School owns campus and records; platform administration remains distinct | Planned |
| NET-003 | School Network | International institution application, country requirements, verification, invitations and onboarding configuration | ADR-0002, ADR-0004 | `school_onboarding.py`, `templates/school_network/onboarding.html`, `docs/international-school-onboarding.md` | Draft/resume, email verification, dynamic country validation, duplicate, abuse, invitation replay/expiry, notification outbox, tenant scope and lifecycle tests | Incomplete applications resume securely; country rules are data-configurable; only verified submissions create pending schools; only active schools receive administrator invitations | Implemented; CI and manual review pending |
| WEB-001 | School Websites | Build school website management as a bounded capability | ADR-0001, ADR-0002 | Future `modules/school_websites/` | Domain resolution, tenant content and public/private access tests | Each school manages its own website/content without cross-tenant exposure | Planned |
| MKT-001 | Education Marketplace | Define marketplace provider/listing/purchase boundary | Future ADR | Future `modules/education_marketplace/` | Ownership, moderation, payment and provider authorization tests | Marketplace cannot bypass school finance or tenant policies | Planned |

## Update protocol

For each item, add:
- PR URL and commit SHA;
- changed file paths;
- migration identifier(s), if any;
- focused test command and result;
- full CI run URL/result and Python matrix;
- manual browser/operational evidence;
- acceptance checklist and remaining limitations;
- approval/release reference when required.

Do not mark an item verified based only on code presence, test names, or a previous CI run. Evidence must match the current commit.


## NET-001 verification record

- Implementation PR: https://github.com/emmanuellloyd59/glihs-portal/pull/53 (draft, stacked on the engineering-governance branch).
- Tested code commit: 3ec74b656c5302dbafa3097faa41c8deaa12f089.
- GitHub Actions run #840: https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45944332220 — 158 tests passed on Python 3.11 and 158 passed on Python 3.14.
- Manual browser/accessibility review, full legacy-route tenant audit, domain verification and production migration rehearsal remain pending. NET-001 is not yet marked accepted or production-ready.


## NET-003 implementation record

- Implementation branch: `feature/international-school-onboarding` (stacked on `feature/school-network-foundation`).
- Added `school_onboarding.py` with additive onboarding schema, configurable country profiles, service catalogue, resumable drafts, separate email verification, duplicate detection, IP/email rate limits, audit events, administrator invitations, live progress/configuration APIs and a bounded SMTP outbox.
- Registered onboarding with the existing Flask app and School Network / users authentication infrastructure.
- Added `/school-network/onboarding`, secure `/school-network/invitations/accept`, and `docs/international-school-onboarding.md`.
- Automated coverage: `tests/test_international_onboarding.py` (11 cases). Current-commit CI status is not exposed by the connected GitHub status endpoints; execution remains unverified and must be checked in GitHub Actions before acceptance.
- No merge, deployment, production migration, or production data changes.

- Latest code commit: `2ce23db3202cf72399adf7e483f3ccb09864787f`.
