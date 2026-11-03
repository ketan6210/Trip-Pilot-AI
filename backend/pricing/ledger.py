from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import ceil
from uuid import uuid4

from backend.currency import currency_service
from backend.optimization.plan_profiles import PlanProfile
from backend.pricing.estimator import estimate_place_price
from backend.pricing.models import CostLedgerEntry, CostScope, PriceType

CITY_HOTEL_USD = {
    "new york city": 280,
    "new york": 280,
    "london": 240,
    "paris": 220,
    "delhi": 72,
    "new delhi": 72,
    "varanasi": 48,
    "mumbai": 90,
}
CITY_DAY_USD = {
    "new york city": 1.35,
    "new york": 1.35,
    "london": 1.25,
    "paris": 1.15,
    "delhi": 0.42,
    "new delhi": 0.42,
    "varanasi": 0.32,
    "mumbai": 0.52,
}


def _entry(
    category: str,
    expected: float | None,
    currency: str,
    scope: CostScope,
    price_type: PriceType,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    provider: str | None = None,
    source: str | None = None,
    item_id: str | None = None,
    confidence: float = 0.6,
    metadata: dict | None = None,
) -> CostLedgerEntry:
    return CostLedgerEntry(
        cost_id=f"{category}-{uuid4().hex[:12]}",
        category=category,
        item_id=item_id,
        minimum=minimum,
        expected=expected,
        maximum=maximum,
        currency=currency,
        shared_or_personal=scope,
        price_type=price_type,
        confidence=confidence,
        provider=provider,
        source=source,
        metadata=metadata or {},
        fetched_at=datetime.now(UTC),
    )


def _converted_usd(values: tuple[float, float, float], currency: str) -> tuple[float, float, float] | None:
    results = [currency_service.convert(value, "USD", currency) for value in values]
    if any(result is None for result in results):
        return None
    return tuple(result.converted_amount for result in results)  # type: ignore[return-value]


def build_cost_ledger(
    trip,
    profile: PlanProfile,
    selected: list[dict],
    flight_quote: dict | None,
    *,
    align_to_budget: bool = True,
    target_profile_key: str | None = None,
) -> list[CostLedgerEntry]:
    """Build an independent group-cost ledger for one strategy."""
    currency = trip.currency.upper()
    people = max(1, trip.traveler_count)
    days = (trip.end_date - trip.start_date).days + 1
    nights = max(1, (trip.end_date - trip.start_date).days)
    city_factor = CITY_DAY_USD.get(trip.destination_city.casefold(), 1.0)
    allocations = (trip.budget.categories if trip.budget else {}) or {}
    entries: list[CostLedgerEntry] = []

    if getattr(trip, "flight_included", True):
        if flight_quote and flight_quote.get("status") == "LIVE_FARE" and flight_quote.get("total"):
            total = float(flight_quote["total"])
            entries.append(
                _entry(
                    "flight",
                    total,
                    currency,
                    CostScope.PER_PERSON,
                    PriceType.LIVE,
                    minimum=total,
                    maximum=total,
                    provider=flight_quote.get("provider"),
                    source=flight_quote.get("source_url"),
                    confidence=0.98,
                    metadata={
                        "adults": people,
                        "per_person": flight_quote.get("per_person"),
                        "route": f"{trip.origin} → {trip.destination_city}",
                    },
                )
            )
        elif float(allocations.get("flight", 0)) > 0:
            allowance = float(allocations["flight"])
            entries.append(
                _entry(
                    "flight",
                    allowance,
                    currency,
                    CostScope.PER_PERSON,
                    PriceType.USER_ALLOWANCE,
                    minimum=allowance,
                    maximum=allowance,
                    provider="Traveler budget",
                    source=flight_quote.get("search_url") if flight_quote else None,
                    confidence=1,
                    metadata={"not_a_fare": True, "adults": people},
                )
            )
        else:
            entries.append(
                _entry(
                    "flight",
                    None,
                    currency,
                    CostScope.PER_PERSON,
                    PriceType.UNKNOWN,
                    provider=flight_quote.get("search_provider") if flight_quote else None,
                    source=flight_quote.get("search_url") if flight_quote else None,
                    confidence=0,
                    metadata={"reason": "No connected live flight provider and no flight allowance"},
                )
            )

    rooms = ceil(people / 2)
    nightly = CITY_HOTEL_USD.get(trip.destination_city.casefold(), 150) * profile.hotel_multiplier
    hotel_values = _converted_usd(
        (nightly * 0.82 * rooms * nights, nightly * rooms * nights, nightly * 1.28 * rooms * nights), currency
    )
    if hotel_values:
        entries.append(
            _entry(
                "hotel",
                hotel_values[1],
                currency,
                CostScope.SHARED,
                PriceType.ESTIMATED,
                minimum=hotel_values[0],
                maximum=hotel_values[2],
                provider="TripPilot lodging estimator",
                confidence=0.65,
                metadata={"tier": profile.hotel_tier, "rooms": rooms, "nights": nights},
            )
        )
    elif float(allocations.get("hotel", 0)) > 0:
        value = float(allocations["hotel"]) * profile.hotel_multiplier
        entries.append(
            _entry(
                "hotel",
                value,
                currency,
                CostScope.SHARED,
                PriceType.USER_ALLOWANCE,
                minimum=value,
                maximum=value,
                provider="Traveler budget",
                confidence=1,
                metadata={"tier": profile.hotel_tier},
            )
        )

    food_usd = 52 * city_factor * profile.food_multiplier * people * days
    food_values = _converted_usd((food_usd * 0.72, food_usd, food_usd * 1.35), currency)
    if food_values:
        entries.append(
            _entry(
                "food",
                food_values[1],
                currency,
                CostScope.PER_PERSON,
                PriceType.ESTIMATED,
                minimum=food_values[0],
                maximum=food_values[2],
                provider="TripPilot meal estimator",
                confidence=0.58,
                metadata={"people": people, "days": days, "tier_multiplier": profile.food_multiplier},
            )
        )

    transit_usd = (12 if profile.key == "budget" else 24) * city_factor * profile.transport_multiplier * people * days
    transit_values = _converted_usd((transit_usd * 0.75, transit_usd, transit_usd * 1.35), currency)
    if transit_values:
        entries.append(
            _entry(
                "transport",
                transit_values[1],
                currency,
                CostScope.PER_PERSON if profile.key != "relaxed" else CostScope.SHARED,
                PriceType.ESTIMATED,
                minimum=transit_values[0],
                maximum=transit_values[2],
                provider="TripPilot local transport estimator",
                confidence=0.55,
                metadata={"mode": "taxi / rideshare" if profile.key == "relaxed" else "public transit / mixed"},
            )
        )

    nightlife_count = sum(place.get("category") == "nightlife" for place in selected)
    if nightlife_count:
        nightlife_usd = 38 * city_factor * profile.nightlife_multiplier * people * nightlife_count
        values = _converted_usd((nightlife_usd * 0.65, nightlife_usd, nightlife_usd * 1.55), currency)
        if values:
            entries.append(
                _entry(
                    "nightlife",
                    values[1],
                    currency,
                    CostScope.PER_PERSON,
                    PriceType.ESTIMATED,
                    minimum=values[0],
                    maximum=values[2],
                    provider="TripPilot nightlife estimator",
                    confidence=0.5,
                    metadata={"people": people, "stops": nightlife_count},
                )
            )

    for place in selected:
        if place.get("category") in {"food", "nightlife", "shopping"}:
            continue
        estimate = estimate_place_price(place, currency, trip.destination_city)
        multiplier = people
        entries.append(
            _entry(
                "activities",
                None if estimate.expected is None else estimate.expected * multiplier,
                currency,
                CostScope.PER_PERSON,
                estimate.price_type,
                minimum=None if estimate.minimum is None else estimate.minimum * multiplier,
                maximum=None if estimate.maximum is None else estimate.maximum * multiplier,
                provider=estimate.provider,
                source=estimate.source,
                item_id=str(place.get("id")),
                confidence=estimate.confidence,
                metadata={
                    "place_name": place.get("name"),
                    "per_person": estimate.expected,
                    "people": people,
                    "maps_url": place.get("maps_url"),
                    "ticket_url": place.get("ticket_url"),
                },
            )
        )

    wants_shopping = any(place.get("category") == "shopping" for place in selected) or profile.key in {
        "experience_heavy",
        "relaxed",
    }
    shopping_allowance = float(allocations.get("shopping", 0))
    if wants_shopping and shopping_allowance <= 0:
        converted = currency_service.convert(40 * city_factor * profile.shopping_multiplier * people, "USD", currency)
        shopping_allowance = converted.converted_amount if converted else 0
    if shopping_allowance > 0:
        entries.append(
            _entry(
                "shopping",
                shopping_allowance,
                currency,
                CostScope.PER_PERSON,
                PriceType.USER_ALLOWANCE,
                minimum=0,
                maximum=shopping_allowance,
                provider="Plan allowance",
                confidence=1,
                metadata={"optional": True, "people": people},
            )
        )

    subtotal = sum(entry.expected or 0 for entry in entries)
    # Every strategy treats the user's total as a hard ceiling. If the midpoint is
    # too high, choose lower-tier flexible assumptions first. Flight and activity
    # prices represent actual/estimated bookable items, so they are never scaled to
    # manufacture a fit; planning removes an optional stop instead when necessary.
    target_utilization = {
        "budget": 0.78,
        "balanced": 0.90,
        "experience_heavy": 0.96,
        "relaxed": 0.93,
    }.get(target_profile_key or profile.key, 0.90)
    target_before_buffer = float(trip.total_budget) * target_utilization / (1 + profile.buffer_rate)
    if align_to_budget and subtotal > target_before_buffer:
        protected_categories = {"flight", "activities"}
        adjustable_entries = [
            entry
            for entry in entries
            if entry.category not in protected_categories
            and entry.price_type not in {PriceType.LIVE, PriceType.FREE}
            and entry.expected is not None
        ]
        reducible = sum(max(0, float(entry.expected) - float(entry.minimum or 0)) for entry in adjustable_entries)
        reduction_needed = subtotal - target_before_buffer
        factor = max(0, 1 - reduction_needed / reducible) if reducible else 0
        for entry in adjustable_entries:
            original_expected = float(entry.expected)
            floor = float(entry.minimum or 0)
            entry.expected = round(floor + (original_expected - floor) * factor, 2)
            entry.maximum = max(float(entry.maximum or original_expected), entry.expected)
            entry.metadata["budget_aligned_factor"] = round(factor, 4)
            entry.metadata["original_expected"] = original_expected
        subtotal = sum(entry.expected or 0 for entry in entries)
    buffer_value = subtotal * profile.buffer_rate
    entries.append(
        _entry(
            "buffer",
            buffer_value,
            currency,
            CostScope.SHARED,
            PriceType.USER_ALLOWANCE,
            minimum=0,
            maximum=buffer_value,
            provider="Plan contingency",
            confidence=1,
            metadata={"rate": profile.buffer_rate},
        )
    )
    return entries


def summarize_ledger(entries: list[CostLedgerEntry], budget: float, people: int) -> dict:
    categories: dict[str, dict] = defaultdict(
        lambda: {"minimum": 0.0, "expected": 0.0, "maximum": 0.0, "price_types": set()}
    )
    unknown = []
    for entry in entries:
        row = categories[entry.category]
        row["minimum"] += entry.minimum or 0
        row["expected"] += entry.expected or 0
        row["maximum"] += entry.maximum or 0
        row["price_types"].add(entry.price_type.value)
        if entry.price_type == PriceType.UNKNOWN:
            unknown.append(entry.category)
    breakdown = {
        key: {
            **{name: round(value, 2) for name, value in row.items() if name != "price_types"},
            "price_types": sorted(row["price_types"]),
        }
        for key, row in categories.items()
    }
    minimum = round(sum(row["minimum"] for row in breakdown.values()), 2)
    expected_raw = sum(row["expected"] for row in breakdown.values())
    expected_per_person = round(expected_raw / max(1, people), 2)
    expected = round(expected_raw, 2)
    maximum = round(sum(row["maximum"] for row in breakdown.values()), 2)
    delta = round(expected - budget, 2)
    ratio = expected / budget if budget else 0
    status = "OVER_BUDGET" if delta > 0 else "NEAR_BUDGET" if ratio >= 0.9 else "UNDER_BUDGET"
    return {
        "minimum": minimum,
        "expected": expected,
        "maximum": maximum,
        "expected_per_person": expected_per_person,
        "breakdown": breakdown,
        "budget_status": status,
        "budget_delta": delta,
        "over_by": max(0, delta),
        "under_by": max(0, -delta),
        "pricing_complete": not unknown,
        "unknown_categories": sorted(set(unknown)),
    }


def ledger_to_dict(entry: CostLedgerEntry) -> dict:
    return entry.model_dump(mode="json")
