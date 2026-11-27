# Configurable Academic Structure Engine

## Scope

This is an additive academic configuration subsystem for the Digital Education Ecosystem. It does not replace the existing student, grade, report-card, attendance, or timetable workflows.

Academic structures are tenant-owned. The system stores names, codes, sequence, effective dates, and relationships as school data rather than relying on a hardcoded national model. A school may define early-years stages, primary/secondary levels, year groups, vocational pathways, tertiary levels, or its own progression labels.

## Data model

- `academic_years`: institution-defined year labels, optional date boundaries, and lifecycle status.
- `academic_periods`: arbitrary named periods attached to an academic year. Period types include term, semester, quarter, trimester, module, block, and custom.
- `grade_levels`: institution-defined progression stages, sequence, optional code, and progression group.
- `academic_classes`: year-specific class/cohort instances connected to a progression level.
- `class_sections`: sections or homerooms within a class.
- `subjects`: school-owned subject catalogue.
- `curriculum_subjects`: versioned curriculum name/version mapped to a progression level and subject, with required/elective/optional designation and optional credits/effective dates.
- `course_enrollments`: tenant-validated student, class section, subject, academic year, and optional period relationship.
- `academic_structure_versions`: immutable JSON snapshots of the configured structure after each administrative configuration change.

Tenant integrity is checked in route-level inserts and reinforced by database triggers for periods, classes, sections, curriculum mappings, and course enrollments. This protects against forged identifiers and accidental cross-school relationships.

## Historical compatibility and migration

The migration is additive and idempotent:

1. Create the new academic tables and tenant indexes/triggers.
2. Add nullable academic-year and period references to legacy grade and report-card rows; add a nullable subject reference to grades.
3. Import existing academic-year and term labels from grades/report cards without changing their original text, score values, or publication records.
4. Import existing class-level labels from student, teacher-assignment, and timetable records as progression-level labels.
5. Import subject labels from grades, teacher assignments, and timetable records.
6. Backfill grade/report-card academic-year and period references, and grade subject references, when an exact same-school match exists.
7. Record an initial structure snapshot for each school.

Legacy `class_level` is not automatically converted into a class plus section: the legacy field does not reliably distinguish progression level, homeroom, cohort, and section. Schools must confirm those mappings in the new interface rather than allowing the migration to invent structure. No legacy student or academic row is deleted.

## Administration

The `/academic-structure` page is available to authorized school administrators, principals, vice principals, academic administrators, registrars, and teachers (read-only). Managers can create years, periods, progression levels, subjects, classes, sections, curriculum mappings, and course enrollments. All data access is scoped to the authenticated school context.

## Deliberate boundaries

- This release does not select a national curriculum or infer a country's regulatory requirements.
- It does not infer academic-year dates from labels.
- It does not overwrite old text labels or silently move historical student results when a school changes its current structure.
- Editing/retiring existing definitions, advanced progression rules, prerequisite graphs, credit transfer, cross-campus academic calendars, and bulk class mapping are later extensions.
- Existing grade-entry and timetable screens still contain legacy K–12/three-term assumptions; those workflows must be migrated to consume the new structure before the full platform can claim end-to-end configurable academics.

## Verification

The new test module covers legacy label import, idempotent migration, historical references, period-type flexibility, tenant-specific uniqueness, version snapshots, and database-level rejection of cross-tenant class relationships. The complete project regression suite and CI status must pass before merge or deployment.
