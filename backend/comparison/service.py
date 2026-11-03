from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Place, PlanVariant

CATEGORY_LABELS = {
    "activities": "attractions and tickets",
    "attraction": "attractions and tickets",
    "buffer": "emergency cushion",
    "flight": "flights",
    "food": "meals",
    "hotel": "hotel",
    "nightlife": "nightlife",
    "shopping": "shopping",
    "transport": "getting around",
}

STRATEGY_LABELS = {
    "balanced": "Balanced Explorer",
    "experience": "See More & Do More",
    "experience_heavy": "See More & Do More",
    "smart_spend": "Smart Saver",
    "budget": "Smart Saver",
    "premium_convenience": "Easy & Comfortable",
    "relaxed": "Easy & Comfortable",
}

STRATEGY_TIPS = {
    "balanced": "Choose this for a comfortable mix of major sights, free time, and value.",
    "experience": "Choose this to fit in more standout attractions and fuller sightseeing days.",
    "experience_heavy": "Choose this to fit in more standout attractions and fuller sightseeing days.",
    "smart_spend": "Choose this to keep more money in your pocket while covering the essentials.",
    "budget": "Choose this to keep more money in your pocket while covering the essentials.",
    "premium_convenience": "Choose this for an easier pace, added comfort, and fewer tiring tradeoffs.",
    "relaxed": "Choose this for an easier pace, added comfort, and fewer tiring tradeoffs.",
}


def _strategy_label(strategy: str | None) -> str:
    return STRATEGY_LABELS.get(strategy or "", "Traveler's Choice")


def _strategy_tip(strategy: str | None) -> str:
    return STRATEGY_TIPS.get(strategy or "", "Choose this if its places and daily pace suit your group best.")


def compare_plans(db: Session, plan_a_id: int, plan_b_id: int) -> dict:
    plans = list(db.scalars(select(PlanVariant).where(PlanVariant.id.in_([plan_a_id, plan_b_id]))))
    if len(plans) != 2:
        raise LookupError("one or both plans were not found")
    by_id = {plan.id: plan for plan in plans}
    a, b = by_id[plan_a_id], by_id[plan_b_id]
    if a.trip_id != b.trip_id:
        raise ValueError("plans must belong to the same trip")
    ids_a, ids_b = set(a.summary.get("place_ids", [])), set(b.summary.get("place_ids", []))
    all_ids = ids_a | ids_b
    places = {
        place.external_place_id: place.name
        for place in db.scalars(select(Place).where(Place.external_place_id.in_(all_ids)))
    }
    breakdown_a, breakdown_b = a.summary.get("cost_breakdown", {}), b.summary.get("cost_breakdown", {})
    categories = sorted(set(breakdown_a) | set(breakdown_b))
    category_costs = {}
    for category in categories:
        cost_a = round(float(breakdown_a.get(category, {}).get("expected", 0)), 2)
        cost_b = round(float(breakdown_b.get(category, {}).get("expected", 0)), 2)
        category_costs[category] = {
            "label": CATEGORY_LABELS.get(category, category.replace("_", " ")),
            "plan_a": cost_a,
            "plan_b": cost_b,
            "delta": round(cost_b - cost_a, 2),
        }
    category_deltas = {category: row["delta"] for category, row in category_costs.items()}
    daily_a = {int(row["day"]): row for row in a.summary.get("daily_details", [])}
    daily_b = {int(row["day"]): row for row in b.summary.get("daily_details", [])}
    day_differences = []
    for day in sorted(set(daily_a) | set(daily_b)):
        row_a, row_b = daily_a.get(day, {}), daily_b.get(day, {})
        names_a = [place["name"] for place in row_a.get("places", [])]
        names_b = [place["name"] for place in row_b.get("places", [])]
        day_differences.append(
            {
                "day": day,
                "plan_a_places": names_a,
                "plan_b_places": names_b,
                "only_in_a": [name for name in names_a if name not in names_b],
                "only_in_b": [name for name in names_b if name not in names_a],
                "travel_minutes_delta": int(row_b.get("travel_minutes", 0)) - int(row_a.get("travel_minutes", 0)),
                "activity_cost_delta": round(
                    float(row_b.get("activity_cost_expected", 0)) - float(row_a.get("activity_cost_expected", 0)), 2
                ),
            }
        )
    strategy_a, strategy_b = a.summary.get("strategy"), b.summary.get("strategy")
    label_a, label_b = _strategy_label(strategy_a), _strategy_label(strategy_b)
    total_a, total_b = float(a.estimated_total_cost), float(b.estimated_total_cost)
    total_delta = round(total_b - total_a, 2)
    if total_delta < 0:
        verdict = {"cheaper": "plan_b", "savings": abs(total_delta)}
    elif total_delta > 0:
        verdict = {"cheaper": "plan_a", "savings": abs(total_delta)}
    else:
        verdict = {"cheaper": "same", "savings": 0.0}
    largest = sorted(category_costs.items(), key=lambda item: (-abs(item[1]["delta"]), item[0]))[:3]
    reasons = []
    for _, row in largest:
        delta = row["delta"]
        if not delta:
            continue
        if delta < 0:
            reasons.append(f"{label_b} saves {abs(delta):,.2f} on {row['label']}.")
        else:
            reasons.append(f"{label_b} spends {abs(delta):,.2f} more on {row['label']}.")
    return {
        "trip_id": a.trip_id,
        "currency": a.summary.get("currency") or b.summary.get("currency") or "USD",
        "plan_a": {
            "id": a.id,
            "title": a.title,
            "strategy": strategy_a,
            "display_name": label_a,
            "decision_tip": _strategy_tip(strategy_a),
            "total": total_a,
        },
        "plan_b": {
            "id": b.id,
            "title": b.title,
            "strategy": strategy_b,
            "display_name": label_b,
            "decision_tip": _strategy_tip(strategy_b),
            "total": total_b,
        },
        "total_cost_delta": total_delta,
        "verdict": verdict,
        "category_deltas": category_deltas,
        "category_costs": category_costs,
        "shared_places": [places.get(item, item) for item in sorted(ids_a & ids_b)],
        "only_in_plan_a": [places.get(item, item) for item in sorted(ids_a - ids_b)],
        "only_in_plan_b": [places.get(item, item) for item in sorted(ids_b - ids_a)],
        "daily_differences": day_differences,
        "reasons": reasons,
    }
