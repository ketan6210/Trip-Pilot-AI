from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from math import ceil
from statistics import mean

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from backend.core.errors import AppError
from backend.embeddings.semantic_mapper import semantic_similarity
from backend.models import PlanCostEntry, PlanVariant, Traveler, Trip, TripStateRecord, Vote
from backend.naming import generate_plan_names
from backend.optimization.plan_profiles import PLAN_PROFILES, PlanProfile
from backend.preference.aggregator import preference_for_place, traveler_preference_maps
from backend.preference.fairness import fairness_metrics
from backend.pricing.estimator import estimate_place_price
from backend.pricing.ledger import build_cost_ledger, ledger_to_dict, summarize_ledger
from backend.providers import get_place_provider
from backend.providers.base import PlaceSearch
from backend.providers.flights import get_flight_quote
from backend.providers.mock import place_to_dict


def _activity_budget(trip: Trip) -> float:
    if trip.budget and trip.budget.categories:
        return sum(
            value
            for key, value in trip.budget.categories.items()
            if key not in {"flight", "hotel", "buffer", "food", "transport"}
        )
    return float(trip.total_budget) * 0.28


def _in_trip_currency(place: dict, currency: str) -> dict:
    """Attach a typed display-price estimate; never turn unknown into zero/free."""
    estimate = estimate_place_price(place, currency, place.get("city"))
    return {
        **place,
        "published_cost": place.get("published_cost"),
        "published_currency": place.get("published_currency") or place.get("cost_currency"),
        "provider_amount": estimate.provider_amount,
        "provider_currency": estimate.provider_currency,
        "converted_currency": currency.upper(),
        "converted_amount": estimate.expected,
        "exchange_rate": estimate.exchange_rate,
        "rate_timestamp": estimate.rate_timestamp.isoformat() if estimate.rate_timestamp else None,
        "cost": estimate.expected,
        "cost_min": estimate.minimum,
        "cost_max": estimate.maximum,
        "cost_status": estimate.price_type.value,
        "cost_currency": estimate.currency,
        "cost_source": estimate.source or place.get("cost_source"),
        "price_confidence": estimate.confidence,
    }


def _score_place(
    place: dict, profile: PlanProfile, maps: dict[int, dict], custom_preferences: list[dict] | None = None
) -> tuple[float, list[float]]:
    traveler_scores = [preference_for_place(preferences, place) for preferences in maps.values()] or [0.5]
    fairness = fairness_metrics(traveler_scores)
    quality = float(place.get("quality_score", place.get("rating", 0))) / 5
    free_value = 1.0 if place.get("cost_status") == "FREE" else 0.0
    cost = place.get("cost")
    price = min(float(cost) / 75, 1.0) if cost is not None else 1.0
    place_text = " ".join(
        (
            place.get("name", ""),
            place.get("category", ""),
            place.get("subcategory", ""),
            place.get("description", ""),
            " ".join(place.get("tags", [])),
        )
    )
    semantic_match = max(
        (
            semantic_similarity(item.get("raw_text", ""), place_text) * float(item.get("weight", 0.7))
            for item in (custom_preferences or [])
            if item.get("raw_text")
        ),
        default=0.0,
    )
    premium_bonus = min(float(place.get("price_level") or 1) / 4, 1) if profile.key == "relaxed" else 0
    experience_bonus = quality * 0.10 if profile.key == "experience_heavy" else 0
    return (
        profile.preference_weight * mean(traveler_scores)
        + profile.fairness_weight * fairness["score"]
        + profile.quality_weight * quality
        + profile.free_value_weight * free_value
        + 0.12 * semantic_match
        + 0.12 * premium_bonus
        + experience_bonus
        - profile.cost_penalty_weight * price,
        traveler_scores,
    )


def _select_places(
    trip: Trip, profile: PlanProfile, candidates: list[dict], preferred_categories: set[str]
) -> list[dict]:
    days = (trip.end_date - trip.start_date).days + 1
    target = min(len(candidates), days * profile.activities_per_day)
    budget = _activity_budget(trip) * {"experience_heavy": 1.45, "relaxed": 2.0, "budget": 0.35}.get(profile.key, 1)
    cap = ceil(target / len(preferred_categories)) if len(preferred_categories) > 1 else target
    counts: Counter[str] = Counter()
    selected: list[dict] = []
    spent = 0.0
    for place in candidates:
        if len(selected) >= target:
            break
        if place["category"] in preferred_categories and counts[place["category"]] >= cap:
            continue
        per_person = place.get("cost")
        group_cost = float(per_person) * trip.traveler_count if per_person is not None else 0
        if profile.key != "relaxed" and per_person is not None and spent + group_cost > budget:
            continue
        selected.append(place)
        spent += group_cost
        counts[place["category"]] += 1
    for place in candidates:
        if len(selected) >= target:
            break
        if place not in selected:
            if profile.key == "budget":
                per_person = place.get("cost")
                group_cost = float(per_person) * trip.traveler_count if per_person is not None else 0
                if per_person is not None and spent + group_cost > budget:
                    continue
                spent += group_cost
            selected.append(place)
    return selected


def _ledger_within_budget(
    trip: Trip, profile: PlanProfile, selected: list[dict], flight_quote: dict | None
) -> tuple[list[dict], list, dict, PlanProfile]:
    """Keep evidence prices intact and remove optional paid stops until the plan fits."""
    selected = list(selected)
    budget = float(trip.total_budget)
    cost_profile = profile
    while True:
        ledger = build_cost_ledger(trip, cost_profile, selected, flight_quote, target_profile_key=profile.key)
        costs = summarize_ledger(ledger, budget, trip.traveler_count)
        if costs["expected"] <= budget:
            return selected, ledger, costs, cost_profile

        paid_stops = sorted(
            (
                (float(entry.expected), entry.item_id)
                for entry in ledger
                if entry.category == "activities" and entry.item_id and float(entry.expected or 0) > 0
            ),
            reverse=True,
        )
        if not paid_stops:
            if cost_profile.key != "budget":
                # A premium/experience strategy may keep its distinct places and
                # pace while using the value cost tier when its original comfort
                # assumptions cannot satisfy the user's hard ceiling.
                cost_profile = next(item for item in PLAN_PROFILES if item.key == "budget")
                continue
            rounding_unit = 100 if trip.currency.upper() in {"INR", "JPY"} else 10
            minimum_required = ceil(costs["expected"] / rounding_unit) * rounding_unit
            flight_note = (
                "This includes the confirmed live group airfare."
                if flight_quote and flight_quote.get("status") == "LIVE_FARE"
                else "Live airfare is unavailable, so the reserved flight allowance is not a ticket quote."
            )
            raise ValueError(
                f"The {trip.currency} {budget:,.0f} budget cannot cover the lowest known mandatory costs. "
                f"Use at least {trip.currency} {minimum_required:,.0f}, reduce the group/date range, or exclude flights. "
                f"Optional paid activities have already been removed. {flight_note}"
            )
        remove_id = paid_stops[0][1]
        selected = [place for place in selected if str(place.get("id")) != remove_id]


def _build_variant(
    trip: Trip,
    profile: PlanProfile,
    available_places: list[dict],
    flight_quote: dict | None = None,
    custom_preferences: list[dict] | None = None,
) -> dict:
    maps = traveler_preference_maps(trip.travelers)
    avoided = {
        (pref.category.casefold(), pref.subcategory.casefold())
        for traveler in trip.travelers
        for pref in traveler.preferences
        if pref.is_avoid
    }
    candidates = []
    for place in available_places:
        if (place["category"].casefold(), "general") in avoided or (
            place["category"].casefold(),
            place["subcategory"].casefold(),
        ) in avoided:
            continue
        score, traveler_scores = _score_place(place, profile, maps, custom_preferences)
        candidates.append({**place, "strategy_score": score, "traveler_scores": traveler_scores})
    if not candidates:
        raise ValueError(
            f"No live places were found for {trip.destination_city}. Check the city name or retry when the live provider is reachable."
        )
    candidates.sort(
        key=lambda item: (
            -item["strategy_score"],
            -(item.get("price_level") or 0) if profile.key == "relaxed" else (item.get("cost") or 0),
            item["name"],
        )
    )
    preferred_categories = {
        category
        for preferences in maps.values()
        for (category, subcategory), value in preferences.items()
        if subcategory == "general" and value >= 0.7
    }
    selected = _select_places(trip, profile, candidates, preferred_categories)
    original_activity_count = len(selected)
    unaligned_ledger = build_cost_ledger(trip, profile, selected, flight_quote, align_to_budget=False)
    unaligned_costs = summarize_ledger(unaligned_ledger, float(trip.total_budget), trip.traveler_count)
    selected, ledger, costs, cost_profile = _ledger_within_budget(trip, profile, selected, flight_quote)
    traveler_satisfactions = [
        mean([place["traveler_scores"][index] for place in selected]) if selected else 0
        for index in range(max(len(maps), 1))
    ]
    fairness = fairness_metrics(traveler_satisfactions)
    days = (trip.end_date - trip.start_date).days + 1
    neighborhoods = list(dict.fromkeys(place["neighborhood"] for place in selected))
    day_details = []
    for day_index in range(days):
        day_places = selected[day_index * profile.activities_per_day : (day_index + 1) * profile.activities_per_day]
        place_ids = {str(place["id"]) for place in day_places}
        activity_expected = sum(
            (entry.expected or 0) for entry in ledger if entry.category == "activities" and entry.item_id in place_ids
        )
        areas = list(dict.fromkeys(place["neighborhood"] for place in day_places))
        day_details.append(
            {
                "day": day_index + 1,
                "areas": areas,
                "places": [
                    {"id": place["id"], "name": place["name"], "category": place["category"]} for place in day_places
                ],
                "activity_cost_expected": round(activity_expected, 2),
                "travel_minutes": max(0, len(day_places) - 1) * profile.minutes_per_transfer,
            }
        )
    cost_reasons = [
        (
            f"{cost_profile.hotel_tier.title()} lodging is used to keep the {profile.title} strategy within budget."
            if cost_profile.key != profile.key
            else f"{profile.hotel_tier.title()} lodging for {max(1, (trip.end_date - trip.start_date).days)} nights."
        ),
        f"{profile.pace.title()} days with up to {profile.activities_per_day} planned places.",
        "Door-to-door local transport is emphasized."
        if profile.key == "relaxed"
        else "Local transport is optimized for value and route efficiency.",
    ]
    tradeoffs = {
        "balanced": "Balances varied interests, comfort, route time, and cost.",
        "experience_heavy": "Adds more standout paid stops and fuller days, increasing spend and walking.",
        "budget": "Prioritizes free or lower-cost places, efficient transit, and value meals.",
        "relaxed": "Prioritizes premium comfort and fewer transfers while respecting the total budget ceiling.",
    }
    featured = [
        {
            "name": place["name"],
            "category": place["category"],
            "neighborhood": place["neighborhood"],
            "image_url": place.get("image_url"),
            "image_source": place.get("image_source"),
            "cost": place.get("cost"),
            "group_cost": None if place.get("cost") is None else round(float(place["cost"]) * trip.traveler_count, 2),
            "traveler_count": trip.traveler_count,
            "published_cost": place.get("published_cost"),
            "published_currency": place.get("published_currency"),
            "cost_status": place.get("cost_status", "UNKNOWN"),
            "cost_currency": place.get("cost_currency", trip.currency),
            "ticket_url": place.get("ticket_url"),
            "official_url": place.get("official_url"),
            "cost_source": place.get("cost_source"),
            "maps_url": place["maps_url"],
            "rating": place.get("rating") if float(place.get("rating") or 0) > 0 else None,
            "review_count": place.get("review_count", 0),
            "rating_source": place.get("rating_source"),
            "phone_number": place.get("phone_number"),
            "phone_source_url": place.get("phone_source_url"),
        }
        for place in selected[:2]
    ]
    summary = {
        "strategy": {
            "balanced": "balanced",
            "experience_heavy": "experience",
            "budget": "smart_spend",
            "relaxed": "premium_convenience",
        }[profile.key],
        "tagline": profile.tagline,
        "pace": profile.pace,
        "hotel_tier": profile.hotel_tier,
        "neighborhoods": neighborhoods,
        "day_summaries": [
            f"Day {row['day']}: " + (", ".join(row["areas"]) if row["areas"] else "Open time") for row in day_details
        ],
        "daily_details": day_details,
        "currency": trip.currency,
        "traveler_count": trip.traveler_count,
        "place_ids": [place["id"] for place in selected],
        "featured_places": featured,
        "flight_quote": flight_quote,
        "main_tradeoffs": tradeoffs[profile.key],
        "cost_reasons": cost_reasons,
        "cost_breakdown": costs["breakdown"],
        "cost_ledger": [ledger_to_dict(entry) for entry in ledger],
        "pricing_complete": costs["pricing_complete"],
        "unknown_categories": costs["unknown_categories"],
        "budget_status": costs["budget_status"],
        "budget_delta": costs["budget_delta"],
        "over_by": costs["over_by"],
        "under_by": costs["under_by"],
        "cost_range": {"minimum": costs["minimum"], "expected": costs["expected"], "maximum": costs["maximum"]},
        "price_freshness": datetime.now(UTC).isoformat(),
        "category_budget": {key: row["expected"] for key, row in costs["breakdown"].items()},
        "activity_cost": costs["breakdown"].get("activities", {}).get("expected", 0),
        "budget_guidance": {
            "requested_budget": float(trip.total_budget),
            "full_itinerary_minimum": unaligned_costs["minimum"],
            "full_itinerary_expected": unaligned_costs["expected"],
            "activities_removed": original_activity_count - len(selected),
            "flexible_costs_reduced": any(entry.metadata.get("budget_aligned_factor", 1) < 1 for entry in ledger),
            "tier_downgraded_for_budget": cost_profile.key != profile.key,
            "flight_pricing_status": (flight_quote or {}).get("status", "NOT_INCLUDED"),
            "live_airfare_included": (flight_quote or {}).get("status") == "LIVE_FARE",
            "flight_caveat": (
                None
                if (flight_quote or {}).get("status") in {"LIVE_FARE", "NOT_INCLUDED"}
                else "Flight amount is a planning allowance, not a live fare; use the exact-route search or configure a production provider."
            ),
        },
    }
    return {
        "plan_type": profile.key,
        "title": profile.title,
        "estimated_total_cost": costs["expected"],
        "estimated_per_person_cost": costs["expected_per_person"],
        "mean_group_satisfaction": fairness["mean"],
        "minimum_traveler_satisfaction": fairness["minimum"],
        "satisfaction_variance": fairness["variance"],
        "fairness_score": fairness["score"],
        "travel_minutes": sum(row["travel_minutes"] for row in day_details),
        "walking_intensity": profile.walking_intensity,
        "activity_count": len(selected),
        "summary": summary,
    }


def generate_plan_variants(db: Session, trip_id: int, user_id: str | None = None) -> list[PlanVariant]:
    from backend.llm.telemetry import Trace, active_trace, tracing
    from backend.models import PlanningPolicy

    if db.get(PlanningPolicy, trip_id) is not None:
        from backend.ai.pipeline import generate_advanced

        trip = db.get(Trip, trip_id)
        plans, _ = generate_advanced(db, trip_id, user_id or trip.owner_id)
        return plans
    if active_trace.get() is not None:
        return _generate_legacy_variants(db, trip_id)
    trace = Trace()
    try:
        with tracing(trace), trace.stage("legacy_plan_generation"):
            plans = _generate_legacy_variants(db, trip_id)
        trace.save(db, trip_id=trip_id, user_id=user_id, operation="legacy_generate")
        db.commit()
        return plans
    except Exception as exc:
        db.rollback()
        if db.get(Trip, trip_id) is not None:
            trace.save(
                db,
                trip_id=trip_id,
                user_id=user_id,
                operation="legacy_generate",
                status="failed",
                error_code=getattr(exc, "code", "GENERATION_FAILED"),
            )
            db.commit()
        raise


def _generate_legacy_variants(db: Session, trip_id: int) -> list[PlanVariant]:
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == trip_id)
        .options(selectinload(Trip.travelers).selectinload(Traveler.preferences), selectinload(Trip.budget))
    )
    if not trip:
        raise LookupError("trip not found")
    generation_version = trip.trip_version
    if len(trip.travelers) != trip.traveler_count:
        raise ValueError(
            "Complete all traveler profiles before generation (at least two travelers for a two-person trip)"
        )
    provider = get_place_provider(db)
    place_records = provider.search(PlaceSearch(city=trip.destination_city, limit=300))
    available_places = [_in_trip_currency(place_to_dict(place), trip.currency) for place in place_records]
    flight_quote = get_flight_quote(trip)
    state_record = db.scalar(select(TripStateRecord).where(TripStateRecord.trip_id == trip_id))
    state = state_record.state if state_record else {}
    custom_preferences = state.get("custom_preferences", [])
    broad_preferences = state.get("broad_preferences", {})
    dominant_preferences = [
        key for key, _ in sorted(broad_preferences.items(), key=lambda item: (-float(item[1]), item[0]))
    ]
    if not dominant_preferences:
        dominant_preferences = sorted(
            {p.category for traveler in trip.travelers for p in traveler.preferences if p.score >= 7}
        )
    previews = [
        _build_variant(trip, profile, available_places, flight_quote, custom_preferences) for profile in PLAN_PROFILES
    ]
    featured_ids = list(
        dict.fromkeys(place_id for preview in previews for place_id in preview["summary"]["place_ids"][:2])
    )
    by_external_id = {place.external_place_id: place for place in place_records}
    for external_id in featured_ids:
        if external_id in by_external_id:
            provider.enrich(by_external_id[external_id])
    available_places = [_in_trip_currency(place_to_dict(place), trip.currency) for place in place_records]
    variants = [
        _build_variant(trip, profile, available_places, flight_quote, custom_preferences) for profile in PLAN_PROFILES
    ]
    names = generate_plan_names(
        trip.destination_city,
        dominant_preferences,
        {variant["plan_type"]: variant["summary"]["neighborhoods"][:4] for variant in variants},
    )
    for variant in variants:
        display = names[variant["plan_type"]]
        variant["title"] = display.title
        variant["summary"]["tagline"] = display.subtitle
    current_trip = db.scalar(
        select(Trip).where(Trip.id == trip_id).with_for_update().execution_options(populate_existing=True)
    )
    if current_trip.trip_version != generation_version:
        raise AppError(409, "TRIP_CHANGED", "Trip details changed during generation. Please retry.")
    plans = [PlanVariant(trip_id=trip_id, plan_set_version=trip.trip_version, **variant) for variant in variants]
    db.execute(delete(Vote).where(Vote.trip_id == trip_id))
    db.execute(
        update(PlanVariant).where(PlanVariant.trip_id == trip_id, PlanVariant.status != "stale").values(status="stale")
    )
    db.add_all(plans)
    db.flush()
    for plan in plans:
        from backend.repositories.itineraries import persist_schedule

        persist_schedule(db, plan, place_records)
        for row in plan.summary.get("cost_ledger", []):
            db.add(
                PlanCostEntry(
                    plan_id=plan.id,
                    cost_id=row["cost_id"],
                    category=row["category"],
                    item_id=row.get("item_id"),
                    minimum=row.get("minimum"),
                    expected=row.get("expected"),
                    maximum=row.get("maximum"),
                    currency=row["currency"],
                    shared_or_personal=row["shared_or_personal"],
                    price_type=row["price_type"],
                    confidence=row.get("confidence", 0),
                    provider=row.get("provider"),
                    source=row.get("source"),
                    fetched_at=datetime.fromisoformat(row["fetched_at"]),
                    metadata_json=row.get("metadata", {}),
                )
            )
    trip.status = "planned"
    db.commit()
    for plan in plans:
        db.refresh(plan)
    return plans


def serialize_plan(plan: PlanVariant) -> dict:
    data = {
        column.name: getattr(plan, column.name)
        for column in PlanVariant.__table__.columns
        if column.name != "created_at"
    }
    data["estimated_total_cost"] = float(plan.estimated_total_cost)
    data["estimated_per_person_cost"] = float(plan.estimated_per_person_cost)
    data["vote_count"] = sum(vote.vote_type == "vote" for vote in plan.votes)
    data["favorite_count"] = sum(vote.vote_type == "favorite" for vote in plan.votes)
    return data
