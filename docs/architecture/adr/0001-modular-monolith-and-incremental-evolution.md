# ADR-0001: Retain Flask and evolve the existing application incrementally

- **Status:** Accepted
- **Date:** 2026-11-27
- **Related backlog:** GOV-001, ARC-001

## Context

The existing repository is a working Flask application with established routes, templates, authentication, SQLite persistence, migrations and a GitHub Actions unittest workflow. The application has a large central `app.py` and several extracted route files that receive dependencies through a global namespace. The product direction is a multi-school Digital Education Ecosystem.

A framework rewrite or microservice split would add operational and migration risk before the domain boundaries, tenant controls and service contracts are mature.

## Decision

Retain Flask and the existing repository. Evolve toward a modular monolith with explicit application construction, shared core interfaces, bounded business modules and incremental route/service/repository extraction. Preserve current URLs, templates, authentication, roles, database records and user workflows unless a separately reviewed compatibility plan changes them.

The eight target modules are School Network, School Websites, School ERP, Learning Platform, Education Finance, Communication, Intelligence Centre and Education Marketplace.

## Alternatives considered

- Rewrite using a different web framework: rejected because it increases migration scope and breaks established workflows without a demonstrated requirement.
- Split into microservices now: rejected because operational complexity, distributed transactions, deployment and monitoring costs are premature.
- Keep the monolith unchanged: rejected because current global namespace registration and concentrated SQL/business logic impede safe evolution.

## Consequences

**Positive:** incremental delivery, one deployable application, simpler local development, preserved CI and a path to clearer ownership.

**Negative:** boundaries require discipline; shared database coupling remains during transition; modules are not independently deployable.

## Compatibility and migration

Do not move or rename routes without compatibility tests. Do not replace primary keys or reset the database as part of extraction. Schema changes remain additive and separately reviewed. Existing SQLite behavior and current CI remain supported while any future PostgreSQL work proceeds as its own verified workstream.

## Verification and acceptance

Each extraction must preserve route behavior, pass focused and full regression tests, enforce school scope, avoid circular imports and document changed dependencies. A folder structure alone does not prove modularity.
