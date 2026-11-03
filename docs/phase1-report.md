# Phase 1 implementation report

This upgrade extends the existing application. The original planning, comparison,
pricing, conversation, restaurant, context and expense modules remain in use.
Verification was completed locally on October 25, 2026.

## 1. Existing repository audit

The baseline had a React/TypeScript frontend, a FastAPI backend, SQLAlchemy models,
nine Alembic migrations, PostGIS support, four planning strategies and 71 passing
tests. The frontend production build passed, and the API started successfully
against an isolated database. The supplied SQLite database contained 38 trips.

The principal gaps were unrestricted access, implicit USD defaults, incomplete
input extraction, itineraries stored mainly as JSON, inconsistent provider errors,
an unbounded route cache, and startup schema creation outside migrations. The
Docker PostGIS image also needed an explicit platform on this ARM Mac.

See [the feature-by-feature audit](phase1-audit.md). This directory has no Git
repository. Before implementation, a source snapshot was saved at
`/private/tmp/trippilot-before-phase1.zip`. The original `trippilot.db` was not
modified or used as a test database.

## 2. Features fixed

- Currency is required at the API boundary. Rupees remain INR; repeated questions
  no longer invent USD. Unsupported LLM scalar values cannot silently fill missing
  critical fields.
- Natural-language extraction handles the requester plus their friends, solo trips,
  duration, dietary preferences, mobility limits, nightlife and intensity. A typed
  normalizer reports missing fields and asks one necessary question.
- Date ranges, traveler counts, budgets, enum values and partial updates are
  validated. Trip changes invalidate old plans; stale plans cannot be selected or
  voted on.
- Group totals retain their original amount instead of being reconstructed from
  rounded per-person values. Budget responses use six distinct categories and
  decimal amounts, including remaining budget and per-person cost.
- Generated candidates persist days and activities immediately. Detailed planning
  and meal replacement synchronize normalized activities with the existing detail
  representation. Reopening a saved plan uses stored details when available.
- Legacy itinerary generation uses the shared planning/scheduling pipeline. Nightlife
  is scheduled in its appropriate time window; avoided preferences are filtered.
- Routing rejects excessive consecutive city-leg distances. Walking-only candidates
  must respect the daily transfer-walking allowance.
- Provider failures have finite retries and typed errors. Place outages no longer
  appear to be invalid destinations; expired FX is not silently reused. Routes have
  a bounded TTL cache and failed lookups are not cached.
- Existing trip, traveler, plan, comparison, detail, meal, expense and conversation
  routes now enforce access permissions. Voting cannot impersonate another account.
- Database startup uses migrations. Test fixtures use disposable databases, enforce
  SQLite foreign keys, and refuse PostgreSQL test databases without a `_test` suffix.
- Docker supports this ARM Mac, retains PostgreSQL in a volume, exposes local-only
  ports, checks API readiness, and uses the appropriate host address for Ollama.

## 3. Features added

- Modular API, authentication, domain and itinerary repository layers around the
  existing domain-specific modules.
- Registration, login, logout, expiring database sessions, salted password hashes,
  browser cookies, bearer-token support, CSRF checks and per-process auth throttling.
- OWNER, EDITOR and VIEWER membership, access revocation and authenticated group votes.
- Versioned REST APIs with typed request/response schemas and consistent errors.
- User/group UUIDs, public trip UUIDs, trip status and timestamps, normalized itinerary
  days/activities, integrity constraints, and one selected plan per trip.
- Shared provider retry/error handling, maps/currency/hotel contracts, adapters around
  existing providers, and concurrent async origin/destination geocoding.
- Sign-in/registration UI, session-expiry handling and a saved-trips view.
- Ruff linting/formatting, pre-commit configuration, development dependencies and a
  GitHub Actions workflow for SQLite, PostgreSQL and the frontend build.
- An explicit administrator script to assign selected pre-account trips to an
  existing registered user; it defaults to a dry run.

## 4. Files changed

The principal implementation files are grouped below. Shared Python formatting and
import cleanup also touched existing modules and migrations; those changes are not
new feature implementations.

| Area | Files |
|---|---|
| Application and APIs | `backend/main.py`; new `backend/api/{auth,legacy,v1}.py` |
| Security/errors | new `backend/core/{auth,errors}.py` |
| Domain services | new `backend/domain/{trips,normalization,itineraries}.py` |
| Persistence | `backend/db.py`, `backend/models.py`; new `backend/repositories/itineraries.py` |
| Schemas and extraction | `backend/schemas.py`, `backend/orchestration/{parser,schemas,service}.py`, `backend/conversation/anti_loop.py`, `backend/parsing/currency_parser.py` |
| Planning and costs | `backend/planning.py`, `backend/services.py`, `backend/pricing/ledger.py`, `backend/optimization/route_optimizer.py`, `backend/detail/service.py`, `backend/meals/service.py` |
| Provider reliability | new `backend/providers/{http,contracts,locations}.py`; `backend/providers/{flights,google_places,live}.py`, `backend/currency/converter.py`, `backend/geography/cities.py`, `backend/transport/service.py` |
| Migrations | new `migrations/versions/0010_security_itineraries.py`, `0011_selected_plan_constraint.py`; `alembic.ini` |
| Frontend | `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/vite.config.ts`, `frontend/nginx.conf`, new `frontend/.dockerignore` |
| Configuration | `backend/settings.py`, `.env.example`, `docker-compose.yml`, new `.dockerignore` |
| Quality/CI | new `pyproject.toml`, `requirements-dev.txt`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` |
| Tests | `tests/conftest.py`; new `tests/test_phase1_{security,domain,providers,migrations}.py`, `tests/__init__.py`; updates to existing test payloads/assertions |
| Operations/docs | new `scripts/assign_legacy_owner.py`; rewritten `README.md`; new `docs/phase1-audit.md`, `docs/planning-reference.md`, this report |

Package `__init__.py` files were added for the new modules. Existing tests now supply
an explicit currency and authenticated session; their planning, pricing and detail
behavior checks remain. The previous README's deeper planning documentation is
retained in `docs/planning-reference.md`.

## 5. API endpoints

All paths below are under `/api/v1`. `/docs` exposes the full OpenAPI specification.

| Method | Path | Result |
|---|---|---|
| POST | `/auth/register` | Create account and session |
| POST | `/auth/login` | Authenticate and start session |
| GET | `/auth/me` | Current account |
| POST | `/auth/logout` | Revoke session; 204 |
| POST | `/trips/normalize` | Typed constraints, missing fields and question |
| POST | `/trips` | Create owned trip from typed constraints |
| GET | `/trips` | Paginated accessible trips |
| GET, PATCH | `/trips/{id}` | Read/update trip |
| POST | `/trips/{id}/generate` | Persist four strategy variants |
| GET | `/trips/{id}/itineraries` | Plans, days, activities and budgets |
| GET, PATCH | `/itineraries/{id}` | Read/rename itinerary |
| POST | `/itineraries/{id}/select` | Select one current itinerary |
| GET | `/trips/{id}/group` | Membership and roles |
| PUT | `/groups/{id}/members` | Grant/update a registered account's role |
| DELETE | `/groups/{id}/members/{user_id}` | Revoke membership |
| POST, GET | `/groups/{id}/votes` | Vote as authenticated user/list votes |
| POST | `/locations/resolve` | Concurrent origin/destination geocoding |

Existing planning, comparison, place, conversation, restaurant and expense APIs
remain available through protected compatibility routes, including versioned paths.
Unversioned paths are retained for the existing frontend. `/health` and
`/health/ready` expose liveness and database readiness.

Errors have `error.code`, `error.message` and `error.details`, with a compatibility
`detail` string. Validation, authentication, permissions, conflicts, quotas and
provider unavailability return distinct HTTP statuses. Unexpected errors are
sanitized and logged.

## 6. Database schema

PostgreSQL 17 with PostGIS is the default and was exercised in Docker. SQLite remains
an isolated development/test option. Existing integer IDs and domain tables remain;
UUIDs are used for new accounts, groups and public trip identifiers.

| Entity/table | Persistence and integrity |
|---|---|
| `users` | Unique normalized email, display name, salted password hash, UUID |
| `auth_sessions` | Token digest, user foreign key, expiry; plaintext tokens are not stored |
| `trips` | Existing constraints and budget; added owner, public UUID, status, updated timestamp, date/count/budget checks |
| `travelers`, `traveler_preferences` | Existing per-traveler preferences, diet and mobility data |
| `groups`, `group_members` | One group per trip; unique group/user membership; role check |
| `group_votes` | One current vote per member/group; itinerary reference |
| `plan_variants` | Strategy, version, status and estimates; partial unique index for one selected plan per trip |
| `itinerary_days` | Unique plan/day number, scheduled date |
| `itinerary_activities` | Place reference, ordered position, start/end minutes, estimated cost, currency, coordinates, category, selection reason |
| `trip_budgets`, `plan_cost_entries` | Existing allocations and itemized planning ledger |
| `expense_line_items`, `budget_snapshots` | Existing detailed costs and budget evaluations |
| `places` | Existing metadata, provider identity, spatial indexes and PostgreSQL geography |
| `trip_states`, `orchestration_messages` | Existing durable conversations; added conversation ownership |
| `plan_details`, `meals`, `place_contexts`, `place_context_sources`, `votes` | Existing detailed plans, restaurants, sourced context and legacy traveler votes |

Revision `0010_security_itineraries` adds security and normalized schedules, backfills
public trip IDs and detailed activities, and leaves old trips unowned. Revision
`0011_selected_plan` resolves historical duplicate selections before adding the
unique selection index. Earlier revisions retain their migration behavior.

Migrating a copy of the actual supplied database preserved all **38 trips** and
backfilled **188 activities**. Counts in eight existing domain tables were checked
before and after migration. The original database remains at revision
`0009_google_place_contact`; the migrated verification copy is at
`/private/tmp/trippilot-existing-migration.db`.

Changing the connection string does not import SQLite records into PostgreSQL.
The running PostgreSQL application database is separate from those legacy records.
Legacy import and explicit account ownership assignment remain deployment steps;
the first account is never given access to previously private trips automatically.

## 7. Test results

| Verification | Observed result |
|---|---|
| Original baseline | 71 tests passed in 2.41 seconds |
| Final suite, isolated SQLite | **104 passed**, 1 warning, 6.47 seconds |
| Final suite, PostgreSQL/PostGIS test database | **104 passed**, 1 warning, 6.18 seconds |
| Ruff lint | All checks passed |
| Ruff format check | 126 Python files already formatted |
| Frontend | TypeScript and Vite production build passed |
| Migrations | Fresh database upgrades; SQLite upgrade/downgrade/re-upgrade; populated-copy preservation passed |
| Docker | Images built; database and API health checks passed; frontend started |
| HTTP flow through nginx | Registration, authenticated identity, INR/friends normalization, trip creation, four plans, selection, detailed itinerary (`READY`), retrieval, listing and logout passed |

The HTTP flow used a dedicated PostgreSQL test database, deterministic parsing and
explicit mock places. The stack was subsequently restored to its normal application
database. Mocked service tests cover invalid input, missing information, currency,
budgets, permissions, session revocation, provider failures/retries, cache expiry,
concurrent lookups, routing, distinct plans and persistence.

The warning is Starlette's deprecation notice for its current `httpx` TestClient
integration; it did not fail tests. No paid live provider transactions, browser
automation, load tests or hosted GitHub Actions runs were performed. CI configuration
is supplied; its equivalent checks were run locally.

## 8. Remaining limitations

- Existing SQLite trips are preserved but have not been imported into PostgreSQL or
  assigned to an account. Follow the README's explicit legacy-data procedure.
- Live geocoding, places, flight and optional model integrations still depend on
  external availability/configuration; this upgrade's repeatable checks mock them.
- Hotels have a provider contract but no live inventory/booking adapter. Lodging and
  many activity/transport prices are estimates; pricing completeness is exposed.
- Planning is heuristic. Detailed validation reports `READY` or `FAILED`; approximate
  route times, walking inside venues and actual accessibility require verification.
- Itinerary PATCH supports renaming. Schedule changes currently use constraints and
  regeneration; arbitrary manual activity editing is not implemented.
- Membership is API-based for registered accounts. Email invitations, account
  verification, password recovery and a group-management UI are not implemented.
- Process caches and authentication throttling need shared coordination or ingress
  controls for multiple workers. Long generations have no durable background queue.
- The conservative deterministic parser is not a general multilingual date parser.
  Async overlap is implemented for independent location requests; existing planning
  providers predominantly run synchronously in worker threads.
- The production frontend builds and its proxy/API flow were checked, but rendered
  browser interactions have not been automated. Production deployment still needs
  HTTPS/secure cookies, deployment secrets and operational monitoring.
