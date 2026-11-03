from decimal import Decimal
from statistics import mean

from backend.geography.distance import distance_km


def evaluate_plan(plan: dict, trip: dict, preferences: list[set[str]]) -> dict:
    """Measure returned values, including reconciliation; no model self-grading."""
    ai = plan["summary"]["ai"]
    evidence = {row["place_id"]: row for row in ai["evidence"]}
    schedule = ai["schedule"]
    costs = plan["budget"]
    observed, supported, schedule_checks, categories = 0, 0, [], set()
    route_km, nearest_km = 0.0, 0.0
    seen = set()
    for day in schedule["days"]:
        prior_end = ai["constraints"]["day_start_minute"]
        places = []
        for index, stop in enumerate(day["stops"]):
            observed += 1
            place = evidence.get(stop["place_id"])
            if place is None:
                schedule_checks.append(False)
                continue
            categories.add(place["category"])
            supported += int(
                stop["cost_cents"] == place["cost_cents"] and bool(place["provider"]) and bool(place["price_source"])
            )
            pause = (20 if plan["plan_type"] == "relaxed" else 10) if index else 0
            windows = place["opening_windows"].get(day["date"], [])
            schedule_checks.extend(
                [
                    stop["start_minute"] >= prior_end + stop["travel_minutes"] + pause,
                    stop["end_minute"] - stop["start_minute"] == place["duration_minutes"],
                    any(stop["start_minute"] >= a and stop["end_minute"] <= b for a, b in windows),
                    stop["place_id"] not in seen,
                ]
            )
            seen.add(stop["place_id"])
            prior_end = stop["end_minute"]
            places.append(place)
        schedule_checks.append(prior_end + day["return_travel_minutes"] <= ai["constraints"]["return_by_minute"])

        def km(a, b):
            return distance_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])

        route_km += sum(km(a, b) for a, b in zip(places, places[1:]))
        if places:
            remaining = places[1:]
            current = places[0]
            while remaining:
                next_place = min(remaining, key=lambda row: km(current, row))
                nearest_km += km(current, next_place)
                remaining.remove(next_place)
                current = next_place
    reconciled = sum(Decimal(value) for value in costs["category_breakdown"].values()) == Decimal(
        costs["estimated_total"]
    )
    budget_ok = reconciled and Decimal(costs["estimated_total"]) <= Decimal(str(trip["total_budget"]))
    hard = [
        *schedule_checks,
        budget_ok,
        set(ai["constraints"]["must_place_ids"]).issubset(seen),
        not (set(ai["constraints"]["veto_place_ids"]) & seen),
        set(ai["constraints"]["must_categories"]).issubset(categories),
        not (set(ai["constraints"]["veto_categories"]) & categories),
    ]
    coverage = [len(interests & categories) / len(interests) for interests in preferences if interests]
    return {
        "strategy": plan["plan_type"],
        "constraint_satisfaction_rate": sum(hard) / len(hard),
        "budget_compliant": budget_ok,
        "schedule_valid": all(schedule_checks),
        "provider_reference_grounding_rate": supported / observed if observed else None,
        "activity_count": observed,
        "route_km": round(route_km, 4),
        "nearest_neighbor_baseline_km": round(nearest_km, 4),
        "route_to_nearest_neighbor_ratio": round(route_km / nearest_km, 4) if nearest_km else None,
        "preference_coverage": mean(coverage) if coverage else None,
        "minimum_member_coverage": min(coverage) if coverage else None,
        "solver_status": schedule["status"],
        "solver_seconds": schedule["solver_seconds"],
    }
