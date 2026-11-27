# Contributing to CMHS Digital Campus

This repository evolves an existing Flask application into the Digital Education Ecosystem. Contributions must extend the existing application safely; do not restart, replace, or silently re-platform it.

Read these documents before changing application behavior:

- [Engineering governance](docs/engineering-governance.md)
- [Architecture decisions](docs/architecture/README.md)
- [Coding and repository standards](docs/standards/coding-and-repository.md)
- [API standards](docs/standards/api.md)
- [Database migration standards](docs/standards/database-migrations.md)
- [Security and testing standards](docs/standards/security-and-testing.md)
- [Feature acceptance and release standards](docs/standards/acceptance-and-release.md)
- [Traceable implementation backlog](docs/engineering-backlog.md)

## Contribution workflow

1. Confirm the feature or defect has a backlog ID, scope, owner, dependencies and acceptance criteria. Record a new item before implementation when necessary.
2. Read relevant architecture decision records (ADRs) and inspect existing routes, templates, database schema, tests and CI behavior.
3. Create a focused branch using the conventions in the coding standards.
4. Make the smallest additive change that satisfies the acceptance criteria. Preserve existing URLs, roles, records and workflows unless a separately reviewed compatibility plan says otherwise.
5. Add or update focused tests, including unauthorized and cross-school cases where school-owned data is involved.
6. Run the exact repository regression command: `python -m unittest discover -s tests -v`.
7. Update the backlog with changed paths, test evidence, CI run/commit and acceptance status.
8. Open a pull request with the required traceability and rollback notes. Do not merge or deploy without the project owner's approval.

## Scope and safety

- Do not introduce a new framework, database, service, dependency or CI tool solely for appearance.
- Do not add a microservice, background worker, API version or module boundary without an explicit need and an ADR where the architectural impact is material.
- Do not perform destructive schema changes, production data migrations, account resets, secret changes, merges or deployments without explicit authorization.
- Use fictional data in development and tests. Never commit credentials, real student/guardian information, payment data or production database files.
- A passing test suite is not proof of production readiness. Follow the security, migration, browser, backup/restore and release gates.
