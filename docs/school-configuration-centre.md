# School Configuration Centre

## Purpose

The School Configuration Centre gives each institution an independently managed configuration profile. The school organization owns defaults; campuses inherit those defaults and may store selective overrides.

## Configuration sections

| Section | Organization default | Campus override |
|---|---:|---:|
| Display name, logo URL and brand colors | Yes | Yes |
| Contact and address details | Yes | Yes |
| Academic year and term calendar | Yes | Yes |
| Grading scale and passing score | Yes | Yes |
| Currency, IANA time zone and languages | Yes | Yes |
| Enabled platform modules | Yes | No |
| Operational policies | Yes | Yes |

The logo field accepts an HTTPS URL or a local /static/ path. Managed uploads and object storage are not part of this iteration.

Module selection is configuration, not an authorization grant. Subscription entitlements, permissions and feature-level access checks remain separate enforcement layers.

## Tenant isolation

- Organization settings are keyed by the existing schools.id.
- Campus settings use (school_id, campus_id) as their key and a composite foreign key to campuses(school_id, id).
- A school administrator or principal can access only their authenticated school.
- Platform owners must select a school explicitly.
- Every campus query includes both school and campus identifiers.
- Client-provided IDs never establish authorization.

## Interfaces and APIs

- Web interface: GET/POST /school-network/configuration.
- Organization read: GET /api/v1/schools/{school_id}/configuration.
- Organization update: PATCH /api/v1/schools/{school_id}/configuration.
- Organization history: GET /api/v1/schools/{school_id}/configuration/history.
- Campus effective settings: GET /api/v1/schools/{school_id}/campuses/{campus_id}/configuration.
- Campus overrides: PATCH /api/v1/schools/{school_id}/campuses/{campus_id}/configuration.
- Campus history: GET /api/v1/schools/{school_id}/campuses/{campus_id}/configuration/history.

Organization updates may include expected_version to reject stale writes. Campus reads return organization defaults recursively overlaid by campus overrides. Campus patches accept an overrides object; omit a section to inherit the organization default.

## Versioning and audit

Each successful organization or campus change:
1. validates the complete merged configuration;
2. increments that scope's version;
3. saves the current document;
4. inserts an immutable snapshot in school_configuration_versions;
5. writes a corresponding row in school_configuration_audit;
6. commits all changes together.

The version history API exposes actor ID, change summary and timestamp. History is not overwritten by later edits.

## Controlled legacy migration

The additive, idempotent startup migration creates:
- school_configurations;
- campus_configurations;
- school_configuration_versions;
- school_configuration_audit.

For each existing school without a configuration row, the migration copies the existing school name, website, address, country, time zone and contact values into version 1. Existing campus names, addresses, country and contact details are preserved as campus override version 1. Requested onboarding services seed the initial organization module list when those records exist.

Existing configuration rows and version histories are never overwritten. Legacy school/campus records and IDs remain intact. Existing schools display/contact fields are synchronized on subsequent organization configuration saves for compatibility with legacy application code.

## Validation

The server validates:
- display-name and contact field lengths;
- HTTPS/local logo locations and HTTPS institutional website URLs;
- six-digit hexadecimal brand colors;
- email, country code, language tags and three-letter currency format;
- IANA time zones;
- ISO academic dates, term names and date ordering;
- grading bands and passing-score bounds;
- module catalogue membership;
- campus override section allowlist;
- structured operational policy values.

## Operational boundaries

This implementation is SQLite-backed to match the current application. PostgreSQL tenant RLS, production migration rehearsal, managed logo uploads, and full integration of every academic/finance route with configurable grading and currency remain separate release gates. Changing a setting does not rewrite historical grades, invoices or payments.
