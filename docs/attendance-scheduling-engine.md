# Attendance and Scheduling Engine

## Scope
Additive engine for the multi-tenant Digital Education Ecosystem. Existing attendance and timetable tables remain intact; this engine adds session-based student attendance, staff attendance, correction approvals, school/campus calendar exceptions, teacher availability, and recurring schedule slots.

## Tenant and academic boundaries
Every new operational row carries `school_id`; campus is nullable for school-wide records. API handlers derive school scope from the authenticated school context and never trust a client-supplied school ID. Academic year and period references are validated against the same school, and date ranges are checked before a session or slot is created. Student attendance is protected by a database trigger that requires the student, session, school, campus and class to agree.

## Attendance integrity
- Unique session key per school/campus/class/local date/session code.
- Unique student entry per session; batch duplicates are rejected before writes and database uniqueness remains the final concurrency guard.
- Staff entries are unique per school/user/local date/session code.
- School-local future-date validation uses the configured IANA time zone.
- Correction requests capture old/new status and a reason; only a different authorized manager can approve or reject.
- Correction events are append-only and all writes are audited.

## Scheduling and calendar
- Calendar exceptions are scoped to school or campus and can block session creation on holidays/closures/weekends.
- Teacher availability windows are weekday-based, time-bounded and conflict checked.
- Timetable slots reject overlapping class, teacher and room allocations and honor explicit availability windows.
- Academic years/periods are optional for compatibility with existing unstructured schedules but are validated when supplied.

## API surface
- `GET /api/v1/attendance/summary?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `POST /api/v1/attendance/sessions`
- `POST /api/v1/attendance/sessions/{id}/entries`
- `POST /api/v1/attendance/staff`
- `POST /api/v1/attendance/corrections`
- `POST /api/v1/attendance/corrections/{id}/decision`
- `POST /api/v1/school-calendar/days`
- `POST /api/v1/scheduling/availability`
- `POST /api/v1/scheduling/slots`

## Compatibility and rollout
The legacy `attendance` and `class_timetable` tables and their existing reports are not dropped, rewritten or bulk-migrated. New endpoints use the new session/slot model. The schema initializer is idempotent and additive. Production migration, authenticated browser QA, full CI and deployment remain release gates.
