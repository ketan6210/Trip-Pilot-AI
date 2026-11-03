from decimal import Decimal

from backend.ai.constraints import cents
from backend.core.errors import AppError
from backend.optimization.plan_profiles import PLAN_PROFILES
from backend.pricing.ledger import build_cost_ledger

CATEGORIES = ("transportation", "hotel", "food", "activities", "local_transport", "miscellaneous")
ALIASES = {
    "flight": "transportation",
    "transport": "local_transport",
    "buffer": "miscellaneous",
    "shopping": "miscellaneous",
}


def fixed_costs(trip, constraints, strategy, flight_quote):
    profile = next(item for item in PLAN_PROFILES if item.key == strategy)
    entries = build_cost_ledger(trip, profile, [], flight_quote, align_to_budget=False)
    amounts = {category: 0 for category in CATEGORIES if category != "activities"}
    sources = {}
    unknown = set()
    for entry in entries:
        category = ALIASES.get(entry.category, entry.category)
        if category == "activities" or entry.category == "shopping":
            continue
        if category in constraints.fixed_costs:
            continue
        if entry.expected is None:
            unknown.add(category)
            continue
        amount = Decimal(str(entry.expected))
        # Reserve breakfast/lunch; the solver prices exactly one dinner per day.
        if category == "food":
            amount *= Decimal(2) / 3
        amounts[category] += cents(amount)
        sources[category] = {
            "type": entry.price_type.value,
            "source": entry.provider,
            "scope": "breakfast/lunch" if category == "food" else "group trip total",
        }
    for category, value in constraints.fixed_costs.items():
        amounts[category] = cents(value)
        sources[category] = {
            "type": "USER_ALLOWANCE",
            "source": "Explicit planning policy",
            "scope": "non-scheduled costs; restaurant dinners are added separately",
        }
    required = {"hotel", "food", "local_transport"} | ({"transportation"} if trip.flight_included else set())
    unknown.update(required - set(sources))
    unknown -= set(constraints.fixed_costs)
    if unknown:
        raise AppError(
            422,
            "COST_INPUT_REQUIRED",
            "Unknown essential costs prevent enforcing the budget. Supply explicit group allowances or connect the provider.",
            {"categories": sorted(unknown)},
        )
    return amounts, sources


def budget_result(schedule, candidates, fixed, trip):
    totals = {key: value for key, value in fixed.items()}
    totals.setdefault("activities", 0)
    by_id = {candidate.place_id: candidate for candidate in candidates}
    for day in schedule.days:
        for stop in day.stops:
            place = by_id[stop.place_id]
            category = "food" if place.category == "food" else "activities"
            totals[category] += place.cost_cents
    total = sum(totals.values())
    return {
        "category_cents": totals,
        "total_cents": total,
        "remaining_cents": cents(trip.total_budget) - total,
        "per_person_cents": round(total / trip.traveler_count),
        "currency": trip.currency,
        "within_budget": total <= cents(trip.total_budget),
    }
