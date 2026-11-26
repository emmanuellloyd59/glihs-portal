# CMHS Digital Campus — Version 4 Reconciliation Report

**Audit date:** 2026-11-26  
**Baseline:** `main` at `50f6500a07e3ba1ee96da6a29761e6f4b0874c14` (Version 3 merge, PR #48)  
**Consolidated development branch:** `version-4/reconciled-main-2026-11-26`  
**Draft pull request:** #49

## Executive finding

PR #49 is the safer consolidated Version 4 track because it is based on the latest verified Version 3 baseline. PR #47 is an older draft based on `0ebcf6ef5469fdf54530e704d449a6087088ee30`, before the Version 3 integration. GitHub reports PR #47 as not mergeable and its head and PR #49's head have diverged. PR #47 must not be merged wholesale or used as the release base.

The consolidated branch retains the current Version 3 teacher-assignment and school-scope architecture while carrying forward the valuable Version 4 finance, account-linking, login-protection, branding and responsive-interface work. The branch also adds a more accurate Feature Center and a payment/audit atomicity regression test.

## Reconciliation decisions

| Area | Decision |
|---|---|
| Version 3 baseline | Preserve the latest `main` baseline and all integrated Version 3 capabilities. |
| Teacher permissions | Keep the newer least-privilege rule: teachers work within assigned academic scope and cannot create official student records. The older PR #47 test allowing teacher registration is not carried forward because it conflicts with the stated teacher-access requirement. |
| Teacher assignments | Preserve the Version 3 assignment model and its tests; do not replace it with the older branch's timetable-coupled assumptions. |
| Finance | Retain Decimal/cents validation, serialized SQLite writes, overpayment protection, ledger reconciliation and immutable payment history safeguards. |
| Student/parent account linking | Retain atomic student-account linking and protection against role changes that bypass verified student/guardian relationships. |
| Login | Retain brief escalating failed-login cooldowns and successful-login cleanup; this remains a mitigation, not a complete anti-abuse system. |
| UI and branding | Retain CMHS Digital Campus Version 4 development branding and responsive registration/finance work. Replace the stale Feature Center copy with an accurate development overview and fictional-data warning. |
| Audit trail | Payment, balance and school-scoped audit writes are committed in one transaction. A failure-injection test checks rollback. |
| Data and deployment | No production data, Render settings, live deployment or `main` branch changes are part of this reconciliation. |

## Verification evidence

- The consolidated branch is based on the current Version 3 merge commit, not PR #47's older base.
- PR #49 is open as a draft and GitHub currently reports it mergeable.
- Regression CI for commit `3b3f5c8b43c54425a72bcaf2cf11d26cc4464138` completed successfully on Python 3.11 and Python 3.14.
- The suite includes finance receipt/balance checks, school-scoped audit checks, invalid amount and overpayment checks, teacher authorization boundaries, student/parent linking protections and Feature Center accuracy.
- The new failure-injection test forces the payment audit insert to fail and asserts that the payment ledger and student balance remain unchanged.

CI evidence: [latest workflow run](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45904634982).

## Remaining release blockers

Passing automated tests do not complete the release gate. The following remain unverified or require a separately approved operational decision:

1. Durable production database storage. The currently inspected Render service has no persistent disk or connected PostgreSQL database; SQLite persistence is therefore a release risk.
2. Verified backup schedule and a successful restore rehearsal in an isolated environment.
3. End-to-end browser testing on desktop and mobile for every role and core workflow.
4. A complete route-by-route authorization and cross-school isolation review, including direct URLs, exports and file downloads.
5. Staging persistence checks, operational monitoring, deployment verification and rollback rehearsal.
6. Independent review of financial correction, refund, reversal and reconciliation workflows before describing the module as accounting-grade.

## Version 4 implementation checklist

- [x] Rebase the development effort on the latest verified Version 3 baseline.
- [x] Keep a single consolidated Version 4 branch and draft PR.
- [x] Preserve the Version 3 teacher-assignment and least-privilege direction.
- [x] Carry forward finance validation and payment-ledger protections.
- [x] Make payment, balance and audit writes atomic.
- [x] Add a failure-injection regression test for audit-write rollback.
- [x] Correct stale Feature Center descriptions and warn against real student data during development.
- [x] Run the automated regression suite on Python 3.11 and 3.14.
- [ ] Complete route-by-route authorization and cross-school security review.
- [ ] Complete desktop/mobile browser QA.
- [ ] Configure and verify durable storage through an approved hosting decision.
- [ ] Complete backup and restore rehearsal.
- [ ] Complete staging, monitoring and rollback verification.
- [ ] Obtain explicit owner approval before merging or deploying.

## Release recommendation

**Version 4 remains a draft and is not approved for production.** Continue on PR #49. Do not merge PR #47, do not merge PR #49, and do not deploy until the remaining release blockers have evidence and the project owner explicitly approves the consequential release actions.
