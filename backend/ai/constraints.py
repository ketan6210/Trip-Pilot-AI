"""Conservative provider-hours parsing and hard candidate filtering."""

import re
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from backend.ai.schemas import Candidate, PlanningConstraints
from backend.currency import currency_service
from backend.geography.distance import distance_km

WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def cents(value) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_CEILING))


def opening_windows(hours: dict, day: date) -> list[tuple[int, int]] | None:
    """None means unknown, [] means closed. Unsupported syntax never implies open."""
    structured = hours.get("exceptions", {}).get(day.isoformat())
    if structured is None and "weekly" in hours:
        structured = hours["weekly"].get(str(day.weekday()), [])
    if structured is not None:
        try:
            windows = [(int(a), int(b)) for a, b in structured]
            return windows if all(0 <= a < b <= 2880 for a, b in windows) else None
        except (TypeError, ValueError):
            return None
    raw = str(hours.get("raw") or "").strip()
    if raw == "24/7":
        return [(0, 2880)]
    if raw.casefold() in {"closed", "off"}:
        return []
    if not raw:
        return None
    result = []
    for clause in raw.split(";"):
        match = re.fullmatch(
            r"\s*(?:(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?\s+)?(off|closed|\d{1,2}:\d{2}-\d{1,2}:\d{2}(?:\s*,\s*\d{1,2}:\d{2}-\d{1,2}:\d{2})*)\s*",
            clause,
        )
        if not match:
            return None
        first, last, periods = match.groups()
        if first:
            start, end = WEEKDAYS.index(first), WEEKDAYS.index(last or first)
            applies = day.weekday() in ({(start + i) % 7 for i in range((end - start) % 7 + 1)})
            if not applies:
                continue
        if periods in {"off", "closed"}:
            result = []
            continue
        for window in periods.split(","):
            begin, finish = window.strip().split("-")
            ah, am = map(int, begin.split(":"))
            bh, bm = map(int, finish.split(":"))
            if ah > 23 or bh > 24 or am > 59 or bm > 59 or bh == 24 and bm != 0:
                return None
            a, b = ah * 60 + am, bh * 60 + bm
            result.append((a, b if b > a else b + 1440))
    return result


def prepare_candidates(places, trip, constraints: PlanningConstraints) -> tuple[list[Candidate], list[dict]]:
    days = [trip.start_date + timedelta(days=i) for i in range((trip.end_date - trip.start_date).days + 1)]
    accepted, rejected = [], []
    for place in places:
        reason = None
        metadata = place.source_metadata or {}
        if (
            place.id in constraints.veto_place_ids
            or place.category in constraints.veto_categories
            or f"{place.category}/{place.subcategory}" in constraints.veto_subcategories
        ):
            reason = "veto"
        elif metadata.get("permanently_closed") or metadata.get("business_status") in {
            "CLOSED_PERMANENTLY",
            "CLOSED_TEMPORARILY",
        }:
            reason = "closed"
        elif (
            constraints.youngest_age is not None
            and place.age_restriction
            and place.age_restriction > constraints.youngest_age
        ):
            reason = "age_restriction"
        elif constraints.wheelchair_required and place.accessibility.casefold() not in {
            "yes",
            "accessible",
            "wheelchair_accessible",
        }:
            reason = "accessibility_unverified"
        elif place.category == "food" and not set(x.casefold() for x in constraints.dietary_requirements).issubset(
            set(x.casefold() for x in metadata.get("dietary_options", []))
        ):
            reason = "dietary_incompatible_or_unknown"
        elif (
            constraints.anchor_latitude is not None
            and distance_km(constraints.anchor_latitude, constraints.anchor_longitude, place.latitude, place.longitude)
            > constraints.max_leg_km
        ):
            reason = "outside_radius"
        elif place.cost_status == "UNKNOWN":
            reason = "unknown_price"
        cost = (
            currency_service.convert(float(place.cost_expected), place.cost_currency, trip.currency)
            if not reason
            else None
        )
        if not reason and cost is None:
            reason = "currency_unavailable"
        group_cost = cents(cost.converted_amount) * trip.traveler_count if cost else 0
        if not reason and group_cost > cents(trip.total_budget):
            reason = "outside_budget"
        hours = {day: opening_windows(place.opening_hours or {}, day) for day in days}
        known = all(value is not None for value in hours.values())
        if not reason and constraints.require_known_hours and not known:
            reason = "unknown_hours"
        available = {
            day: periods if periods is not None else [(constraints.day_start_minute, constraints.return_by_minute)]
            for day, periods in hours.items()
        }
        if not reason and not any(available.values()):
            reason = "closed_during_trip"
        if reason:
            rejected.append({"place_id": place.id, "reason": reason})
            continue
        accepted.append(
            Candidate(
                place_id=place.id,
                external_id=place.external_place_id,
                name=place.name,
                category=place.category,
                subcategory=place.subcategory,
                description=place.description,
                latitude=place.latitude,
                longitude=place.longitude,
                neighborhood=place.neighborhood,
                duration_minutes=max(15, min(480, place.duration_minutes)),
                cost_cents=group_cost,
                currency=trip.currency,
                price_source=place.cost_source or place.external_provider,
                price_type=place.cost_status,
                rating=float(place.rating) if place.rating_source else None,
                provider=place.external_provider,
                opening_windows=available,
                hours_known=known,
                source=place.official_url or place.cost_source,
            )
        )
    return accepted, rejected
