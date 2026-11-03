# Phase 1 repository audit

Baseline: 71 tests passed (2.41 seconds); TypeScript/Vite production build passed;
FastAPI started against an isolated SQLite database and `/health` returned 200.
The supplied directory has no Git repository. A source snapshot was saved outside
the project before implementation. Existing user databases are not test fixtures.

| Feature | Existing? | Working? | Tested at baseline? | Action |
|---|---|---|---|---|
| React/Vite UI | Yes | Builds; planning, comparison, detail and expense flows | Build only | Preserve; add sign-in and session handling |
| FastAPI API | Yes | Unversioned, many handlers own business logic | Yes | Extract application setup; add v1 and shared services |
| Authentication/permissions | No | Anyone can access and mutate any trip/session | No | Database sessions, password hashes, owner/editor/viewer checks |
| PostgreSQL/PostGIS | Migrations 0001–0009 | Docker image lacks native ARM manifest | SQLite tests only | Fix Docker platform; test migrations on PostgreSQL |
| Trip/traveler/preferences | Normalized tables | Missing ownership; solo travel rejected | Yes | Extend in place; preserve integer identifiers |
| Groups/authenticated votes | Anonymous traveler votes only | Voting identity is supplied by caller | Partial | Add account membership and account-bound voting |
| Budget/currency | Separate ledgers and FX service | API defaults USD; repeated clarification invents currency | Partial | Require currency, preserve ambiguity, decimal totals |
| Input/LLM | Typed commands, deterministic/Ollama/OpenAI | Friends count, mobility/diet extraction incomplete | Yes | Extend deterministic parser; no advanced AI work |
| Missing fields | Durable conversation state | Critical fields tracked; unsafe currency fallback | Yes | Keep missing currency unresolved |
| Four strategies | Distinct scoring, capacity and cost profiles | Existing tests confirm differences | Yes | Preserve; persist scheduled activities |
| Itinerary storage | Plans and detailed JSON, meals and expenses | Attractions/days only in JSON | Yes | Add normalized days/activities, migrate existing details |
| Routing | Haversine, scheduler, optional Google Routes | No excessive-distance gate; route cache never expires | Partial | Add distance checks and bounded TTL |
| Providers | Place/flight abstractions and live adapters | Timeouts, inconsistent error/fallback behavior | Mocks | Shared bounded retries/errors, contracts and adapters |
| Async I/O | Sync routes run in FastAPI thread pool | External requests predominantly sequential | No | Concurrent independent lookups without sharing ORM sessions |
| Environment/Docker | Example env and compose | Startup `create_all` bypasses migration discipline | Not run before audit | Migration-only startup, persistent volume, health checks |
| Static quality | Type hints, TS build | No configured Python lint/format check | No | Configure Ruff and CI |

The existing domain-specific modules (planning, optimization, pricing, meals,
expenses, geography and orchestration) are retained. Changes concentrate on the
API boundary, security, transactions, persistence and validation.
