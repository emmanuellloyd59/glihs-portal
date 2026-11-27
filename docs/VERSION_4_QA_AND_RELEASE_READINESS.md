# CMHS Digital Campus — Version 4 QA and Release Readiness

**Assessment date:** 2026-11-26  
**Development branch:** `version-4/reconciled-main-2026-11-26`  
**Pull request:** #49 (draft)  
**Live service:** `https://glihs-portal-3.onrender.com`

## Executive result

Version 4 remains **not approved for production**. The consolidated branch has substantial automated regression coverage and new database, authorization and audit safeguards. The latest full regression run passed on Python 3.11 and 3.14, but authenticated end-to-end testing against a V4 staging deployment has not been performed.

The public Render service currently serves the earlier **Version 0.3 preview**, not the unmerged Version 4 branch. Browser observations below therefore describe the current live preview only, not the V4 candidate.

## Automated test inventory

The current branch contains **184 test methods** across the following suites:

| Suite | Test count | Primary coverage |
|---|---:|---|
| `test_access_control.py` | 48 | Direct-route authorization, teacher scope, cross-school access, account linking, content and homework ownership |
| `test_app.py` | 95 | Authentication, registration, student records, grades, attendance, finance, timetable, homework and audit rollback |
| `test_demo_staff.py` | 4 | Fictional staff import safety and idempotency |
| `test_demo_student_import.py` | 5 | Explicit confirmation, production refusal and duplicate protection |
| `test_migrations.py` | 3 | Legacy preservation, failed-migration rollback, repeatability and tenant integrity |
| `test_report_cards.py` | 13 | Publication, term rules and teacher/student restrictions |
| `test_sqlite_backup.py` | 7 | Backup integrity, foreign-key checks, restore and overwrite protection |
| `test_student_finances.py` | 3 | Finance access, balance and overpayment |
| `test_student_profiles.py` | 4 | Student privacy and profile editing |
| `test_stylesheet_cascade.py` | 2 | CSS order and accessible page landmarks |

The CI workflow is configured for Python 3.11 and 3.14. The previous 176-test baseline passed on both versions in GitHub Actions run [#45913387703](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45913387703). The current 184-test candidate adds route-inventory enforcement, unauthenticated-route coverage, specialist-role boundaries, registrar homework denial, teacher report-card subject isolation, cross-school content/homework object tests, and dashboard/export regressions. The latest candidate workflow is pending; do not count the new tests as passed until both matrix jobs finish successfully.

## Browser compatibility observations — live Version 0.3 preview only

Visual inspection was performed without signing in, submitting forms or changing live data.

### Desktop — 1440 × 1000
- Login page loaded after the Render service woke.
- Navigation links were visible without the mobile toggle.
- Login form was centered; observed width approximately 395 px.
- Username, password and Sign In controls were visible and not clipped.
- Document scroll width was 1425 px, below the 1440 px viewport; no horizontal overflow was observed.
- No visible console errors were reported by the browser session.

### Mobile — 390 × 844
- Navigation collapsed into a toggle and expanded successfully when selected.
- Login form and both credential fields remained visible and usable.
- Development-preview banner wrapped without clipping.
- Footer remained visible when scrolling.
- Document scroll width was 375 px against a 390 px viewport; no horizontal overflow was observed.

Additional public-route smoke checks confirmed that the home page, Features page and linked Request Staff Access page (`/admin/request-access`) loaded. The access form's visible controls were inspected but never submitted. These checks cover only public pages on the live Version 0.3 preview. They do not verify authenticated role dashboards, student registration, finance, reports, homework or other staff workflows on the V4 branch.

## Accessibility finding

Axe-core audits of the live Version 0.3 login, Features and Request Staff Access pages each found one moderate `region` violation: the `.release-notice` content was outside a landmark.

The V4 branch changes that notice to a top-level `header` banner landmark and adds a template regression test asserting the banner, navigation, main and footer landmarks. This code has not been deployed, so the live service may continue to show the original issue until a separately approved release. Re-run axe on the deployed V4 build before release.

## Defect and release-blocker register

| ID | Severity | Finding | Current disposition |
|---|---|---|---|
| QA-01 | Critical | Render service has no configured persistent disk or connected PostgreSQL database; SQLite durability across service replacement/redeploy remains unproven | Open — production release blocked |
| QA-02 | Critical | Scheduled, encrypted, off-host backup retention and operational restore drills are not configured | Open — helper and automated tests only |
| QA-03 | High | Complete route-by-route authorization and cross-school review, including counselor and full parent/student workflows | Source audit and 30-route inventory completed; expanded regression tests committed; latest CI verification pending |
| QA-04 | High | Authenticated desktop/mobile browser testing of the V4 candidate has not been run against a staging deployment | Open — live public login page only was inspected |
| QA-05 | High | Latest full regression run for current candidate | Previous 176-test baseline passed; 184-test candidate pending on Python 3.11 and 3.14 |
| QA-06 | Medium | Historical schema upgrades remain consolidated in the startup migration function; ledger milestones do not yet represent one migration file per historical change | Documented limitation |
| QA-07 | Moderate | Live Version 0.3 release notice is outside an accessibility landmark | V4 branch fix and regression test added; live service not changed |

## Database recovery status

The existing `scripts/sqlite_backup.py` utility now verifies both `PRAGMA integrity_check` and `PRAGMA foreign_key_check` for copied databases. Automated tests cover backup creation, restore to a new destination, corrupted-source rejection, foreign-key corruption rejection and refusal to overwrite by default.

This is not yet a production disaster-recovery service. Scheduling, encrypted backup storage, off-host copies, retention policy, restore-point objectives and recurring restore drills remain unimplemented.

## Security and data-protection status

- Teacher direct-route restrictions are tested for finance access, official student record edits and announcement publishing/archiving.
- School ownership checks now cover core student, parent, finance, academic, report-card, timetable, learning-resource, announcement and homework relationships.
- Academic, student, finance, timetable and content mutations write audit events in the same database transaction.
- Homework mutations and audit events are also committed atomically.
- Learning-resource uploads remove the stored file if the metadata/audit transaction fails.
- No production data reset, import, account change, deployment or infrastructure change was performed.

## Release decision

**Status: BLOCKED — do not merge or deploy yet.**

Required before production approval:
1. Both current Python 3.11 and 3.14 CI jobs pass (latest run passed; repeat after any code changes).
2. Complete route-by-route authorization review and tests for all roles and cross-school object access (source audit and expanded tests committed; verify final CI).
3. A staging environment with durable database storage is configured and verified.
4. Authenticated desktop/mobile end-to-end testing is completed using fictional test accounts and records.
5. Backup encryption, off-host retention, restore rehearsal and rollback procedures are demonstrated.
6. Accessibility audit is rerun against the deployed V4 candidate, including the corrected banner landmark.
7. Monitoring, deployment rollback and incident response checks are documented.
8. The project owner explicitly approves merge and deployment.

Passing automated tests alone is not sufficient to release a school information system containing sensitive student and financial data.
