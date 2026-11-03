# TripPilot AI

TripPilot turns a trip brief into validated constraints and four persisted strategies: balanced, budget, experience-focused, and relaxed. This upgrade extends the existing FastAPI/React application, preserving conversation, place lookup, preference scoring, pricing, comparison, restaurant, context, and expense features.

Phase 2 adds a constraint-optimized group planner with OR-Tools, structured model adapters, scoped tools, voting, partial edits and immutable itinerary versions. See the [Phase 2 implementation report](docs/phase2-report.md) and [benchmark artifact](artifacts/phase2-evaluation.json) for measured results and limits.

## Use advanced planning

Create a trip and complete its traveler profiles, then open **Advanced planning**. Save your interests and dietary or mobility requirements. Owners can grant a registered account viewer/editor access; each account supplies its own preferences and votes. No invitation email is sent.

Use **Schedule and cost constraints** to set the daily window, transfer limit and explicit group allowances where essential costs are unavailable. Allowances are estimates, not provider quotes. Scheduled dinners are priced separately from the breakfast/lunch allowance. By default, places with unknown opening hours, unverified dietary compatibility or unverified required wheelchair access are excluded.

**Optimize four alternatives** creates balanced, budget, experience-heavy and relaxed schedules. Each passes independent budget/time/constraint validation before any previous plan set is superseded. Select a plan, vote or rank it, inspect its activity score components, and try edits such as `Make day 2 cheaper`, `Remove museum`, `Add nightlife`, `Add Central Park`, `Move dinner later on day 2`, or `Reduce walking`. Exact venue edits require an eligible provider result. Infeasible edits preserve the saved itinerary. Version history restores an older schedule as a new version after checking current provider facts.

The current scheduler includes one dinner per day, distinct venues across days, and up to 3/3/4/2 stops per day for the four strategies. Routes use labeled coordinate estimates; a return-by constraint includes the return leg when an anchor location is supplied through the API. Unknown hours can be permitted explicitly, but then they are labeled unverified. Model downtime falls back to deterministic ranking.

The compatibility generator remains available for trips without an advanced planning policy. Once advanced planning is enabled, generation uses its hard constraints and legacy detail mutation routes reject changes that would bypass versioned validation.

## Phase 2 configuration and evaluation

`ORCHESTRATOR_PROVIDER` supports `deterministic`, `ollama`, and `openai`; model names stay configurable. `LLM_TIMEOUT_SECONDS` and `LLM_CORRECTION_ATTEMPTS` bound each structured call. `AI_SOLVER_SECONDS` bounds each CP-SAT solve; `AI_CANDIDATE_LIMIT` controls the ranked pool, with required category coverage retained. See `.env.example` for all settings.

Start the existing local Ollama runtime with `./scripts/start-local-llm.sh`. Docker connects through `host.docker.internal`. The default `EMBEDDING_BACKEND=concept_hash` uses deterministic synonym-expanded vectors; optional `sentence_transformers` requires `requirements-embeddings.txt` and model weights. Exact constraints never depend on embeddings.

Run the reproducible synthetic benchmark without changing application data:

```bash
python -m scripts.evaluate_planning
```

To evaluate the configured model, use `--use-llm --output artifacts/phase2-evaluation-model.json`. Cloud providers may incur their normal charges. Model usage and stage latency are persisted per request, including failed planning runs. Owners can view diagnostics under `/api/v1/trips/{id}/ai/runs`. Token costs remain unknown unless both `LLM_INPUT_USD_PER_MILLION` and `LLM_OUTPUT_USD_PER_MILLION` are configured; local zero rates exclude hardware/electricity costs. Synthetic benchmark results do not establish live travel-data quality.

## Run with Docker

Requirements: Docker Desktop with Compose. The PostGIS image runs as `linux/amd64` through emulation on ARM Macs because this image tag lacks an ARM manifest.

```bash
cp .env.example .env
docker compose up --build -d
```

Open **http://localhost:5173**, create an account, and sign in. API documentation: **http://localhost:8000/docs**. The API runs Alembic migrations before startup. PostgreSQL data lives in the `postgres_data` named volume; `docker compose down` preserves it. Application ports bind to localhost.

For an offline demonstration with explicitly labeled sample places:

```bash
PLACE_PROVIDER=mock ORCHESTRATOR_PROVIDER=deterministic docker compose up --build -d
```

Live mode never substitutes mock venues after a provider failure. Numeric flight quotes require production provider credentials; otherwise the UI labels the flight amount as a planning allowance and supplies a search link.

## Native development

Use Python 3.13+ and Node 22+. PostgreSQL/PostGIS is the default.

```bash
docker compose up -d db
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn backend.main:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

For isolated SQLite development, set `DATABASE_URL=sqlite:///./development.db` and run the same migrations. SQLite enforces foreign keys. PostgreSQL retains the existing PostGIS geography column, GiST index, and `ST_DWithin` queries. Startup does not create or alter tables implicitly. `/health` checks liveness; `/health/ready` checks database connectivity.

## Architecture

```mermaid
flowchart LR
    UI[React UI] --> API[FastAPI routers]
    API --> Auth[Sessions and permissions]
    API --> Domain[Trip and itinerary services]
    API --> Input[Typed normalization and conversation]
    Domain --> Planning[Planning / optimization / pricing]
    Planning --> Providers[Provider adapters]
    Providers --> HTTP[Timeouts / retries / TTL caches]
    Domain --> Repo[Itinerary repository]
    Repo --> DB[(PostgreSQL + PostGIS)]
    Planning --> DB
```

- `backend/main.py`: application setup, CORS, health and error handling.
- `backend/api/`: authentication, v1 APIs and protected compatibility routes.
- `backend/core/`: sessions, permissions and consistent error envelopes.
- `backend/domain/`: trip changes, normalization, selection and budget summaries.
- `backend/repositories/`: normalized itinerary persistence and retrieval.
- Existing planning, optimization, pricing, detail, meals, expenses, geography, orchestration and provider modules retain their domain responsibilities.

Place, flight, maps, currency and hotel contracts separate providers from core logic. Hotels currently have no booking adapter; lodging remains an estimate. Requests have finite timeouts and at most three attempts with exponential backoff. Independent route-location lookups use concurrent async HTTP. Existing synchronous providers run in FastAPI worker threads; ORM sessions are not shared between tasks.

Places, FX rates, routes, city suggestions and flight quotes have TTLs. Failed routes are not cached; expired FX is not silently reused. Redis is not required for this single-process foundation. Sessions are database-backed; process caches and authentication throttling need shared coordination or ingress controls for multi-worker deployment.

## Authentication and permissions

Register/login issue random opaque session tokens, stored only as SHA-256 digests. Browsers use HttpOnly, SameSite=Lax cookies; API clients use `Authorization: Bearer <access_token>`. Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 iterations, following [OWASP work-factor guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). Sessions expire after 24 hours by default; logout revokes the current session.

Cookie mutations require `X-Requested-With: TripPilot`, and supplied origins must be trusted. Enable `COOKIE_SECURE=true` behind HTTPS. Auth responses are not cacheable; login/register are rate-limited per process.

| Role | Read | Edit/generate | Vote as self | Select/manage members |
|---|---|---|---|---|
| OWNER | Yes | Yes | Yes | Yes |
| EDITOR | Yes | Yes | Yes | No |
| VIEWER | Yes | No | Yes | No |

Owners grant access to registered accounts through the membership API; no email is sent. Conversations remain private to their owner. Legacy traveler voting lets owners record anonymous group choices; v1 group voting binds identity to the authenticated account. All legacy routes enforce permissions, including comparisons, details, expenses and meal replacement.

## API

These paths are under `/api/v1`. OpenAPI includes schemas and compatibility endpoints.

| Method | Path | Behavior |
|---|---|---|
| POST | `/auth/register`, `/auth/login` | Create account/start session |
| GET | `/auth/me` | Current account |
| POST | `/auth/logout` | Revoke session (204) |
| POST | `/trips/normalize` | Normalize `{text, known?}` |
| POST, GET | `/trips` | Create/list accessible trips |
| GET, PATCH | `/trips/{id}` | Read/validate a partial update |
| POST | `/trips/{id}/generate` | Persist four strategies |
| GET | `/trips/{id}/itineraries` | Current days, activities and budgets |
| GET, PATCH | `/itineraries/{id}` | Read/rename an itinerary |
| POST | `/itineraries/{id}/select` | Owner selects a current itinerary |
| GET | `/trips/{id}/group` | Group membership |
| PUT | `/groups/{id}/members` | Grant/update `{email, role}` |
| DELETE | `/groups/{id}/members/{user_id}` | Revoke access |
| POST, GET | `/groups/{id}/votes` | Vote `{itinerary_id}`/list votes |
| POST | `/locations/resolve` | Concurrent origin/destination geocoding |

Example trip creation:

```json
{
  "origin": "Chicago",
  "destination": "New York City",
  "start_date": "2027-06-01",
  "duration_days": 3,
  "travelers": 5,
  "budget_amount": "70000.00",
  "currency": "INR",
  "interests": ["nightlife", "museums", "nature", "food"],
  "dietary_preferences": ["vegetarian"],
  "mobility_preferences": ["minimal_walking"]
}
```

Currency is mandatory: `2000 rupees` remains INR 2000; a missing currency stays unresolved after repeated questions. “I travel with 4 friends” means 5 travelers. Solo trips through groups of 20 and ranges up to 30 days are supported. The normalizer returns `status`, `request`, `missing_fields`, and one necessary follow-up `question`. Start date plus duration determines the end date in the typed API; the existing chat additionally requests return-date confirmation. Unrecognized prose remains unresolved.

Trip PATCH uses established names `destination_city`, `traveler_count`, and `total_budget`. Changes mark old plans stale; stale plans cannot be selected or voted on. Itinerary PATCH currently supports renaming; schedule changes use trip constraints and regeneration. V1 budget amounts are decimal strings. See [the planning reference](docs/planning-reference.md) for existing detail/meal/expense endpoints.

Errors have a stable envelope, with a compatibility `detail` string:

```json
{"error":{"code":"MISSING_TRIP_FIELDS","message":"Complete the trip details first.","details":{"missing_fields":["currency"]}}}
```

Validation returns 422; authentication 401; forbidden access 403; missing records 404; stale/conflicting changes 409; throttling/provider quotas 429; unavailable providers 503; malformed provider data 502. Unexpected failures return 500 without exposing internals or submitted secrets.

## Database and existing data

Eleven Alembic revisions preserve existing tables and integer identifiers. UUID users/groups and public trip UUIDs extend the model. Tables include users, auth sessions, trips, travelers/preferences, groups/members/votes, budgets, plan variants/cost entries, itinerary days/activities, places, conversations, context sources, meals, expense lines and budget snapshots. Foreign keys, indexes, unique constraints and date/count/cost checks protect integrity. A partial unique index allows one selected plan per trip.

Activities store a place foreign key, start/end minutes, estimated group cost, currency, coordinates, category and selection reason. Duration derives from the time range; times can exceed 24:00 for nightlife. Existing detailed itineraries are backfilled into normalized rows while their original JSON remains available. New candidates persist schedules immediately; detail and meal replacement keep normalized rows synchronized.

The supplied `trippilot.db` is preserved. Back it up before running `DATABASE_URL=sqlite:///./trippilot.db alembic upgrade head`. Switching to PostgreSQL does **not** automatically transfer SQLite data. Pre-account trips remain unowned until explicit administrator assignment; the first registrant cannot claim them. Register the intended account in the same database, then review/apply specific assignments:

```bash
python -m scripts.assign_legacy_owner --email you@example.com --trip-id 1
python -m scripts.assign_legacy_owner --email you@example.com --trip-id 1 --apply
```

## Configuration

See [.env.example](.env.example) and [backend/settings.py](backend/settings.py).

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection; Compose supplies its internal database host |
| `CORS_ORIGINS` | Trusted frontend origins |
| `COOKIE_SECURE`, `SESSION_HOURS`, `AUTH_RATE_LIMIT` | Session/auth controls |
| `PLACE_PROVIDER` | `live` or explicit `mock` |
| `ORCHESTRATOR_PROVIDER` | `deterministic`, `ollama`, or existing `openai` integration |
| `OLLAMA_BASE_URL`, `OLLAMA_DOCKER_BASE_URL`, `OLLAMA_MODEL` | Optional model; Docker uses its separate host address |
| `PROVIDER_ATTEMPTS`, `LIVE_DATA_TIMEOUT_SECONDS` | Bounded external requests |
| `PLACE_CACHE_HOURS`, `ROUTE_CACHE_SECONDS`, `FLIGHT_CACHE_MINUTES` | Cache freshness |
| `GOOGLE_MAPS_API_KEY` | Optional Places/Routes enrichment |
| `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET`, `SKYSCANNER_API_KEY` | Optional live flight providers |

Ollama is optional; deterministic parsing requires no model. The existing local model startup script and optional `requirements-embeddings.txt` remain available.

## Testing and quality

```bash
pytest -q
ruff check backend tests migrations scripts/assign_legacy_owner.py
ruff format --check backend tests migrations scripts/assign_legacy_owner.py
cd frontend && npm ci && npm run build
```

External providers are mocked. Default tests use a temporary SQLite file, never existing user databases. Run the same suite against PostgreSQL/PostGIS:

```bash
docker compose exec db createdb -U trippilot trippilot_test
TEST_DATABASE_URL=postgresql+psycopg://trippilot:trippilot@127.0.0.1:5433/trippilot_test pytest -q
```

The database name must end in `_test`; fixtures clear its application tables. Migration tests cover upgrade/downgrade and existing-trip preservation. Ruff lint/format and optional pre-commit are configured. CI checks SQLite, PostgreSQL, Python quality and the frontend build.

## Practical limits

- Planning is heuristic. Candidate schedules are estimates; detailed validation determines `READY` or `FAILED`.
- Routing rejects excessive consecutive city-leg distances. Without Google Routes, timing uses labeled coordinate estimates. Walking limits cover transfers; accessibility and walking inside venues still need verification.
- Lodging and many prices remain estimates. No booking/payment integration is implied. Unknown prices and unavailable conversions remain labeled.
- Email verification, password recovery, email invitations, distributed cache/throttling coordination, and durable generation queues are future work.
- Deterministic extraction is conservative, not multilingual date NLP. SQLite-to-PostgreSQL transfer is an explicit deployment step.

See [the audit](docs/phase1-audit.md) and [the implementation report](docs/phase1-report.md) for evidence, changes and limitations.
