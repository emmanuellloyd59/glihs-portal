# CMHS Digital Campus — Version 0.1 Demo

CMHS Digital Campus is an early school-portal demonstration built with Flask, Flask-Login, SQLite, Flask-WTF, and Bootstrap.

## What is included

- Public welcome page and consistent responsive branding.
- Login with hashed-password verification and protected routes.
- Role-aware dashboard for student, teacher, and administrator accounts.
- Teacher/administrator student registration and records listing.
- Search across visible student records.
- CSRF protection on forms and POST-only sign-out.
- Startup creation of the `users` and `students` tables if missing.

## Important demonstration limits

This is a development/demo build, not a production school management system. Do not enter real student records, dates of birth, guardian details, or real contact information. Use fictional test data only.

The application currently uses SQLite. If the hosting provider's filesystem is ephemeral, database contents may be lost on a redeploy or restart. Reliable long-term storage needs an appropriately configured persistent disk or a managed database and corresponding application configuration. Do not assume a successful deployment means the database is persistent.

Grades, attendance, homework, report cards, and learning materials are not implemented yet; they are clearly marked as planned.

## Suggested demonstration walkthrough

1. Open the public home page and show the CMHS Digital Campus branding.
2. Open Portal Login. Sign in only with a demonstration account already configured by the project owner.
3. Show the role-specific dashboard and account details.
4. For a teacher or administrator account, open Student Records.
5. Register a fictional student using a fake admission number such as `DEMO-001` and invented contact details.
6. Confirm the new record appears in the records table, then test the search field.
7. Sign out and confirm the protected dashboard redirects to login.
8. Explain which modules are available and which are planned for later releases.

## Configuration

Required environment variable:

- `SECRET_KEY`: a private, strong random value configured in the deployment environment; never commit it to the repository.

Optional first administrator setup:

- `INITIAL_ADMIN_USERNAME`
- `INITIAL_ADMIN_PASSWORD`

Set both optional variables in the hosting environment to create the initial administrator account on startup. The password is stored as a hash. Choose a unique demonstration password, and do not publish it in this README or commit it to Git.

Optional database location:

- `DATABASE_PATH`: a filesystem path for the SQLite database. This only provides persistence if the chosen path is backed by storage that actually persists across deployments and restarts.

## Local development

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Configure `SECRET_KEY` in your local environment, then start the app:

```bash
python app.py
```

The application serves on port 5000. The first launch creates missing database tables, but it does not invent user credentials. A demonstration user must be configured through the supported setup or an existing database.

## Version 0.1 acceptance checklist

- [ ] Public home page loads on desktop and mobile widths.
- [ ] Login works with a configured demo account.
- [ ] Dashboard is protected when signed out.
- [ ] Student accounts cannot open staff-only student-record pages.
- [ ] Teacher/admin can register fictional students and see saved records.
- [ ] Duplicate admission numbers are handled with a friendly message.
- [ ] Records search filters the visible table.
- [ ] Sign-out works and protected pages require login again.
- [ ] Hosting persistence is understood and verified before storing any important data.
