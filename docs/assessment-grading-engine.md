# Configurable Assessment and Grading Engine

The module adds school-specific grading independently of legacy grade/report-card records.

- Each school owns independent grading scales; each change creates a new version.
- Grade bands must cover 0–100 contiguously, without gaps or overlaps.
- Weighted averages use Decimal arithmetic and half-up rounding to two decimal places. Component weights are normalized, allowing schools to choose their own weighting.
- Every calculated result stores a grading-scale snapshot and a calculation/component snapshot.
- Publication requires a separate authorized approver; self-approval is rejected.
- Issued results are immutable at database level. Corrections are separate, reasoned, independently approved records; approved corrections are applied to transcript presentation while the original issued result and grading snapshot remain unchanged.
- Tenant scope is enforced in route queries and score-insert database triggers.
- Schema creation is additive and idempotent; existing records are not rewritten.

## API
POST /api/assessment/grading-scales
GET /api/assessment/grading-scales
POST /api/assessment/assessments
POST /api/assessment/assessments/{id}/scores
POST /api/assessment/results/calculate
POST /api/assessment/results/{id}/request-publication
POST /api/assessment/approvals/{id}/decision
POST /api/assessment/results/{id}/corrections
POST /api/assessment/corrections/{id}/decision
GET /api/assessment/students/{student_id}/transcript

Assessment types: examination, continuous assessment, assignment, project, practical and oral.
