# Feature Acceptance, Release and Rollback Standards

## Feature acceptance record

Every backlog item must define:
- user/business outcome and scope;
- target module and data ownership (platform, school, campus or shared);
- related ADRs and dependencies;
- roles and authorization matrix;
- route/API and compatibility impact;
- schema/data migration impact;
- positive, negative, duplicate and cross-school cases;
- changed files and tests;
- CI evidence and manual verification where needed;
- operational, privacy and accessibility considerations;
- rollback/recovery approach.

A feature may be marked **implemented** when code exists, **verified** when automated/manual evidence passes, and **accepted** only when all stated acceptance criteria and required approvals are satisfied. Do not label a planned or partially tested feature complete.

## Definition of done

- Acceptance criteria are testable and all pass.
- Existing workflows, routes, roles and records remain compatible or an approved migration plan exists.
- Focused tests and full regression suite pass.
- School-owned data access is proven isolated.
- Database migration and recovery requirements are satisfied where applicable.
- Documentation, limitations, permissions and support notes are updated.
- CI result and commit SHA are linked in the backlog/PR.
- No unresolved critical security or data-integrity defect remains.
- Required review and project-owner approvals are recorded.

## Branch and pull request policy

- Work in a focused branch; do not commit feature work directly to the deployment branch.
- Use branch prefixes from the coding standards.
- Keep pull requests small enough to review and tie them to backlog IDs.
- CI must pass on the PR before merge.
- PRs changing authentication, tenant isolation, migrations, finance, exports or sensitive data require explicit security/data review.
- A green CI run does not grant merge or deployment authorization.
- The repository's existing workflow is the baseline; do not add mandatory checks that are not configured and tested.

## Release procedure

1. Confirm release scope and accepted backlog items.
2. Review all open PRs, dependency changes and migration scripts.
3. Verify supported Python/runtime configuration and secrets.
4. Run the full regression suite and review CI for the exact release commit.
5. Complete security, tenant-isolation and migration checks.
6. Create and verify a backup; rehearse restoration against a disposable environment.
7. Perform desktop/mobile browser, print/PDF and accessibility checks for affected workflows.
8. Deploy to staging with fictional/test data; verify health, persistence, logs and monitoring.
9. Obtain explicit release/deployment approval.
10. Deploy using the existing hosting workflow; verify health and key user journeys.
11. Record release commit, migration versions, CI run, operator, approval and outcome.

## Rollback procedure

Before release, document:
- last known good commit/deployment;
- backup identifier and restore verification;
- whether schema changes are backward-compatible;
- how to disable the feature or revert application code;
- how to reconcile writes created after deployment;
- expected downtime and responsible approver.

If a release fails: stop further changes, preserve logs safely, assess data integrity, roll back application code when schema compatibility permits, otherwise use the approved forward-recovery/restore plan. Never restore or overwrite production data without explicit authorization. Verify health and key workflows after recovery and document the incident.

## Current repository-specific constraint

The README and Version 0.3 release plan explicitly describe the application as a development preview, prohibit real student data, and require further tenant audit, browser verification, backup/restore and staging gates. These existing restrictions remain in force; this governance document does not change the product's release status.
