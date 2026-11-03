from __future__ import annotations

from collections import Counter
from datetime import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.context import get_or_create_place_context, serialize_place_context
from backend.expenses.service import expense_dashboard, reconcile_expenses
from backend.geography.maps import google_maps_place_url
from backend.models import BudgetSnapshot, ExpenseLineItem, Meal, Place, PlanDetail, PlanVariant, Trip
from backend.optimization.schedule_optimizer import _opening_window
from backend.pricing.estimator import estimate_place_price
from backend.providers import get_place_provider
from backend.providers.base import PlaceSearch
from backend.providers.mock import distance_km, place_to_dict

MEAL_MULTIPLIERS = {"breakfast": 0.58, "lunch": 0.82, "dinner": 1.18}
MEAL_WINDOWS = {
    "breakfast": (7 * 60, 10 * 60 + 30),
    "lunch": (11 * 60 + 30, 14 * 60 + 30),
    "dinner": (17 * 60 + 30, 21 * 60 + 30),
}


def _time_from_minute(minute: int) -> time:
    return time((minute // 60) % 24, minute % 60)


def _meal_raw(place: Place, trip: Trip, meal_type: str) -> tuple[float, float, float, str, str | None]:
    estimate = estimate_place_price(place_to_dict(place), trip.currency, trip.destination_city)
    multiplier = MEAL_MULTIPLIERS[meal_type]
    if estimate.minimum is None or estimate.expected is None or estimate.maximum is None:
        raise ValueError(
            f"A currency-safe meal estimate is unavailable for {place.name}; "
            "TripPilot will not substitute an invented restaurant price."
        )
    values = (float(estimate.minimum), float(estimate.expected), float(estimate.maximum))
    return tuple(round(value * multiplier, 2) for value in values) + (estimate.price_type.value, estimate.source)


def _supports_diet(place: Place, requirements: set[str]) -> bool:
    if not requirements:
        return True
    tags = (place.source_metadata or {}).get("osm_tags", {})
    searchable = " ".join([*place.tags, place.subcategory, str(tags.get("cuisine") or "")]).casefold()
    for requirement in requirements:
        normalized = requirement.casefold().replace(" ", "_")
        if tags.get(f"diet:{normalized}") == "yes" or requirement.casefold() in searchable:
            continue
        return False
    return True


def _meal_type_fit(place: Place, meal_type: str) -> float:
    searchable = " ".join([place.name, place.subcategory, *place.tags]).casefold()
    cafe = any(token in searchable for token in ("cafe", "café", "coffee", "bakery", "bagel", "breakfast", "brunch"))
    fast = any(token in searchable for token in ("fast_food", "fast food", "sandwich", "deli"))
    if meal_type == "breakfast":
        return 1.0 if cafe else 0.72 if fast else 0.35
    if meal_type == "lunch":
        return 0.95 if cafe or fast else 0.85
    return 0.25 if cafe else 0.72 if fast else 1.0


def _open_for_meal(place: Place, meal_type: str) -> bool:
    raw = (place.opening_hours or {}).get("raw")
    if not raw:
        return True
    start = {"breakfast": 8 * 60, "lunch": 12 * 60 + 30, "dinner": 18 * 60 + 30}[meal_type]
    opens, closes = _opening_window(raw, "food")
    return opens <= start and start + 60 <= closes


def _rank_restaurants(
    restaurants: list[Place], center: tuple[float, float] | None, meal_type: str, used: Counter, requirements: set[str]
) -> list[Place]:
    eligible = [
        place for place in restaurants if _supports_diet(place, requirements) and _open_for_meal(place, meal_type)
    ]
    if not eligible and requirements:
        return []

    def score(place: Place) -> float:
        distance = distance_km(center[0], center[1], place.latitude, place.longitude) if center else 0
        metadata = place.source_metadata or {}
        tags = metadata.get("osm_tags", {})
        local_relevance = 1 if tags.get("cuisine") else 0.45
        quality = min(1, float(metadata.get("ranking_score", place.rating or 3.5)) / 5)
        price_fit = 1 / max(1, place.price_level or 1)
        diversity = 1 / (1 + used[place.id])
        geographic_fit = 1 / (1 + distance)
        dietary = 1 if _supports_diet(place, requirements) else 0
        return (
            0.20 * _meal_type_fit(place, meal_type)
            + 0.15 * dietary
            + 0.13 * geographic_fit
            + 0.12 * local_relevance
            + 0.10 * quality
            + 0.10 * price_fit
            + 0.08
            + 0.05 * 0.8
            + 0.04 * diversity
            + 0.03 * (1 if place.reservation_url else 0.5)
        )

    return sorted(eligible, key=lambda place: (-score(place), place.name))


def restaurant_candidates(
    db: Session, trip: Trip, center: tuple[float, float] | None = None, limit: int = 100
) -> list[Place]:
    provider = get_place_provider(db)
    query = PlaceSearch(
        city=trip.destination_city,
        category="food",
        limit=limit,
        latitude=center[0] if center else None,
        longitude=center[1] if center else None,
        radius_km=4 if center else None,
    )
    candidates = provider.search(query)
    if not candidates and center:
        candidates = provider.search(PlaceSearch(city=trip.destination_city, category="food", limit=limit))
    # Keep broad candidate ranking on the cached provider records. Enrichment can
    # be billable (Google Places) and is applied only to the selected restaurants
    # or to the small alternatives list a traveler explicitly opens.
    return candidates


def build_meal_specs(
    db: Session, trip: Trip, scheduled_days: list[dict], preferred_restaurants: list[Place] | None = None
) -> list[dict]:
    requirements = {
        item.strip() for traveler in trip.travelers for item in traveler.dietary_restrictions if item.strip()
    }
    retrieved = restaurant_candidates(db, trip)
    all_restaurants = list({place.id: place for place in [*(preferred_restaurants or []), *retrieved]}.values())
    if not all_restaurants:
        raise ValueError(f"No exact restaurant records are available for {trip.destination_city}")
    used: Counter = Counter()
    provider = get_place_provider(db)
    specs = []
    default_starts = {"breakfast": 8 * 60, "lunch": 12 * 60 + 30, "dinner": 18 * 60 + 30}
    for day_number, day in enumerate(scheduled_days, start=1):
        day_places = [scheduled.place for scheduled in day["schedule"]]
        center = (
            (
                sum(place.latitude for place in day_places) / len(day_places),
                sum(place.longitude for place in day_places) / len(day_places),
            )
            if day_places
            else None
        )
        local_candidates = [
            place
            for place in all_restaurants
            if not center or distance_km(center[0], center[1], place.latitude, place.longitude) <= 5
        ] or all_restaurants
        for meal_type in ("breakfast", "lunch", "dinner"):
            ranked = _rank_restaurants(local_candidates, center, meal_type, used, requirements)
            if not ranked:
                raise ValueError(
                    f"No restaurant with explicit support for {', '.join(sorted(requirements))} was found near Day {day_number}'s route"
                )
            selected = ranked[0]
            selected = provider.enrich(selected)
            used[selected.id] += 1
            raw_min, raw_expected, raw_max, price_type, source = _meal_raw(selected, trip, meal_type)
            start = default_starts[meal_type]
            specs.append(
                {
                    "day_number": day_number,
                    "meal_type": meal_type,
                    "place": selected,
                    "preferred_start": start,
                    "duration": 60,
                    "raw_min_per_person": raw_min,
                    "raw_expected_per_person": raw_expected,
                    "raw_max_per_person": raw_max,
                    "price_type": price_type,
                    "source": source,
                    "reason_selected": (
                        f"Chosen near Day {day_number}'s activity cluster to reduce detours; it fits the {meal_type} window"
                        + (
                            f" and the group's {', '.join(sorted(requirements))} requirement."
                            if requirements
                            else " and the selected plan's meal budget."
                        )
                    ),
                }
            )
    return specs


def persist_meals(
    db: Session, plan: PlanVariant, trip: Trip, specs: list[dict], food_target: float
) -> tuple[list[Meal], float]:
    db.execute(delete(Meal).where(Meal.plan_id == plan.id))
    raw_total = sum(spec["raw_expected_per_person"] * trip.traveler_count for spec in specs)
    normalization = food_target / raw_total if raw_total > 0 and food_target > 0 else 1.0
    meals = []
    for spec in specs:
        start = int(spec["start_minute"])
        end = int(spec["end_minute"])
        minimum = round(spec["raw_min_per_person"] * normalization, 2)
        expected = round(spec["raw_expected_per_person"] * normalization, 2)
        maximum = max(expected, round(spec["raw_max_per_person"] * normalization, 2))
        assumptions = [
            "Restaurant amount is an estimate unless its source quality says LIVE or PUBLISHED.",
            "Taxes, tips, service charges, and reservation fees are not included unless separately listed.",
            f"Meal estimates were aligned by a {normalization:.4f} factor to the selected plan's food budget while preserving restaurant price differences.",
        ]
        meal = Meal(
            plan_id=plan.id,
            day_number=spec["day_number"],
            meal_type=spec["meal_type"],
            restaurant_place_id=spec["place"].id,
            start_time=_time_from_minute(start),
            end_time=_time_from_minute(end),
            traveler_count=trip.traveler_count,
            min_cost_per_person=minimum,
            expected_cost_per_person=expected,
            max_cost_per_person=maximum,
            expected_total=round(expected * trip.traveler_count, 2),
            reason_selected=spec["reason_selected"],
            assumptions=assumptions,
            source_quality=spec["price_type"],
        )
        db.add(meal)
        meals.append(meal)
    db.flush()
    return meals, normalization


def serialize_meal(meal: Meal, context: dict | None = None) -> dict:
    place = meal.restaurant
    start_minute = meal.start_time.hour * 60 + meal.start_time.minute
    end_minute = meal.end_time.hour * 60 + meal.end_time.minute
    metadata = place.source_metadata or {}
    tags = metadata.get("osm_tags", {})
    raw_hours = (place.opening_hours or {}).get("raw")
    opens, closes = _opening_window(raw_hours, "food")
    opening_feasible = not raw_hours or opens <= start_minute and end_minute <= closes
    return {
        "id": meal.id,
        "item_id": f"meal-{meal.id}",
        "day_number": meal.day_number,
        "meal_type": meal.meal_type,
        "category": "food",
        "subcategory": meal.meal_type,
        "restaurant_id": place.id,
        "restaurant_name": place.name,
        "name": place.name,
        "cuisine": [item for item in str(tags.get("cuisine") or place.subcategory).split(";") if item],
        "neighborhood": place.neighborhood,
        "borough_or_district": place.borough_or_district,
        "address": place.address,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "photo_url_or_reference": place.image_url or "/placeholders/food.svg",
        "photo_source": "wikimedia" if place.image_url else "category_placeholder",
        "photo_source_url": place.image_source,
        "photo_attribution": place.image_attribution or "TripPilot placeholder",
        "start_time": meal.start_time.strftime("%H:%M"),
        "end_time": meal.end_time.strftime("%H:%M"),
        "start_minute": start_minute,
        "end_minute": end_minute,
        "duration_minutes": end_minute - start_minute,
        "traveler_count": meal.traveler_count,
        "min_cost_per_person": float(meal.min_cost_per_person),
        "expected_cost_per_person": float(meal.expected_cost_per_person),
        "max_cost_per_person": float(meal.max_cost_per_person),
        "expected_total": float(meal.expected_total),
        "reason_selected": meal.reason_selected,
        "assumptions": meal.assumptions,
        "source_quality": meal.source_quality,
        "maps_url": google_maps_place_url(place.name, place.address, place.latitude, place.longitude),
        "menu_url": tags.get("menu") or tags.get("contact:menu"),
        "official_url": place.official_url,
        "reservation_url": place.reservation_url,
        "opening_hours": raw_hours,
        "opening_feasible": opening_feasible,
        "rating": place.rating if place.rating > 0 else None,
        "review_count": place.review_count,
        "rating_source": place.rating_source,
        "phone_number": place.phone_number,
        "phone_source_url": place.phone_source_url,
        "context": context,
    }


def meal_alternatives(db: Session, plan_id: int, day_number: int, meal_type: str) -> list[dict]:
    meal = db.scalar(
        select(Meal).where(Meal.plan_id == plan_id, Meal.day_number == day_number, Meal.meal_type == meal_type)
    )
    plan = db.get(PlanVariant, plan_id)
    if not meal or not plan:
        raise LookupError("meal was not found")
    trip = db.get(Trip, plan.trip_id)
    current = meal.restaurant
    candidates = restaurant_candidates(db, trip, (current.latitude, current.longitude))
    ranked = [
        place
        for place in _rank_restaurants(candidates, (current.latitude, current.longitude), meal_type, Counter(), set())
        if place.id != current.id
    ]
    current_expense = db.scalar(
        select(ExpenseLineItem).where(
            ExpenseLineItem.plan_id == plan_id, ExpenseLineItem.itinerary_item_id == f"meal-{meal.id}"
        )
    )
    factor = float((current_expense.metadata_json or {}).get("normalization_factor", 1)) if current_expense else 1
    alternatives = []
    provider = get_place_provider(db)
    for place in (provider.enrich(candidate) for candidate in ranked[:5]):
        raw_min, raw_expected, raw_max, quality, source = _meal_raw(place, trip, meal_type)
        expected_pp = round(raw_expected * factor, 2)
        total = round(expected_pp * trip.traveler_count, 2)
        context = serialize_place_context(get_or_create_place_context(db, place))
        alternatives.append(
            {
                "restaurant_id": place.id,
                "name": place.name,
                "cuisine": place.tags,
                "neighborhood": place.neighborhood,
                "distance_km": round(
                    distance_km(current.latitude, current.longitude, place.latitude, place.longitude), 2
                ),
                "expected_cost_per_person": expected_pp,
                "group_total": total,
                "cost_delta": round(total - float(meal.expected_total), 2),
                "source_quality": quality,
                "photo_url": place.image_url or "/placeholders/food.svg",
                "maps_url": place.maps_url,
                "official_url": place.official_url,
                "reservation_url": place.reservation_url,
                "why_selected": "Nearby alternative ranked for route fit, estimated meal price, and restaurant variety.",
                "context": context,
            }
        )
    return alternatives


def replace_meal(db: Session, plan_id: int, day_number: int, meal_type: str, restaurant_id: int) -> dict:
    meal = db.scalar(
        select(Meal).where(Meal.plan_id == plan_id, Meal.day_number == day_number, Meal.meal_type == meal_type)
    )
    plan = db.get(PlanVariant, plan_id)
    restaurant = db.get(Place, restaurant_id)
    if not meal or not plan or not restaurant or restaurant.category != "food":
        raise LookupError("meal or replacement restaurant was not found")
    allowed = {item["restaurant_id"] for item in meal_alternatives(db, plan_id, day_number, meal_type)}
    if restaurant_id not in allowed:
        raise ValueError("replacement must be one of the nearby ranked alternatives")
    trip = db.get(Trip, plan.trip_id)
    expense = db.scalar(
        select(ExpenseLineItem).where(
            ExpenseLineItem.plan_id == plan_id, ExpenseLineItem.itinerary_item_id == f"meal-{meal.id}"
        )
    )
    if not expense:
        raise LookupError("meal expense was not found")
    factor = float((expense.metadata_json or {}).get("normalization_factor", 1))
    raw_min, raw_expected, raw_max, quality, source = _meal_raw(restaurant, trip, meal_type)
    minimum, expected, maximum = [round(value * factor, 2) for value in (raw_min, raw_expected, raw_max)]
    old_total = float(meal.expected_total)
    meal.restaurant_place_id = restaurant.id
    meal.restaurant = restaurant
    meal.min_cost_per_person, meal.expected_cost_per_person, meal.max_cost_per_person = (
        minimum,
        expected,
        max(expected, maximum),
    )
    meal.expected_total = round(expected * trip.traveler_count, 2)
    meal.reason_selected = f"Replaced with a nearby {meal_type} option that changes the group meal estimate by {float(meal.expected_total) - old_total:+.2f} {trip.currency}."
    meal.source_quality = quality
    expense.title = f"{meal_type.title()} · {restaurant.name}"
    expense.description = f"Estimated {meal_type} for {trip.traveler_count} travelers in {restaurant.neighborhood}."
    expense.unit_cost_min, expense.unit_cost_expected, expense.unit_cost_max = minimum, expected, max(expected, maximum)
    expense.subtotal_min = expense.total_min = round(minimum * trip.traveler_count, 2)
    expense.subtotal_expected = expense.total_expected = round(expected * trip.traveler_count, 2)
    expense.subtotal_max = expense.total_max = round(max(expected, maximum) * trip.traveler_count, 2)
    expense.calculation_expression = f"{trip.traveler_count} people × {trip.currency} {expected:,.2f} = {trip.currency} {float(expense.total_expected):,.2f}"
    expense.source_type = expense.source_quality = quality
    expense.source_reference = source
    expense.metadata_json = {
        **(expense.metadata_json or {}),
        "restaurant_id": restaurant.id,
        "raw_expected_per_person": raw_expected,
    }
    db.flush()
    rows = list(db.scalars(select(ExpenseLineItem).where(ExpenseLineItem.plan_id == plan_id)))
    reconciliation = reconcile_expenses(rows)
    db.query(BudgetSnapshot).filter(BudgetSnapshot.plan_id == plan_id).delete()
    db.add(
        BudgetSnapshot(
            plan_id=plan_id,
            expected_total=reconciliation["expected_total"],
            category_totals=reconciliation["category_totals"],
            reconciliation=reconciliation,
        )
    )
    context = serialize_place_context(get_or_create_place_context(db, restaurant))
    serialized = serialize_meal(meal, context)
    record = db.scalar(select(PlanDetail).where(PlanDetail.plan_id == plan_id))
    if record:
        detail = dict(record.detail)
        for day in detail.get("days", []):
            if day["day_number"] != day_number:
                continue
            day["meals"] = [serialized if row["meal_type"] == meal_type else row for row in day.get("meals", [])]
            day["timeline"] = [
                serialized if row.get("item_id") == f"meal-{meal.id}" else row for row in day.get("timeline", [])
            ]
        dashboard = expense_dashboard(db, plan_id)
        detail["expenses"] = dashboard
        detail["budget"]["expected"] = dashboard["expected_total"]
        detail["budget"]["expected_per_person"] = round(dashboard["expected_total"] / trip.traveler_count, 2)
        detail["budget"]["activity_categories"] = reconciliation["category_totals"]
        detail["budget"]["reconciliation"] = reconciliation
        detail["budget"]["status"] = (
            "WITHIN_BUDGET" if dashboard["expected_total"] <= float(trip.total_budget) else "OVER_BUDGET"
        )
        record.detail = detail
        from backend.repositories.itineraries import persist_detail

        persist_detail(db, plan, detail)
    db.commit()
    return {
        "meal": serialized,
        "savings": round(old_total - float(meal.expected_total), 2),
        "detail": record.detail if record else None,
    }
