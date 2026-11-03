"""Compatibility response for the original single-itinerary endpoint."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.domain.itineraries import budget_summary
from backend.models import ItineraryActivity, ItineraryDay
from backend.planning import generate_plan_variants
from backend.providers.mock import place_to_dict


def generate_itinerary(db: Session, trip_id: int) -> dict:
    # Reuse the persisted planning engine so this route has identical currency,
    # group-cost, duration and routing rules to the versioned API.
    plan = next(plan for plan in generate_plan_variants(db, trip_id) if plan.plan_type == "balanced")
    budget = budget_summary(db, plan)
    days = []
    for day in db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.plan_id == plan.id)
        .order_by(ItineraryDay.day_number)
        .options(selectinload(ItineraryDay.activities).selectinload(ItineraryActivity.place))
    ):
        items = [
            {
                **place_to_dict(activity.place),
                "place_id": activity.place_id,
                "cost": None if activity.estimated_cost is None else float(activity.estimated_cost),
                "cost_currency": activity.currency,
                "start_time": f"{activity.start_minute // 60:02d}:{activity.start_minute % 60:02d}",
                "end_time": f"{activity.end_minute // 60:02d}:{activity.end_minute % 60:02d}",
                "duration_minutes": activity.end_minute - activity.start_minute,
                "why_selected": activity.reason_for_selection,
            }
            for activity in day.activities
        ]
        days.append(
            {"date": day.date.isoformat(), "items": items, "day_cost": sum(item["cost"] or 0 for item in items)}
        )
    return {
        "trip_id": trip_id,
        "itinerary_id": plan.id,
        "currency": plan.trip.currency,
        "days": days,
        "cost_summary": {
            "activities": float(budget["category_breakdown"]["activities"]),
            "total_estimated": float(budget["estimated_total"]),
            "trip_budget": float(plan.trip.total_budget),
            "remaining": float(budget["remaining_budget"]),
            "pricing_complete": budget["pricing_complete"],
        },
    }
