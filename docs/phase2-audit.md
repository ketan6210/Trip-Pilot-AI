# Phase 2 baseline audit

The Phase 1 source was snapshotted to `/private/tmp/trippilot-before-phase2.zip`
before implementation. Its SQLite regression suite passed: 104 tests in 6.02s,
with the existing Starlette TestClient deprecation warning. The separate
PostgreSQL baseline also passed all 104 tests in 6.12s.

| Area | Existing behavior | Phase 2 action |
|---|---|---|
| Security and v1 API | Sessions, role checks, typed schemas, 104 regression tests | Retain and enforce on new endpoints |
| Orchestration | Typed extraction; unstructured question answers; no shared LLM interface | Structured adapters, bounded correction, factual allowlists, telemetry |
| Tool use | Backend calls providers directly | Explicit scoped tool registry and bounded model/tool loop |
| Retrieval | Normalized place cache and PostGIS radius queries | Broad category retrieval, hard filtering and evidence snapshots |
| Ranking | Weighted strategies and optional MiniLM; hash fallback | Exposed components, configurable weights, member and ballot influence |
| Constraints | Some checks in detailed generation; category hours priors | Explicit hard/soft distinction and independent post-solve validation |
| Scheduling | Nearest-neighbor heuristic, no OR-Tools dependency | Joint visit selection, day assignment and routing with CP-SAT |
| Budgets | Existing cost ledgers and estimated lodging/food | Fixed-cost reservation, integer optimization ceiling, bounded repair |
| Editing | Trip regeneration and meal replacement | Scoped day edits, optimistic version checks, immutable snapshots and rollback |
| Votes | Anonymous traveler votes and one authenticated plan choice | Per-account activity/plan up/down/rank ballots used in ranking |
| Evaluation | Regression tests only | Reproducible synthetic benchmarks and measured outcome metrics |

Existing APIs and the legacy planning UI remain available. Advanced planning is
exposed explicitly, so stricter evidence requirements do not silently reinterpret
previously saved plans or overwrite their schedules.

Final preservation verification upgraded a separate copy of the original SQLite
database from revision 0009 to 0012. All original table row counts matched,
including 38 trips, 106 travelers and 144 plans; 188 normalized activities were
backfilled. The original file's SHA-256 was unchanged. The machine-readable
result is in `artifacts/phase2-migration.json`. This verifies migration safety;
it does not import those legacy trips into PostgreSQL or assign them to an account.
