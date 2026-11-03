from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.errors import AppError
from backend.models import ExpenseLineItem, PlanCostEntry, PlanDetail, PlanVariant, Trip
from backend.planning import serialize_plan
from backend.repositories.itineraries import read_days


def budget_summary(db: Session, plan: PlanVariant) -> dict:
    categories = {
        key: Decimal("0")
        for key in ("transportation", "hotel", "food", "activities", "local_transport", "miscellaneous")
    }
    aliases = {
        "flight": "transportation",
        "flights": "transportation",
        "transport": "local_transport",
        "buffer": "miscellaneous",
        "shopping": "miscellaneous",
        "nightlife": "activities",
    }
    unknown = set()
    expenses = list(db.scalars(select(ExpenseLineItem).where(ExpenseLineItem.plan_id == plan.id)))
    entries = expenses or list(db.scalars(select(PlanCostEntry).where(PlanCostEntry.plan_id == plan.id)))
    for entry in entries:
        key = aliases.get(entry.category, entry.category)
        if key not in categories:
            key = "miscellaneous"
        if entry.currency != plan.trip.currency:
            raise AppError(409, "CURRENCY_MISMATCH", "The itinerary ledger contains mixed currencies; regenerate it.")
        expected = entry.total_expected if expenses else entry.expected
        if expected is None:
            unknown.add(key)
        else:
            categories[key] += expected
    total = sum(categories.values(), Decimal("0"))
    return {
        "currency": plan.trip.currency,
        "estimated_total": total,
        "remaining_budget": plan.trip.total_budget - total,
        "category_breakdown": categories,
        "per_person_cost": (total / plan.trip.traveler_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "pricing_complete": not unknown and bool(plan.summary.get("pricing_complete")),
        "unknown_categories": sorted(unknown),
    }


def read_itinerary(db: Session, plan: PlanVariant) -> dict:
    budget = budget_summary(db, plan)
    return {
        **serialize_plan(plan),
        "estimated_total_cost": float(budget["estimated_total"]),
        "estimated_per_person_cost": float(budget["per_person_cost"]),
        "strategy": plan.plan_type,
        "days": read_days(db, plan.id),
        "budget": budget,
    }


def select_itinerary(db: Session, plan: PlanVariant) -> PlanVariant:
    trip = db.scalar(select(Trip).where(Trip.id == plan.trip_id).with_for_update())
    db.refresh(plan)
    if plan.status == "stale" or plan.plan_set_version != trip.trip_version:
        raise AppError(409, "STALE_ITINERARY", "Regenerate itineraries after changing the trip.")
    db.execute(
        update(PlanVariant)
        .where(PlanVariant.trip_id == trip.id, PlanVariant.status == "selected")
        .values(status="candidate")
    )
    db.flush()
    plan.status = "selected"
    trip.status = "selected"
    db.commit()
    return plan


def rename_itinerary(db: Session, plan: PlanVariant, title: str) -> PlanVariant:
    if plan.status == "stale":
        raise AppError(409, "STALE_ITINERARY", "Regenerate itineraries after changing the trip.")
    plan.title = title
    detail = db.scalar(select(PlanDetail).where(PlanDetail.plan_id == plan.id))
    if detail:
        detail.detail = {**detail.detail, "title": title}
    if plan.summary.get("engine") == "cp_sat":
        from backend.ai.persistence import append_version

        append_version(db, plan, None, "Renamed itinerary")
    db.commit()
    return plan
