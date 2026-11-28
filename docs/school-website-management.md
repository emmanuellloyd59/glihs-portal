# School Website Management Engine

The School Website Management Engine is a bounded capability in the existing Flask modular monolith. It gives each institution an independently branded public website while keeping public content separate from ERP records.

## Data boundaries

- `school_websites` owns a school's websites, globally unique public slugs, publication state, owner and theme.
- `school_website_pages` stores plain-text page content, draft/published state and navigation order.
- `school_website_domains` stores custom hostnames and verification state. Only platform owners can mark a domain verified after checking the required DNS TXT record outside the application.
- `school_website_audit` records website, page, publication and domain administration events.
- The public renderer reads only published website/page rows and an allow-list of public institutional profile fields (`name`, `description`, public location and public contact details). It never queries student, guardian, staff, attendance, grade, report-card, discipline or finance tables.

All schema creation is additive and idempotent. It does not rewrite school IDs, existing school profile data or ERP records.

## Permissions and ownership

- `school.manage_websites`: create websites, edit branding, manage pages and domains.
- `school.publish_websites`: publish and unpublish websites/pages.
- School owners, school administrators and principals receive these permissions through the existing scoped RBAC policy defaults. Explicit policy denies still override allows.
- A website has one owner user. Ownership transfers only to an active user attached to the same school (legacy school assignment or active school membership).
- Every management operation derives school scope from the authenticated request's active school context and filters all website/page/domain queries by both school and website IDs. Foreign tenant identifiers return 404.

## Routes

- `GET /school-websites/manage` — management workspace.
- `GET /school-websites` — tenant-scoped website list (JSON).
- `POST /api/school-websites` — create a draft website and a draft home page.
- `GET/PATCH /api/school-websites/<website_id>` — read/update website metadata, owner and theme.
- `POST /api/school-websites/<website_id>/pages` — create a draft page.
- `PATCH /api/school-websites/<website_id>/pages/<page_id>` — update page content, navigation and publication state.
- `POST /api/school-websites/<website_id>/publish` and `/unpublish` — control public availability.
- `POST /api/school-websites/<website_id>/domains` — register a pending custom domain and receive its DNS TXT proof value.
- `POST /api/school-websites/domains/<domain_id>/verify` — platform-owner action after independent DNS verification.
- `GET /school-sites/<website_slug>/<page_slug>` — public website by globally unique website slug.
- Verified custom domains resolve their request host to exactly one published website; an unknown page or unpublished page returns 404.

JSON mutation requests are protected by the application's global CSRF middleware and must send the CSRF token in `X-CSRFToken`. The management workspace does this automatically.

## Publication and content safety

Websites and pages start as drafts. A website cannot be published until its home page is published. Only published pages appear in public navigation. Public content is rendered as escaped plain text; arbitrary HTML is not trusted or marked safe. Logo URLs must use HTTPS and theme colors are validated hexadecimal values.

## Verification limits

Automated tests cover additive/idempotent schema creation, public page resolution, unpublished/draft denial, cross-school routing, global slug uniqueness, pending/verified custom-domain behavior and non-disclosure of a private ERP sentinel. Domain ownership still requires an operator to verify DNS externally. Manual browser/accessibility review, production migration rehearsal and live custom-domain/TLS configuration are release tasks, not claims made by this implementation.
