from copy import deepcopy
from decimal import Decimal

from sqlalchemy import delete, func, select

from backend.ai.schemas import ScheduleResult
from backend.models import ItineraryActivity, ItineraryDay, ItineraryVersion, PlanCostEntry


def persist_days(db, plan, schedule: ScheduleResult, candidates, only_days=None):
    evidence = {place.place_id: place for place in candidates}
    for day in schedule.days:
        if only_days is not None and day.day_number not in only_days:
            continue
        existing = db.scalar(
            select(ItineraryDay).where(ItineraryDay.plan_id == plan.id, ItineraryDay.day_number == day.day_number)
        )
        if existing:
            db.delete(existing)
            db.flush()
        row = ItineraryDay(plan_id=plan.id, day_number=day.day_number, date=day.date)
        db.add(row)
        db.flush()
        for position, stop in enumerate(day.stops):
            candidate = evidence[stop.place_id]
            db.add(
                ItineraryActivity(
                    day_id=row.id,
                    place_id=stop.place_id,
                    position=position,
                    start_minute=stop.start_minute,
                    end_minute=stop.end_minute,
                    estimated_cost=Decimal(candidate.cost_cents) / 100,
                    currency=plan.trip.currency,
                    category=candidate.category,
                    reason_for_selection=stop.reason,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                )
            )


def persist_costs(db, plan, budget, sources):
    db.execute(delete(PlanCostEntry).where(PlanCostEntry.plan_id == plan.id))
    for category, value in budget["category_cents"].items():
        amount = Decimal(value) / 100
        db.add(
            PlanCostEntry(
                plan_id=plan.id,
                cost_id=f"ai-{plan.id}-{category}",
                category=category,
                minimum=amount,
                expected=amount,
                maximum=amount,
                currency=plan.trip.currency,
                shared_or_personal="SHARED",
                price_type="ESTIMATED",
                confidence=0.5,
                provider="Grounded activity prices and explicit planning estimates",
                source=None,
                metadata_json={"sources": sources.get(category), "group_total": True},
            )
        )


def latest_version(db, plan_id):
    return db.scalar(select(func.max(ItineraryVersion.version)).where(ItineraryVersion.plan_id == plan_id)) or 0


def append_version(db, plan, user_id, reason):
    version = latest_version(db, plan.id) + 1
    plan.summary = {**plan.summary, "itinerary_version": version}
    fields = (
        "title",
        "estimated_total_cost",
        "estimated_per_person_cost",
        "travel_minutes",
        "activity_count",
        "mean_group_satisfaction",
        "minimum_traveler_satisfaction",
        "satisfaction_variance",
        "fairness_score",
    )
    snapshot = {
        "plan": {
            key: str(getattr(plan, key)) if isinstance(getattr(plan, key), Decimal) else getattr(plan, key)
            for key in fields
        },
        "summary": deepcopy(plan.summary),
    }
    row = ItineraryVersion(
        plan_id=plan.id,
        version=version,
        trip_version=plan.plan_set_version,
        actor_id=user_id,
        reason=reason[:500],
        snapshot=snapshot,
    )
    db.add(row)
    db.flush()
    return row
