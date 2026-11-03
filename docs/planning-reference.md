# Planning and provider reference

The paths below remain available as authenticated compatibility APIs and under `/api/v1`. See the main README for setup, authentication, and the preferred trip/itinerary API.

## Current architecture

FastAPI, Pydantic, SQLAlchemy, and Alembic own validation, persistence, and schema evolution. `TripStateRecord` stores normalized trip state independently of chat history, while `OrchestrationMessage` preserves the conversational audit trail. The orchestrator emits a typed command containing intent, slot updates, operations, missing fields, and a follow-up question. A deterministic state reducer validates and applies that command before anything is persisted.

A provider-neutral adapter now fills the normalized `places` table from live OpenStreetMap data for worldwide cities and keeps a 24-hour local cache. As the user types a destination or origin, the frontend queries the keyless Open-Meteo geocoding API and displays worldwide city suggestions with region and country context. Selected and featured places are enriched with Wikimedia thumbnails, official-site/ticket links, provider opening-hours tags, published fee tags, and conservative official-page price extraction. When an official site links to a ticket page, TripPilot checks that page and records that exact page as the price source. Every user-facing venue link uses the official, keyless Google Maps URL format, and each itinerary leg includes Google Maps directions between its exact provider coordinates with the trip's transport mode. OpenStreetMap remains the attributed source of place records. Published foreign-currency fees are converted with current central-bank reference data through the keyless Frankfurter API before budget arithmetic; if conversion is unavailable, currencies are not mixed. Admission is calculated per traveler and rolled up to the selected group size. Missing admission prices remain `UNKNOWN`; they are never converted into fake zero-dollar/free claims. Production and local live mode filter the cache strictly to OpenStreetMap records and never seed or fall back to mock venues. The mock provider is enabled only when `PLACE_PROVIDER=mock`, which the automated test suite sets explicitly.

The four profiles—Balanced, Smart Spend, Experience, and Premium & Convenient—use different place scoring, daily capacity, hotel/dining tiers, transport assumptions, nightlife spend, shopping allowances, and buffers. Every strategy treats the user's total budget as a hard ceiling, including Premium & Convenient. Flexible costs may move toward—but never below—their lower estimates, and optional paid stops are removed when required. Flight and activity price evidence is never rewritten. If the lowest known mandatory costs still exceed the budget, generation reports a currency-specific minimum and suggests reducing the group/date range or excluding flights instead of creating an unrealistic plan. The user can raise the budget in the same chat and retry the already-created trip. Each generated plan owns a typed cost ledger. Every row is `LIVE`, `ESTIMATED`, `FREE`, `UNKNOWN`, or `USER_ALLOWANCE`, and states whether the value is shared or per person. Unknown is never represented as zero. Plan cards expose the independent category breakdown, budget delta, evidence type, and deterministic cost reasons. The comparison API uses actual persisted place IDs and ledgers to return category deltas, shared/unique places, and day-specific differences.

Budget text is parsed independently of the LLM, preserving explicit INR, USD, GBP, and EUR symbols or words. Bare amounts remain unresolved until a currency is explicitly supplied. Preferences are stored as weighted canonical, subcategory, or raw custom signals. MiniLM (`sentence-transformers/all-MiniLM-L6-v2`) is loaded lazily when installed; a conservative dependency-free encoder keeps the application runnable when PyTorch is unavailable. Install the full embedding option with `pip install -r requirements-embeddings.txt`.

PostgreSQL runs on the `postgis/postgis:17-3.5-alpine` image. Migration `0003_location` enables PostGIS, creates a generated `geography(POINT, 4326)` column, and adds a GiST index. PostgreSQL radius queries use `ST_DWithin`; SQLite uses the same provider contract with a Haversine fallback.

Neighborhood ranking currently combines group preference match, category density, rating quality, price fit, transit connectivity, geographic compactness, and a neutral hotel-proximity placeholder. Every neighborhood result includes up to five exact places rather than returning an area-only suggestion.

After a plan is selected, the detail service resolves its stored place IDs back to persisted provider records and runs a deterministic constraint scheduler. It applies DBSCAN-style geographic clustering, category/interest/pace duration ranges, best-time windows, provider opening hours, dynamic profile-specific hotel departures, breaks, Haversine route time, and late-night day bounds. Breakfast, lunch, and dinner are persisted as exact `Meal` records and participate in the same route sequence before timing is finalized. Restaurants come from the active place provider near that day's activity cluster, honor explicit dietary constraints, and expose provider identity, Maps, official/menu/reservation links when available, a labeled price range, and the route/budget reason they were selected.

Every itinerary attraction and restaurant also has a source-linked `PlaceContext` record. OpenStreetMap/provider tags, the official venue page, and a linked structured-knowledge page are stored as individual source records. Unsupported history, architecture, celebrity, and film/TV claims remain empty and are not rendered. The UI separates the factual “why people visit” explanation from TripPilot's personalized route, preference, schedule, and budget reason.

Detailed-plan costs are persisted as `ExpenseLineItem` rows rather than a second display-only total. Each row stores quantity, per-person/shared basis, minimum/expected/maximum values, tax/fee/tip fields, arithmetic, assumptions, evidence, and an optional day or itinerary-item link. `BudgetSnapshot` stores the validated aggregation. The frontend budget cards open a category → day → exact line-item drawer; category totals, daily totals, the selected plan's expected total, and meal totals all derive from those same rows. Replacing a meal with one of the ranked nearby alternatives updates only that meal, its expense row, its day/food/overall totals, and the reconciliation snapshot.

A separate validator checks overlap, travel feasibility, duration realism, opening hours, the three daily meal windows, nightlife timing, long hikes, locks, budget ceiling, duplicates, grounded Maps links, exact restaurant records, and full expense reconciliation before the API may mark an itinerary `READY`. Generated detail is persisted in `PlanDetail` and normalized itinerary day/activity tables, so page reloads do not require regenerating it. Real images retain their Wikimedia source link and attribution; local category artwork is explicitly labeled as a fallback.

Flight search is provider-neutral and cached briefly by exact origin, destination, dates, adults, cabin, and currency. It prefers the official Skyscanner Partner Live Prices API when `SKYSCANNER_API_KEY` is configured, then uses production Amadeus Flight Offers Search + Price when its credentials are configured. Confirmed responses include group total, per-person price, offer links, provider, and freshness. Without a connected provider or confirmed offer, TripPilot returns `SEARCH_REQUIRED`, shows the allocation only as a user allowance, and provides an exact-route retail search link; it never substitutes a random or invented numeric fare.

### Google place and city-transport enrichment

Set `GOOGLE_MAPS_API_KEY` in `.env` and enable **Places API (New)** and **Routes API** in the same Google Cloud project. On selected places and restaurants, TripPilot uses the official Places Text Search response to attach a conservatively matched Google rating, review count, phone number, and canonical Maps listing. Those fields remain visibly unavailable when Google does not return them; OpenStreetMap phone tags may be shown as a labeled fallback, but TripPilot does not scrape or synthesize Google ratings.

For each adjacent itinerary stop, Mixed transport compares the public-transit total for every traveler with the number of cabs required for the group and selects the lower whole-group amount. Google Routes supplies route duration and a transit fare only when its response includes one. Cab/rideshare cost is always a labeled planning estimate because the Routes API does not quote taxi or surge prices. Both alternatives, arithmetic, Maps directions, source quality, and the winning reason are retained in the itinerary and transport expense drawer.

The trip form and trip editor call `POST /budget/minimum-estimate` while the route, dates, group size, currency, hotel tier, and flight choice are being entered. It shows a component-by-component bare-minimum planning floor. The airfare component is a distance-based reserve rather than a live fare, and the UI states that distinction explicitly.

### Orchestration API

- `POST /orchestrator/sessions`
- `GET /orchestrator/status`
- `GET /orchestrator/sessions/{session_id}`
- `POST /orchestrator/sessions/{session_id}/messages`
- `GET /orchestrator/sessions/{session_id}/messages`
- `POST /orchestrator/sessions/{session_id}/generate-plans`

### Phase 2 API

- `POST /trips/{trip_id}/generate-plans`
- `GET /trips/{trip_id}/plans`
- `GET /plans/{plan_id}`
- `POST /plans/{plan_id}/vote`
- `GET /trips/{trip_id}/votes`
- `POST /plans/{plan_id}/select`
- `POST /plans/compare`
- `GET /plans/{plan_id}/costs`
- `POST /plans/{plan_id}/bring-under-budget`
- `POST /flights/search`
- `PUT /trips/{trip_id}`
- `POST /trips/{trip_id}/regenerate-plans`

### Phase 3 API

- `GET /places/search?city=New+York+City&category=nightlife`
- `GET /places/search?city=New+York+City&latitude=40.735&longitude=-74.002&radius_km=0.5`
- `GET /places/{place_id}`
- `GET /trips/{trip_id}/neighborhoods?category=nightlife`
- `GET /locations/hierarchy?city=Boston`
- `GET /locations/search?q=Lon` (live worldwide city autocomplete)

### Selected-plan detail API

- `POST /plans/{plan_id}/generate-detail`
- `GET /plans/{plan_id}/detail`
- `GET /plans/{plan_id}/budget`
- `GET /places/{place_id}/context`
- `GET /trips/{trip_id}/restaurants/search`
- `GET /restaurants/{restaurant_id}`
- `GET /restaurants/{restaurant_id}/context`
- `GET /plans/{plan_id}/days/{day_number}/meals`
- `POST /plans/{plan_id}/days/{day_number}/meals/{meal_type}/replace`
- `GET /plans/{plan_id}/expenses`
- `GET /plans/{plan_id}/expenses/{category}`
- `GET /plans/{plan_id}/expenses/{category}/days/{day_number}`
- `GET /plans/{plan_id}/days/{day_number}/expenses`
- `GET /plans/{plan_id}/meals/{meal_id}/expense`

## Known limitations

- Public OpenStreetMap, Wikimedia, exchange-rate, and official-site endpoints can be temporarily unavailable; cached place data remains usable until it is refreshed.
- Many venues do not publish admission prices in structured markup. Those prices are labeled unknown and linked to the official/ticket page for verification.
- Detailed travel estimates use coordinates and deterministic mode speeds rather than traffic-aware live routing. The current constraint optimizer is dependency-free and compatible with a later CP-SAT adapter; it does not require OR-Tools to keep local setup lightweight.
- Opening-hour feasibility is enforced when OpenStreetMap publishes parseable hours. Unknown hours are labeled unknown and are not presented as verified availability.
- Official Skyscanner Partner or production Amadeus credentials are required for numeric live airfare. A public Google/Skyscanner page cannot be reliably scraped into a guaranteed bookable price and is therefore used only as a recheck link.
- Lodging, meals, transport, nightlife, and unpublished venue prices are explicitly labeled estimates until corresponding booking providers are connected. Export and sharing remain later roadmap phases.
