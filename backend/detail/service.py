from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.context import get_or_create_place_context, serialize_place_context
from backend.expenses.service import expense_dashboard, make_expense, persist_expenses
from backend.geography.maps import google_maps_directions_url, google_maps_place_url
from backend.itinerary.itinerary_planner import build_daily_schedule
from backend.meals.service import build_meal_specs, persist_meals, serialize_meal
from backend.models import Meal, Place, PlanDetail, PlanVariant, Traveler, Trip, TripStateRecord
from backend.optimization.plan_profiles import PLAN_PROFILES
from backend.preference.aggregator import preference_for_place, traveler_preference_maps
from backend.pricing.estimator import estimate_place_price
from backend.providers import get_place_provider
from backend.providers.mock import place_to_dict
from backend.transport import choose_transport_leg
from backend.validation.itinerary_validator import validate_itinerary

STAGE_HISTORY = [
    "SELECTED",
    "RESOLVING_AREAS",
    "RESOLVING_EXACT_PLACES",
    "FINDING_LOCAL_MEALS",
    "RETRIEVING_PLACE_STORIES",
    "FETCHING_PHOTOS",
    "FETCHING_LINKS",
    "FETCHING_PRICES",
    "FETCHING_OPENING_HOURS",
    "BUILDING_ROUTES",
    "OPTIMIZING_SCHEDULE",
    "RECONCILING_BUDGET",
    "VALIDATING",
    "READY",
]

PHOTO_PLACEHOLDERS = {
    "museums": "/placeholders/museums.svg",
    "nightlife": "/placeholders/nightlife.svg",
    "food": "/placeholders/food.svg",
    "nature": "/placeholders/nature.svg",
    "landmarks": "/placeholders/landmarks.svg",
    "entertainment": "/placeholders/entertainment.svg",
}


def _minute_time(value: int) -> str:
    return f"{value // 60 % 24:02d}:{value % 60:02d}"


def _route_day(scheduled_day: dict, meal_specs: list[dict], trip: Trip) -> list[dict]:
    """Insert meals before final timing so they participate in travel and feasibility."""
    activities = list(scheduled_day["schedule"])
    breakfast = next(spec for spec in meal_specs if spec["meal_type"] == "breakfast")
    lunch = next(spec for spec in meal_specs if spec["meal_type"] == "lunch")
    dinner = next(spec for spec in meal_specs if spec["meal_type"] == "dinner")
    morning = [row for row in activities if row.end_minute <= 12 * 60 + 15]
    evening = [row for row in activities if row.start_minute >= 17 * 60 + 30]
    afternoon = [row for row in activities if row not in morning and row not in evening]

    def assemble() -> list[dict]:
        return [
            {
                "kind": "meal",
                "spec": breakfast,
                "place": breakfast["place"],
                "preferred": breakfast["preferred_start"],
                "duration": breakfast["duration"],
            },
            *[
                {
                    "kind": "activity",
                    "scheduled": row,
                    "place": row.place,
                    "preferred": row.start_minute,
                    "duration": row.duration_expected,
                }
                for row in morning
            ],
            {
                "kind": "meal",
                "spec": lunch,
                "place": lunch["place"],
                "preferred": lunch["preferred_start"],
                "duration": lunch["duration"],
            },
            *[
                {
                    "kind": "activity",
                    "scheduled": row,
                    "place": row.place,
                    "preferred": row.start_minute,
                    "duration": row.duration_expected,
                }
                for row in afternoon
            ],
            {
                "kind": "meal",
                "spec": dinner,
                "place": dinner["place"],
                "preferred": dinner["preferred_start"],
                "duration": dinner["duration"],
            },
            *[
                {
                    "kind": "activity",
                    "scheduled": row,
                    "place": row.place,
                    "preferred": row.start_minute,
                    "duration": row.duration_expected,
                }
                for row in evening
            ],
        ]

    def route(sequence: list[dict]) -> list[dict]:
        cursor = 7 * 60
        previous = None
        for index, entry in enumerate(sequence):
            transport_details = choose_transport_leg(previous, entry["place"], trip) if previous else None
            travel = transport_details["duration_minutes"] if transport_details else 0
            break_minutes = 10 if index else 0
            earliest = entry["preferred"]
            if entry["kind"] == "meal":
                earliest = max(
                    earliest,
                    {"breakfast": 7 * 60, "lunch": 11 * 60 + 30, "dinner": 17 * 60 + 30}[entry["spec"]["meal_type"]],
                )
            start = max(earliest, cursor + travel + break_minutes)
            end = start + entry["duration"]
            entry.update(
                {
                    "start_minute": start,
                    "end_minute": end,
                    "travel_minutes": travel,
                    "break_minutes": break_minutes,
                    "previous_place": previous,
                    "transport_details": transport_details,
                }
            )
            cursor, previous = end, entry["place"]
        return sequence

    sequence = route(assemble())
    dinner_entry = next(
        entry for entry in sequence if entry["kind"] == "meal" and entry["spec"]["meal_type"] == "dinner"
    )
    while dinner_entry["start_minute"] > 21 * 60 + 30 and afternoon:
        evening.insert(0, afternoon.pop())
        sequence = route(assemble())
        dinner_entry = next(
            entry for entry in sequence if entry["kind"] == "meal" and entry["spec"]["meal_type"] == "dinner"
        )
    for entry in sequence:
        if entry["kind"] == "meal":
            entry["spec"].update(
                {
                    "start_minute": entry["start_minute"],
                    "end_minute": entry["end_minute"],
                    "travel_minutes": entry["travel_minutes"],
                    "previous_place": entry["previous_place"],
                    "transport_details": entry["transport_details"],
                }
            )
    return sequence


def _allocate(total: float, weighted: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
    if not weighted:
        return []
    weight_total = sum(max(weight, 0) for _, weight in weighted) or len(weighted)
    allocated, used = [], 0.0
    for index, (item, weight) in enumerate(weighted):
        value = (
            round(total - used, 2)
            if index == len(weighted) - 1
            else round(total * (max(weight, 0) or 1) / weight_total, 2)
        )
        allocated.append((item, value))
        used += value
    return allocated


def _expense_rows(plan: PlanVariant, trip: Trip, days: list[dict], meals: list[Meal], normalization: float) -> list:
    breakdown = plan.summary.get("cost_breakdown") or {}
    targets = {category: float(row.get("expected", 0)) for category, row in breakdown.items()}
    rows = []
    meal_by_id = {meal.id: meal for meal in meals}
    for day in days:
        for meal_data in day["meals"]:
            meal = meal_by_id[meal_data["id"]]
            rows.append(
                make_expense(
                    plan_id=plan.id,
                    trip_id=trip.id,
                    category="food",
                    subcategory=meal.meal_type,
                    day_number=meal.day_number,
                    itinerary_item_id=f"meal-{meal.id}",
                    title=f"{meal.meal_type.title()} · {meal.restaurant.name}",
                    description=f"Estimated {meal.meal_type} for {trip.traveler_count} travelers in {meal.restaurant.neighborhood}.",
                    currency=trip.currency,
                    quantity=trip.traveler_count,
                    unit="person",
                    minimum=float(meal.min_cost_per_person) * trip.traveler_count,
                    expected=float(meal.expected_total),
                    maximum=float(meal.max_cost_per_person) * trip.traveler_count,
                    shared_or_personal="PER_PERSON",
                    traveler_count=trip.traveler_count,
                    assumptions=meal.assumptions,
                    source_type=meal.source_quality,
                    source_reference=meal.restaurant.cost_source,
                    source_quality=meal.source_quality,
                    metadata={
                        "meal_id": meal.id,
                        "restaurant_id": meal.restaurant_place_id,
                        "normalization_factor": normalization,
                    },
                )
            )

    activity_items = [item for day in days for item in day["items"] if item["category"] not in {"food", "nightlife"}]
    for item, expected in _allocate(
        targets.get("activities", targets.get("attraction", 0)),
        [(item, item["group_cost_expected"] or 1) for item in activity_items],
    ):
        ratio_min = (item["group_cost_min"] or expected) / max(item["group_cost_expected"] or expected, 0.01)
        ratio_max = (item["group_cost_max"] or expected) / max(item["group_cost_expected"] or expected, 0.01)
        rows.append(
            make_expense(
                plan_id=plan.id,
                trip_id=trip.id,
                category=item["category"],
                subcategory=item["subcategory"],
                day_number=item["day_number"],
                itinerary_item_id=item["item_id"],
                title=item["name"],
                description=f"Admission or activity estimate for {trip.traveler_count} travelers.",
                currency=trip.currency,
                quantity=trip.traveler_count,
                unit="person",
                minimum=round(expected * ratio_min, 2),
                expected=expected,
                maximum=round(expected * ratio_max, 2),
                shared_or_personal="PER_PERSON",
                traveler_count=trip.traveler_count,
                assumptions=[
                    "This category is aligned to the selected plan ledger; exact published prices remain unmodified when available."
                ],
                source_type=item["cost_status"],
                source_reference=item["cost_source"],
                source_quality=item["cost_status"],
            )
        )

    nightlife_items = [item for day in days for item in day["items"] if item["category"] == "nightlife"]
    for item, expected in _allocate(
        targets.get("nightlife", 0), [(item, item["group_cost_expected"] or 1) for item in nightlife_items]
    ):
        rows.append(
            make_expense(
                plan_id=plan.id,
                trip_id=trip.id,
                category="nightlife",
                subcategory=item["subcategory"],
                day_number=item["day_number"],
                itinerary_item_id=item["item_id"],
                title=item["name"],
                description="Estimated evening spend; cover, food, and drinks may vary.",
                currency=trip.currency,
                expected=expected,
                minimum=expected * 0.65,
                maximum=expected * 1.55,
                quantity=trip.traveler_count,
                unit="person",
                shared_or_personal="PER_PERSON",
                traveler_count=trip.traveler_count,
                assumptions=["Nightlife spend is a range, not a guaranteed price."],
                source_type="ESTIMATED",
                source_quality="ESTIMATED",
            )
        )

    for day in days:
        timeline = day["timeline"]
        for previous, current in zip(timeline, timeline[1:]):
            details = current.get("transport_details") or {}
            selected = next(
                (option for option in details.get("options", []) if option["mode"] == details.get("selected_mode")),
                None,
            )
            if not selected:
                continue
            expected = float(selected["group_cost"])
            selected_mode = str(selected["mode"])
            if selected_mode == "public transit":
                quantity, unit, sharing = trip.traveler_count, "traveler", "PER_PERSON"
            elif selected_mode == "cab":
                quantity, unit, sharing = max(1, int(selected.get("vehicles_required") or 1)), "cab", "SHARED"
            else:
                quantity, unit, sharing = 1, "group leg", "SHARED"
            rows.append(
                make_expense(
                    plan_id=plan.id,
                    trip_id=trip.id,
                    category="transport",
                    subcategory=selected_mode,
                    day_number=day["day_number"],
                    itinerary_item_id=current["item_id"],
                    title=f"{previous['name']} → {current['name']}",
                    description=f"{details['selection_reason']} Estimated travel time: {selected['duration_minutes']} minutes.",
                    currency=trip.currency,
                    expected=expected,
                    minimum=float(selected["minimum_group_cost"]),
                    maximum=float(selected["maximum_group_cost"]),
                    quantity=quantity,
                    unit=unit,
                    shared_or_personal=sharing,
                    traveler_count=trip.traveler_count,
                    assumptions=[
                        "The cheapest allowed mode is selected using whole-group cost, not the headline per-person fare.",
                        "Cab amounts are planning estimates and can change with traffic, surge pricing, tolls, tips, and vehicle size.",
                        "Transit is a live provider fare only when source quality explicitly says GOOGLE LIVE FARE.",
                    ],
                    source_type=selected["source_quality"],
                    source_reference=selected["source_reference"],
                    source_quality=selected["source_quality"],
                    metadata={
                        "selection_reason": details["selection_reason"],
                        "options": details["options"],
                        "distance_km": details["distance_km"],
                    },
                )
            )

    handled = {"food", "activities", "attraction", "nightlife", "transport"}
    ledger_by_category = {row["category"]: row for row in plan.summary.get("cost_ledger", [])}
    for category, expected in targets.items():
        if category in handled:
            continue
        ledger = ledger_by_category.get(category, {})
        minimum = float(ledger.get("minimum") if ledger.get("minimum") is not None else expected)
        maximum = float(ledger.get("maximum") if ledger.get("maximum") is not None else expected)
        metadata = ledger.get("metadata") or {}
        if category == "hotel":
            quantity, unit = max(1, (trip.end_date - trip.start_date).days), "night"
            title = "Hotel stay estimate"
            description = f"{metadata.get('rooms', max(1, (trip.traveler_count + 1) // 2))} room(s) for {quantity} night(s); property not booked yet."
        elif category == "flight":
            quantity, unit = trip.traveler_count, "adult"
            title = f"Round-trip flights · {trip.origin} → {trip.destination_city}"
            description = "Live fare when a provider offer is connected; otherwise this is the user's flight allowance."
        else:
            quantity, unit = 1, "group allowance"
            title = {"shopping": "Optional shopping allowance", "buffer": "Emergency cushion"}.get(
                category, category.replace("_", " ").title()
            )
            description = "Plan-level allowance shown separately from confirmed purchases."
        rows.append(
            make_expense(
                plan_id=plan.id,
                trip_id=trip.id,
                category=category,
                title=title,
                description=description,
                currency=trip.currency,
                expected=expected,
                minimum=minimum,
                maximum=maximum,
                quantity=quantity,
                unit=unit,
                shared_or_personal=ledger.get("shared_or_personal", "SHARED"),
                traveler_count=trip.traveler_count,
                assumptions=[
                    "Taxes and fees are included only when the provider or estimate explicitly supplies them."
                ],
                source_type=ledger.get("price_type", "USER_ALLOWANCE"),
                source_reference=ledger.get("source"),
                source_quality=ledger.get("price_type", "USER_ALLOWANCE"),
                metadata=metadata,
            )
        )
    return rows


def generate_plan_detail(db: Session, plan_id: int) -> dict:
    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise LookupError("plan not found")
    if plan.status != "selected":
        raise ValueError("select the plan before generating its detail")
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == plan.trip_id)
        .options(selectinload(Trip.travelers).selectinload(Traveler.preferences), selectinload(Trip.budget))
    )
    external_ids = plan.summary.get("place_ids", [])
    places_by_external_id = {
        place.external_place_id: place
        for place in db.scalars(select(Place).where(Place.external_place_id.in_(external_ids)))
    }
    places = [
        places_by_external_id[external_id] for external_id in external_ids if external_id in places_by_external_id
    ]
    if len(places) != len(external_ids):
        raise ValueError("one or more selected places are unavailable from the provider")
    provider = get_place_provider(db)
    places = [provider.enrich(place) for place in places]
    preference_maps = traveler_preference_maps(trip.travelers)
    state_record = db.scalar(select(TripStateRecord).where(TripStateRecord.trip_id == trip.id))
    state = state_record.state if state_record else {}
    interests = {
        **{
            category: sum(preferences.get((category, "general"), 5) / 10 for preferences in preference_maps.values())
            / max(len(preference_maps), 1)
            for category in {key[0] for preferences in preference_maps.values() for key in preferences}
        },
        **{key: float(value) for key, value in state.get("broad_preferences", {}).items()},
    }
    profile = next((item for item in PLAN_PROFILES if item.key == plan.plan_type), PLAN_PROFILES[0])
    preferred_restaurants = [place for place in places if place.category == "food"]
    activity_places = [place for place in places if place.category != "food"]
    scheduled_days = build_daily_schedule(
        activity_places,
        trip,
        profile,
        interests,
        preferred_start=state.get("preferred_start_time"),
        preferred_end=state.get("preferred_end_time"),
    )
    meal_specs = build_meal_specs(db, trip, scheduled_days, preferred_restaurants)
    days = []
    for day_index, scheduled_day in enumerate(scheduled_days):
        trip_date = scheduled_day["date"]
        day_number = day_index + 1
        day_meal_specs = [spec for spec in meal_specs if spec["day_number"] == day_number]
        routed = _route_day(scheduled_day, day_meal_specs, trip)
        previous_activity = None
        items = []
        for route_position, entry in enumerate(routed):
            if entry["kind"] != "activity":
                continue
            scheduled = entry["scheduled"]
            place = scheduled.place
            travel_minutes = entry["travel_minutes"]
            transport_details = entry.get("transport_details")
            # Preserve the activity-to-activity link used by existing consumers;
            # transport_details carries the exact adjacent timeline leg (including meals).
            directions_url = (
                google_maps_directions_url(
                    previous_activity.latitude,
                    previous_activity.longitude,
                    place.latitude,
                    place.longitude,
                    (transport_details or {}).get("selected_mode", trip.transport_mode),
                )
                if previous_activity
                else None
            )
            place_dict = place_to_dict(place)
            individual_matches = [
                {
                    "traveler_id": traveler.id,
                    "traveler_name": traveler.display_name,
                    "score": round(preference_for_place(preference_maps.get(traveler.id, {}), place_dict), 4),
                }
                for traveler in trip.travelers
            ]
            group_match = mean(match["score"] for match in individual_matches) if individual_matches else 0.5
            photo_url = place.image_url or PHOTO_PLACEHOLDERS.get(place.category, "/placeholders/default.svg")
            live_place = place.external_provider == "openstreetmap"
            source_metadata = place.source_metadata or {}
            source_currency = place.cost_currency.upper()
            price = estimate_place_price(place_dict, trip.currency, trip.destination_city)
            unit_min, unit_expected, unit_max = price.minimum, price.expected, price.maximum
            display_status = price.price_type.value
            group_min = None if unit_min is None else round(unit_min * trip.traveler_count, 2)
            group_expected = None if unit_expected is None else round(unit_expected * trip.traveler_count, 2)
            group_max = None if unit_max is None else round(unit_max * trip.traveler_count, 2)
            context = serialize_place_context(get_or_create_place_context(db, place))
            items.append(
                {
                    "item_id": f"detail-{plan.id}-{place.id}",
                    "place_id": place.id,
                    "external_place_id": place.external_place_id,
                    "day_number": day_number,
                    "route_position": route_position,
                    "photo_url_or_reference": photo_url,
                    "photo_source": "wikimedia" if place.image_url else "category_placeholder",
                    "photo_source_url": place.image_source,
                    "photo_attribution": place.image_attribution or "TripPilot placeholder",
                    "photo_width": 1000 if place.image_url else 800,
                    "photo_height": 650 if place.image_url else 500,
                    "name": place.name,
                    "category": place.category,
                    "subcategory": place.subcategory,
                    "city": place.city,
                    "borough_or_district": place.borough_or_district,
                    "neighborhood": place.neighborhood,
                    "address": place.address,
                    "start_time": _minute_time(entry["start_minute"]),
                    "end_time": _minute_time(entry["end_minute"]),
                    "start_minute": entry["start_minute"],
                    "end_minute": entry["end_minute"],
                    "duration_minutes": scheduled.duration_expected,
                    "duration_min": scheduled.duration_min,
                    "duration_expected": scheduled.duration_expected,
                    "duration_max": scheduled.duration_max,
                    "best_time_of_day": scheduled.best_time_of_day,
                    "cost_min": None if unit_min is None else round(unit_min, 2),
                    "cost_expected": None if unit_expected is None else round(unit_expected, 2),
                    "cost_max": None if unit_max is None else round(unit_max, 2),
                    "group_cost_min": group_min,
                    "group_cost_expected": group_expected,
                    "group_cost_max": group_max,
                    "traveler_count": trip.traveler_count,
                    "published_cost": source_metadata.get("published_price", float(place.cost_expected)),
                    "published_currency": source_metadata.get("published_currency", source_currency),
                    "cost_status": display_status,
                    "cost_source": price.source or place.cost_source,
                    "cost_currency": trip.currency,
                    "group_match": round(group_match, 4),
                    "individual_matches": individual_matches,
                    "travel_minutes_from_previous": travel_minutes,
                    "break_minutes_after_previous": entry["break_minutes"],
                    "transport_mode": transport_details.get("selected_mode")
                    if transport_details
                    else trip.transport_mode,
                    "transport_details": transport_details,
                    "opening_feasible": scheduled.opening_feasible,
                    "opening_status": (
                        "OPEN_AT_PLANNED_TIME"
                        if place.opening_hours.get("raw") and scheduled.opening_feasible
                        else "PROVIDER_HOURS_CONFLICT"
                        if place.opening_hours.get("raw")
                        else "UNKNOWN_PROVIDER_HOURS"
                    ),
                    "opening_hours": place.opening_hours.get("raw"),
                    "why_selected": (
                        f"Selected from live OpenStreetMap data for its {round(group_match * 100)}% group match and fit in {place.neighborhood}."
                        if live_place
                        else f"Selected from provider data for its {round(group_match * 100)}% group match, {place.rating}/5 rating, and fit in {place.neighborhood}."
                    ),
                    "maps_url": google_maps_place_url(place.name, place.address, place.latitude, place.longitude),
                    "directions_url": directions_url,
                    "official_url": place.official_url,
                    "ticket_url": place.ticket_url,
                    "reservation_url": place.reservation_url,
                    "rating": place.rating if place.rating > 0 else None,
                    "review_count": place.review_count,
                    "rating_source": place.rating_source,
                    "phone_number": place.phone_number,
                    "phone_source_url": place.phone_source_url,
                    "is_locked": False,
                    "context": context,
                }
            )
            previous_activity = place
        neighborhoods = list(dict.fromkeys(item["neighborhood"] for item in items))
        days.append(
            {
                "day_number": day_number,
                "date": trip_date.isoformat(),
                "region_summary": " + ".join(neighborhoods) if neighborhoods else "City route",
                "hotel_departure_time": _minute_time(routed[0]["start_minute"]),
                "finish_time": _minute_time(routed[-1]["end_minute"]),
                "hotel_departure_minute": routed[0]["start_minute"],
                "finish_minute": routed[-1]["end_minute"],
                "cost_min": round(sum(item["group_cost_min"] or 0 for item in items), 2),
                "cost_expected": round(sum(item["group_cost_expected"] or 0 for item in items), 2),
                "cost_max": round(sum(item["group_cost_max"] or 0 for item in items), 2),
                "travel_minutes": sum(entry["travel_minutes"] for entry in routed),
                "walking_intensity": plan.walking_intensity,
                "items": items,
                "meals": [],
                "timeline": [],
            }
        )

    food_target = float((plan.summary.get("cost_breakdown") or {}).get("food", {}).get("expected", 0))
    meals, normalization = persist_meals(db, plan, trip, meal_specs, food_target)
    meal_specs_by_key = {(spec["day_number"], spec["meal_type"]): spec for spec in meal_specs}
    for meal in meals:
        context = serialize_place_context(get_or_create_place_context(db, meal.restaurant))
        meal_data = serialize_meal(meal, context)
        spec = meal_specs_by_key[(meal.day_number, meal.meal_type)]
        meal_data.update(
            {
                "route_position": spec["start_minute"],
                "travel_minutes_from_previous": spec.get("travel_minutes", 0),
                "break_minutes_after_previous": 10,
                "transport_mode": (spec.get("transport_details") or {}).get("selected_mode", trip.transport_mode),
                "transport_details": spec.get("transport_details"),
                "directions_url": (spec.get("transport_details") or {}).get("directions_url"),
                "why_selected": meal.reason_selected,
            }
        )
        day = days[meal.day_number - 1]
        day["meals"].append(meal_data)
    for day in days:
        day["timeline"] = sorted([*day["items"], *day["meals"]], key=lambda item: item["start_minute"])
        day["expense_total_expected"] = 0.0

    expense_rows = _expense_rows(plan, trip, days, meals, normalization)
    reconciliation = persist_expenses(db, plan.id, expense_rows)
    expenses = expense_dashboard(db, plan.id)
    minimum = round(sum(category["minimum"] for category in expenses["categories"]), 2)
    expected = expenses["expected_total"]
    safe = round(sum(category["maximum"] for category in expenses["categories"]), 2)
    target = float(trip.total_budget)
    actual_categories = reconciliation["category_totals"]
    for day in days:
        day["expense_total_expected"] = reconciliation["day_totals"].get(str(day["day_number"]), 0)
    pricing_complete = all(
        item["cost_status"] not in {"UNKNOWN", "PUBLISHED_UNCONVERTED"} for day in days for item in day["items"]
    )
    budget = {
        "minimum": round(minimum, 2),
        "expected": round(expected, 2),
        "safe": round(safe, 2),
        "target": target,
        "status": "PRICE_CHECK_REQUIRED"
        if not pricing_complete
        else "WITHIN_BUDGET"
        if expected <= target
        else "OVER_BUDGET",
        "expected_per_person": round(expected / trip.traveler_count, 2),
        "safe_per_person": round(safe / trip.traveler_count, 2),
        "fixed_allocations": round(expected - actual_categories.get("activities", 0), 2),
        "activity_categories": actual_categories,
        "category_targets": trip.budget.categories if trip.budget else {},
        "pricing_complete": pricing_complete,
        "reconciliation": reconciliation,
    }
    validation = validate_itinerary(
        days, expected, target, state.get("locked_items", []), reconciliation=reconciliation
    )
    stage = "READY" if validation["valid"] else "FAILED"
    detail = {
        "plan_id": plan.id,
        "stage": stage,
        "stage_history": STAGE_HISTORY if stage == "READY" else [*STAGE_HISTORY[:-1], "FAILED"],
        "title": plan.title,
        "trip": {
            "destination": trip.destination_city,
            "start_date": trip.start_date.isoformat(),
            "end_date": trip.end_date.isoformat(),
            "traveler_count": trip.traveler_count,
            "currency": trip.currency,
        },
        "group_satisfaction": plan.mean_group_satisfaction,
        "minimum_traveler_satisfaction": plan.minimum_traveler_satisfaction,
        "budget": budget,
        "expenses": expenses,
        "flight_quote": plan.summary.get("flight_quote"),
        "days": days,
        "validation": validation,
        "data_provenance": (
            {
                "places": "OpenStreetMap live cache + optional Google Places rating/contact enrichment",
                "routes": "Google Routes when configured; labeled city-distance estimates otherwise",
                "photos": "Wikimedia + labeled category fallbacks"
                if any(place.image_url for place in places)
                else "category_placeholders",
                "prices": "official pages / OpenStreetMap fee tags; unknowns are labeled",
                "context": "OpenStreetMap provider tags + sourced Wikimedia summaries",
                "expenses": "persisted deterministic line items with reconciliation",
                "ratings_contact": "Google Places when configured; OpenStreetMap phone fallback",
            }
            if any(place.external_provider == "openstreetmap" for place in places)
            else {"places": "mock", "routes": "deterministic_haversine_estimate", "photos": "category_placeholders"}
        ),
    }
    record = db.scalar(select(PlanDetail).where(PlanDetail.plan_id == plan.id))
    if record:
        record.stage, record.detail = stage, detail
    else:
        record = PlanDetail(plan_id=plan.id, stage=stage, detail=detail)
        db.add(record)
    from backend.repositories.itineraries import persist_detail

    persist_detail(db, plan, detail)
    db.commit()
    return detail


def get_plan_detail(db: Session, plan_id: int) -> dict | None:
    record = db.scalar(select(PlanDetail).where(PlanDetail.plan_id == plan_id))
    return record.detail if record else None
