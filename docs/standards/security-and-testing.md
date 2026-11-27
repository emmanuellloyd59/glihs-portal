# Security and Testing Standards

## Security baseline

- Deny by default. Every protected route and mutation checks authentication, role/capability and resource ownership on the server.
- Treat school as the tenant boundary. Resolve school context from authenticated server-side state, never from an untrusted form, query parameter or header.
- Scope every school-owned read and write, including joins, aggregates, search, exports, downloads and background work.
- Verify that referenced users, students, classes, payments, assignments and files belong to the same school before operating on them.
- Keep platform-owner privileges separate from school-level administration. Support access must be explicitly scoped, time-limited and audited before it is introduced.
- Use parameterized SQL, CSRF protection for cookie-authenticated browser mutations, secure password hashing, safe session configuration and output escaping.
- Validate uploads by size, type and storage policy; never trust filenames or client MIME types. Authorize every download.
- Store secrets in environment/hosting secret configuration, never in source control. Rotate exposed secrets and treat logs/artifacts as potentially sensitive.
- Minimize personal data in templates, API responses, analytics, exports and logs. Use fictional data in tests.
- Record sensitive administrative actions with actor, action, target/resource, school scope, timestamp and safe context.
- Security failures must fail closed; avoid leaking whether another school's record exists.

## Test expectations

The current project uses Python `unittest` and runs:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions currently installs `requirements.txt` and runs this command on Python 3.11 and 3.14. Keep that workflow green and avoid adding pytest, coverage or lint tooling unless approved and justified.

For every behavior change, add focused tests for:
- expected successful behavior;
- invalid/malformed input and boundary values;
- unauthenticated and unauthorized access;
- duplicate/conflicting operations;
- school A versus school B isolation where tenant data is involved;
- transaction rollback and no-partial-write behavior where relevant;
- audit event correctness for sensitive actions;
- compatibility with existing routes, records and templates.

For migrations, test legacy schema fixtures, preservation of IDs/relationships/values, foreign-key integrity, repeat-run behavior and recovery.

For APIs, add contract tests for response shape, status codes, validation, pagination and tenant scope.

For UI workflows, add route tests and document manual browser checks for desktop/mobile, accessibility and print/PDF as applicable.

## Coverage expectations

No numeric coverage threshold is imposed at this stage because the current workflow does not measure coverage. New and changed business logic must have direct tests; critical authorization, tenant boundaries, finance mutations, account linking and migration paths require positive and negative tests. A future measured baseline and threshold require a separate decision.

## Test data and environments

- Use isolated temporary databases and fictional accounts/data.
- Tests must not depend on production credentials, production databases, external messaging or payment systems.
- CI secrets must be test-only values.
- Separate local, CI, staging and production configuration.
- A green CI run proves only the tests executed; it does not prove browser behavior, durable storage, backup recovery or production security.
