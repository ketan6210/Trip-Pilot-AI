from decimal import Decimal
from time import perf_counter
from typing import Literal

from pydantic import Field

from backend.ai.budget import budget_result
from backend.ai.optimizer import optimize
from backend.ai.schemas import StrictModel
from backend.currency import currency_service
from backend.llm.providers import strict_schema
from backend.llm.telemetry import active_trace
from backend.providers.flights import get_flight_quote


class SearchArguments(StrictModel):
    category: str | None = Field(default=None, max_length=50)


class PlaceArguments(StrictModel):
    place_id: int = Field(gt=0)


class RouteArguments(StrictModel):
    first_place_id: int = Field(gt=0)
    second_place_id: int = Field(gt=0)


class CurrencyArguments(StrictModel):
    amount: Decimal = Field(ge=0, le=999999999, allow_inf_nan=False)
    source_currency: str = Field(pattern="^[A-Z]{3}$")


class BudgetArguments(StrictModel):
    place_ids: list[int] = Field(max_length=80)


class OptimizeArguments(BudgetArguments):
    strategy: Literal["balanced", "budget", "experience_heavy", "relaxed"]


class EmptyArguments(StrictModel):
    pass


class PlanningTools:
    """Read-only tools scoped to this trip's filtered provider evidence."""

    specs = {
        "search_places": (SearchArguments, "Search this trip's provider-grounded, hard-filtered candidates."),
        "get_place_details": (PlaceArguments, "Get an existing retrieved place; never generate missing facts."),
        "get_route": (RouteArguments, "Get a labeled route estimate between retrieved candidates."),
        "convert_currency": (CurrencyArguments, "Convert using the currency provider into this trip's currency."),
        "calculate_budget": (BudgetArguments, "Calculate estimated group costs including reserved non-activity costs."),
        "get_flight_options": (
            EmptyArguments,
            "Query configured flight providers for this trip; availability may be unknown.",
        ),
        "optimize_schedule": (OptimizeArguments, "Run the CP-SAT constraint optimizer on known candidate IDs."),
    }

    def __init__(self, db, trip, candidates, constraints, ranked, fixed):
        self.db, self.trip, self.constraints = db, trip, constraints
        self.candidates = {row.place_id: row for row in candidates}
        self.ranked, self.fixed = ranked, fixed

    def definitions(self):
        return [
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": strict_schema(schema.model_json_schema()),
                "strict": True,
            }
            for name, (schema, description) in self.specs.items()
        ]

    def execute(self, name, arguments):
        started, success = perf_counter(), False
        try:
            if name not in self.specs:
                raise ValueError("Unknown tool")
            args = self.specs[name][0].model_validate(arguments)
            result = self._execute(name, args)
            success = True
            return {"result": result}
        except Exception:
            # Provider internals, SQL details and credentials never become model context.
            return {
                "error": {
                    "code": "TOOL_UNAVAILABLE_OR_INVALID",
                    "message": "Use only supplied place IDs and schema-valid arguments; required provider data may be unavailable.",
                }
            }
        finally:
            trace = active_trace.get()
            if trace:
                trace.tools.append(
                    {
                        "name": name if name in self.specs else "unrecognized",
                        "success": success,
                        "latency_ms": round((perf_counter() - started) * 1000, 3),
                    }
                )

    def _execute(self, name, args):
        if name == "search_places":
            return [
                row.model_dump(mode="json")
                for row in self.candidates.values()
                if not args.category or row.category == args.category
            ]
        if name == "get_place_details":
            return self.candidates[args.place_id].model_dump(mode="json")
        if name == "get_route":
            from backend.ai.optimizer import leg

            minutes, km = leg(
                self.candidates[args.first_place_id], self.candidates[args.second_place_id], self.trip.transport_mode
            )
            return {
                "duration_minutes": minutes,
                "distance_km": round(km, 3),
                "source": "Coordinate-based estimate",
                "verified_live_route": False,
            }
        if name == "convert_currency":
            result = currency_service.convert(float(args.amount), args.source_currency, self.trip.currency)
            if result is None:
                raise ValueError("No current exchange rate")
            return result.model_dump(mode="json")
        if name == "get_flight_options":
            return get_flight_quote(self.trip)
        if len(args.place_ids) != len(set(args.place_ids)) or set(args.place_ids) - self.candidates.keys():
            raise ValueError("Unavailable or duplicate places")
        if name == "calculate_budget":
            return {
                "currency": self.trip.currency,
                "estimated_total": str(
                    Decimal(sum(self.fixed.values()) + sum(self.candidates[key].cost_cents for key in args.place_ids))
                    / 100
                ),
                "scope": "Group costs, including two reserved meals and priced restaurant dinners",
            }
        from backend.ai.constraints import cents

        result = optimize(
            [row for row in self.ranked if row.candidate.place_id in args.place_ids],
            self.trip,
            self.constraints,
            args.strategy,
            cents(self.trip.total_budget) - sum(self.fixed.values()),
        )
        return {
            "schedule": result.model_dump(mode="json"),
            "budget": budget_result(result, list(self.candidates.values()), self.fixed, self.trip),
        }

    def validate_output(self, advice):
        ids = [row.place_id for row in advice.recommendations]
        unknown = set(ids) - self.candidates.keys()
        trace = active_trace.get()
        if trace:
            trace.reference_count += len(ids)
            trace.unsupported_references += len(unknown)
        if unknown or len(ids) != len(set(ids)):
            raise ValueError("Unsupported or duplicate place IDs")
