from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.itinerary.itinerary_planner import build_daily_schedule
from backend.models import ItineraryActivity, ItineraryDay, Place, PlanVariant
from backend.optimization.plan_profiles import PLAN_PROFILES
from backend.optimization.route_optimizer import validate_geography
from backend.pricing.estimator import estimate_place_price
from backend.providers.mock import place_to_dict


def clear_days(db: Session, plan_id: int) -> None:
    for day in db.scalars(select(ItineraryDay).where(ItineraryDay.plan_id == plan_id)):
        db.delete(day)
    db.flush()


def persist_schedule(db: Session, plan: PlanVariant, places: list[Place]) -> None:
    """Persist candidate schedules using the existing duration/route optimizer."""
    selected = {str(value) for value in plan.summary.get("place_ids", [])}
    places = [place for place in places if place.external_place_id in selected]
    profile = next(p for p in PLAN_PROFILES if p.key == plan.plan_type)
    schedule = build_daily_schedule(places, plan.trip, profile)
    clear_days(db, plan.id)
    for index, row in enumerate(schedule, 1):
        validate_geography([activity.place for activity in row["schedule"]])
        if (
            plan.trip.transport_mode == "walking"
            and sum(activity.travel_minutes for activity in row["schedule"]) > plan.trip.max_walking_per_day
        ):
            raise ValueError("The route exceeds the daily walking limit; reduce activities or allow transit/taxis")
        day = ItineraryDay(plan_id=plan.id, day_number=index, date=row["date"])
        db.add(day)
        db.flush()
        for position, activity in enumerate(row["schedule"]):
            place = activity.place
            price = estimate_place_price(place_to_dict(place), plan.trip.currency, plan.trip.destination_city)
            db.add(
                ItineraryActivity(
                    day_id=day.id,
                    place_id=place.id,
                    position=position,
                    start_minute=activity.start_minute,
                    end_minute=activity.end_minute,
                    estimated_cost=None
                    if price.expected is None
                    else round(price.expected * plan.trip.traveler_count, 2),
                    currency=plan.trip.currency,
                    category=place.category,
                    reason_for_selection=f"Selected for the {profile.key} strategy; grouped by location and scheduled using visit duration and opening-hour information.",
                    latitude=place.latitude,
                    longitude=place.longitude,
                )
            )


def persist_detail(db: Session, plan: PlanVariant, detail: dict) -> None:
    clear_days(db, plan.id)
    for index, row in enumerate(detail.get("days", []), 1):
        day = ItineraryDay(plan_id=plan.id, day_number=index, date=date.fromisoformat(row["date"]))
        db.add(day)
        db.flush()
        for position, item in enumerate(row.get("timeline") or row.get("items", [])):
            place = db.get(Place, item.get("place_id") or item.get("restaurant_id"))
            if not place:
                raise ValueError("An itinerary activity references an unavailable place")
            db.add(
                ItineraryActivity(
                    day_id=day.id,
                    place_id=place.id,
                    position=position,
                    start_minute=item["start_minute"],
                    end_minute=item["end_minute"],
                    estimated_cost=item.get("group_cost_expected", item.get("expected_total")),
                    currency=plan.trip.currency,
                    category=item.get("category", "food"),
                    reason_for_selection=item.get("why_selected") or item.get("reason_selected", ""),
                    latitude=place.latitude,
                    longitude=place.longitude,
                )
            )


def read_days(db: Session, plan_id: int) -> list[dict]:
    rows = db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.plan_id == plan_id)
        .order_by(ItineraryDay.day_number)
        .options(selectinload(ItineraryDay.activities).selectinload(ItineraryActivity.place))
    )
    return [
        {
            "id": day.id,
            "day_number": day.day_number,
            "date": day.date,
            "activities": [
                {
                    "id": a.id,
                    "place_id": a.place_id,
                    "place": a.place.name,
                    "start_time": f"{a.start_minute // 60:02d}:{a.start_minute % 60:02d}",
                    "end_time": f"{a.end_minute // 60:02d}:{a.end_minute % 60:02d}",
                    "duration_minutes": a.end_minute - a.start_minute,
                    "estimated_cost": a.estimated_cost,
                    "currency": a.currency,
                    "latitude": a.latitude,
                    "longitude": a.longitude,
                    "category": a.category,
                    "reason_for_selection": a.reason_for_selection,
                }
                for a in day.activities
            ],
        }
        for day in rows
    ]
