# Unified School ERP

## Integration approach

The ERP workspace composes the existing admissions/student, staff, attendance,
academic structure, homework/learning, grading, report-card, discipline, and
finance workflows. It does not introduce duplicate student, employee, attendance,
grade, report-card, or finance ledgers.

## Core and optional capabilities

Core features are always enabled: admissions, students, staff, attendance,
academics, examinations, discipline, and reporting. Optional library, transport,
and inventory entitlements are school-scoped and default to disabled. A platform
owner controls optional entitlements. Enabling an entitlement is not a claim that
the optional workflow has already shipped; each subsystem remains a separate
implementation stage.

## Dashboards, permissions, and reports

The ERP workspace is available at `/api/erp/workspace`; its JSON dashboard is
`/api/erp/dashboard`. Metrics are included only when the signed-in role has the
corresponding scoped permission. Reports are available at
`/api/erp/reports/{students|admissions|attendance|grades|finance}`, with
class, academic year, term, and date filters where relevant. Add
`format=csv` for an export; exports require the applicable export permission,
are limited to 5,000 rows, and neutralize spreadsheet formula prefixes.

Every report query includes the selected school ID and joins related records
using both record ID and school ID. Feature entitlement changes are audited.
Existing domain workflows remain the systems of record.

## Release safeguards

- Additive schema only; no existing domain tables or rows are replaced.
- Core entitlements are seeded idempotently for existing schools.
- Optional entitlements are isolated by school.
- Dashboard metrics and report datasets are permission-gated.
- Cross-school joins are rejected by explicit school-scoped predicates.
