# Engineering Governance

**Repository:** `emmanuellloyd59/glihs-portal`  
**Product direction:** Digital Education Ecosystem — one connected platform for multiple independent schools.  
**Governance status:** Initial standards established; implementation backlog is a living record.

## 1. Purpose and authority

These standards govern changes to the existing Flask repository. They are designed around its current Flask, Flask-Login, Flask-WTF, SQLite, `unittest`, and GitHub Actions workflow. They do not declare planned architecture implemented.

The project owner approves consequential decisions: production data changes, destructive migrations, releases, deployments, merges when specifically reserved, changes to authentication/tenant security, and new paid infrastructure. Contributors may prepare code and reviewable pull requests within an approved scope.

When a standard conflicts with existing production behavior, do not silently break compatibility. Document the conflict, add a backlog item, and propose an ADR or migration plan.

## 2. Governance documents

| Concern | Source of truth |
|---|---|
| Architecture decisions | [ADR register](architecture/README.md) and individual ADRs |
| Code and repository organization | [Coding and repository standards](standards/coding-and-repository.md) |
| API contracts | [API standards](standards/api.md) |
| Schema changes and migrations | [Database migration standards](standards/database-migrations.md) |
| Security and tests | [Security and testing standards](standards/security-and-testing.md) |
| Acceptance, release and rollback | [Acceptance and release standards](standards/acceptance-and-release.md) |
| Feature traceability | [Engineering backlog](engineering-backlog.md) |
| Contributor workflow | [CONTRIBUTING.md](../CONTRIBUTING.md) |

## 3. Architecture governance

- Retain Flask and evolve the application as a modular monolith unless a documented need and approved ADR justify a change.
- Treat the existing repository, schema and user workflows as the compatibility baseline.
- School is the tenant boundary; campuses are school-owned organizational units, not independent tenants by default.
- Global identity and school membership are distinct concepts in the target architecture; preserve current account compatibility during transition.
- Every school-owned read, write, export, download, join and aggregate must enforce server-derived school scope.
- Module boundaries must be explicit. No import of `app.py` from domain modules, hidden `globals()` injection, circular imports or cross-module private-table access.
- Material architecture changes require an ADR before or alongside implementation. ADRs record context, decision, alternatives, consequences, status and supersession.

## 4. Module addition rules

A contributor adding a module must:
1. Identify its business capability, data ownership, routes, services and dependencies.
2. Identify whether data is platform-, school-, campus- or shared-owned.
3. Define its authorization matrix and tenant-scoping behavior.
4. Reuse shared identity, tenant context, database, audit and error interfaces rather than creating competing foundations.
5. Keep SQL behind the owning repository boundary as extraction progresses.
6. Avoid importing another module's internals; expose narrow service interfaces for cross-module use.
7. Add tests for normal, invalid, duplicate, unauthorized and cross-school scenarios as applicable.
8. Document compatibility, migration, operational and rollback implications.
9. Add or update backlog traceability and acceptance evidence.

The eight product capabilities are School Network, School Websites, School ERP, Learning Platform, Education Finance, Communication, Intelligence Centre and Education Marketplace. Their names do not imply that all are implemented or production-ready.

## 5. Traceability

Every feature/defect is assigned a stable backlog ID (for example `NET-001`, `ERP-004`, `FIN-002`). The backlog links:

`ID → requirement → ADR → implementation paths → migration → tests → CI evidence → acceptance → release`

A feature is not complete because code exists or a route is visible. Mark acceptance complete only when the evidence required by the feature's criteria is recorded.

## 6. Quality gates

- Preserve existing route names, HTTP methods, templates, role behavior and data relationships unless a reviewed compatibility change explicitly changes them.
- Run the existing full regression suite: `python -m unittest discover -s tests -v`.
- Add focused tests for every behavior changed and negative authorization tests for every protected operation.
- Keep the GitHub Actions workflow as the required baseline. It currently runs the unittest suite on Python 3.11 and 3.14 for configured push/PR events.
- Do not impose a numeric coverage threshold until coverage can be measured reliably and the project owner approves a baseline. Every changed behavior must nevertheless have tests.
- Documentation must accurately distinguish implemented, tested, preview-only, planned and production-approved capabilities.

## 7. Change control

No production data migration, destructive schema change, production secret rotation, release, deployment or service cutover is authorized by a documentation or code task alone. Such actions require a specific approval and a tested recovery plan. Pull requests should remain reviewable and should not be merged merely because CI is green.

## 8. Maintenance

The backlog and ADR register are maintained with each material change. Review this governance set when the framework, database strategy, CI workflow, tenant model, security posture or release process changes. Prefer amending an existing standard with a rationale over creating duplicate policy documents.
