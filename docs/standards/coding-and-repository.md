# Coding and Repository Standards

## Compatibility-first development

- Inspect the existing implementation and tests before editing.
- Prefer small, additive changes. Avoid broad formatting, renaming or file moves mixed with behavior changes.
- Keep the existing Flask framework, dependencies and `unittest` conventions unless a reviewed ADR approves a change.
- Preserve route paths, methods, endpoint names, template context, flash behavior and status codes unless acceptance criteria explicitly approve a change.
- Do not introduce dependencies without explaining the concrete capability, maintenance cost, security impact and deployment compatibility.

## Python style

- Follow PEP 8 conventions: four-space indentation, descriptive `snake_case` functions/variables, `PascalCase` classes and uppercase constants.
- Keep functions focused and avoid hidden mutable global state.
- Use explicit imports and narrow function signatures. Do not use `globals()` as a dependency-injection mechanism in new code.
- Avoid circular imports. Core/shared modules must not import business modules.
- Use parameterized SQL exclusively; never interpolate user-controlled values into SQL.
- Use context managers or guaranteed `try/finally` cleanup for database connections and files.
- Keep secrets and personal data out of logs, exceptions, test fixtures and commits.
- Validate at boundaries; services must still enforce business invariants rather than trusting route validation alone.

## Repository organization

- `app.py` remains the compatibility entry point while application factory extraction is introduced incrementally.
- `application/` owns app construction, extension setup and registration.
- `core/` owns framework-neutral shared contracts such as tenant context, authorization interfaces, database lifecycle, audit interface and errors.
- `modules/<capability>/` owns that capability's routes, services, repositories, validators and templates where appropriate.
- `shared/` contains genuinely cross-cutting reusable components, not a dumping ground for business logic.
- `tests/` mirrors capabilities or concerns and retains the existing test discovery command.
- `docs/architecture/adr/` stores ADRs; `docs/standards/` stores standards; `docs/engineering-backlog.md` tracks work.
- Do not create empty speculative packages for all future systems. Add a module when a bounded capability is being implemented.

## Dependency direction

`routes → services → repositories → database adapter`

- Routes may use shared authorization and validation interfaces.
- Services may depend on their own repositories and narrow shared contracts.
- Repositories own SQL for their domain.
- A module must not query another module's private tables. Use a documented service interface or an explicit application-level orchestrator.
- Shared/core code cannot import modules.
- Do not let domain modules import `app.py` or register themselves through a global namespace.
- Cross-module events are not required for simple in-process calls; introduce an event mechanism only when asynchronous decoupling is justified.

## Naming and commits

- Branches: `feature/<short-name>`, `fix/<short-name>`, `docs/<short-name>`, `chore/<short-name>`, `release/<version>`.
- Commits: `feat(scope): ...`, `fix(scope): ...`, `docs(scope): ...`, `test(scope): ...`, `refactor(scope): ...`, `chore(scope): ...`.
- Keep commits focused and explain the reason, not only the file operation.
- Reference backlog IDs in branch, commit or PR description when practical.

## Pull requests

Include: purpose, backlog ID, related ADR, behavior/compatibility impact, changed paths, schema/migration impact, security/tenant impact, tests run and CI evidence, acceptance checklist, rollback/recovery plan, and screenshots for UI changes when useful.

One PR should represent one bounded capability or cohesive change. Do not combine an unrelated feature, mass formatting and migration.
