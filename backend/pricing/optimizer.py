from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import PlanCostEntry, PlanVariant, Trip, TripStateRecord


def bring_plan_under_budget(db: Session, plan_id: int) -> dict:
    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise LookupError("plan not found")
    trip = db.get(Trip, plan.trip_id)
    target = float(trip.total_budget)
    before = float(plan.estimated_total_cost)
    if before <= target:
        return {
            "plan_id": plan.id,
            "before": before,
            "after": before,
            "target": target,
            "changes": [],
            "already_within_budget": True,
        }
    state_record = db.scalar(select(TripStateRecord).where(TripStateRecord.trip_id == trip.id))
    locked = {str(item) for item in ((state_record.state if state_record else {}).get("locked_items", []))}
    rows = [dict(row) for row in plan.summary.get("cost_ledger", [])]
    needed = before - target
    changes = []
    priorities = {
        name: index
        for index, name in enumerate(("shopping", "buffer", "nightlife", "hotel", "food", "transport", "activities"))
    }
    rows.sort(key=lambda row: (priorities.get(row["category"], 99), row.get("item_id") or ""))
    for row in rows:
        if needed <= 0.001:
            break
        if str(row.get("item_id")) in locked or row.get("expected") is None:
            continue
        current = float(row["expected"])
        floor = 0.0 if row["category"] in {"shopping", "buffer", "nightlife"} else float(row.get("minimum") or current)
        available = max(0, current - floor)
        reduction = min(available, needed)
        if reduction <= 0:
            continue
        row["expected"] = round(current - reduction, 2)
        row["maximum"] = max(float(row.get("maximum") or row["expected"]), row["expected"])
        row.setdefault("metadata", {})["bring_under_budget"] = True
        changes.append(
            {
                "category": row["category"],
                "item_id": row.get("item_id"),
                "before": current,
                "after": row["expected"],
                "saved": round(reduction, 2),
            }
        )
        needed -= reduction
    after = round(sum(float(row.get("expected") or 0) for row in rows), 2)
    if after > target + 0.01:
        return {
            "plan_id": plan.id,
            "before": before,
            "after": after,
            "target": target,
            "changes": changes,
            "possible": False,
            "message": "Locked or minimum required costs prevent this plan from fitting the budget.",
        }
    breakdown = defaultdict(lambda: {"minimum": 0.0, "expected": 0.0, "maximum": 0.0, "price_types": set()})
    for row in rows:
        group = breakdown[row["category"]]
        for key in ("minimum", "expected", "maximum"):
            group[key] += float(row.get(key) or 0)
        group["price_types"].add(row["price_type"])
    serialized_breakdown = {
        category: {
            "minimum": round(row["minimum"], 2),
            "expected": round(row["expected"], 2),
            "maximum": round(row["maximum"], 2),
            "price_types": sorted(row["price_types"]),
        }
        for category, row in breakdown.items()
    }
    summary = dict(plan.summary)
    summary.update(
        {
            "cost_ledger": rows,
            "cost_breakdown": serialized_breakdown,
            "cost_range": {
                "minimum": round(sum(row["minimum"] for row in serialized_breakdown.values()), 2),
                "expected": after,
                "maximum": round(sum(row["maximum"] for row in serialized_breakdown.values()), 2),
            },
            "budget_status": "NEAR_BUDGET" if after >= target * 0.9 else "UNDER_BUDGET",
            "budget_delta": round(after - target, 2),
            "over_by": 0,
            "under_by": round(target - after, 2),
            "bring_under_budget_changes": changes,
        }
    )
    plan.summary = summary
    plan.estimated_total_cost = after
    plan.estimated_per_person_cost = round(after / trip.traveler_count, 2)
    persisted = {
        entry.cost_id: entry for entry in db.scalars(select(PlanCostEntry).where(PlanCostEntry.plan_id == plan.id))
    }
    for row in rows:
        entry = persisted.get(row["cost_id"])
        if entry:
            entry.expected, entry.maximum, entry.metadata_json = (
                row.get("expected"),
                row.get("maximum"),
                row.get("metadata", {}),
            )
    db.commit()
    return {
        "plan_id": plan.id,
        "before": before,
        "after": after,
        "target": target,
        "changes": changes,
        "possible": True,
    }
