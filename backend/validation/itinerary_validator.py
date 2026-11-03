from __future__ import annotations


def validate_itinerary(
    days: list[dict],
    expected_budget: float,
    target_budget: float,
    locked_items: list[int] | None = None,
    reconciliation: dict | None = None,
) -> dict:
    items = [item for day in days for item in day["items"]]
    exact_places = all(
        item.get("place_id") and item.get("name") and item.get("address") and item.get("neighborhood") for item in items
    )
    no_duplicates = len({item["place_id"] for item in items}) == len(items)
    no_overlaps = True
    travel_feasible = True
    realistic_durations = True
    opening_hours = True
    sensible_day_bounds = True
    nightlife_timing = True
    meal_timing = True
    long_nature_time = True
    three_meals_daily = True
    exact_restaurants = True
    meal_opening_hours = True
    timeline_no_overlaps = True
    timeline_travel_feasible = True
    for day in days:
        has_structured_meals = bool(day.get("meals"))
        previous_end = None
        for item in day["items"]:
            start, end = item["start_minute"], item["end_minute"]
            if previous_end is not None and start < previous_end:
                no_overlaps = False
            if previous_end is not None and start < previous_end + item["travel_minutes_from_previous"] + item.get(
                "break_minutes_after_previous", 0
            ):
                travel_feasible = False
            realistic_durations = (
                realistic_durations and item["duration_min"] <= item["duration_minutes"] <= item["duration_max"]
            )
            opening_hours = opening_hours and item["opening_feasible"]
            sensible_day_bounds = sensible_day_bounds and 5 * 60 <= start <= 26 * 60 and end <= 28 * 60
            if item["category"] == "nightlife":
                nightlife_timing = nightlife_timing and start >= 18 * 60
            if item["category"] == "food" and not has_structured_meals:
                meal_timing = meal_timing and (8 * 60 <= start <= 15 * 60 or 17 * 60 <= start <= 22 * 60)
            if item["category"] == "nature" and any(
                token in item["subcategory"].casefold() for token in ("hike", "trail", "trek")
            ):
                long_nature_time = long_nature_time and item["duration_minutes"] >= 210
            previous_end = end
        meals = day.get("meals", [])
        three_meals_daily = three_meals_daily and {meal.get("meal_type") for meal in meals} == {
            "breakfast",
            "lunch",
            "dinner",
        }
        exact_restaurants = exact_restaurants and all(
            meal.get("restaurant_id")
            and meal.get("restaurant_name")
            and meal.get("address")
            and meal.get("maps_url", "").startswith("https://")
            for meal in meals
        )
        meal_opening_hours = meal_opening_hours and all(
            not meal.get("opening_hours") or meal.get("opening_feasible") is True for meal in meals
        )
        meal_windows = {
            "breakfast": (7 * 60, 10 * 60 + 30),
            "lunch": (11 * 60 + 30, 14 * 60 + 30),
            "dinner": (17 * 60 + 30, 21 * 60 + 30),
        }
        meal_timing = meal_timing and all(
            meal["meal_type"] in meal_windows
            and meal_windows[meal["meal_type"]][0] <= meal["start_minute"] <= meal_windows[meal["meal_type"]][1]
            for meal in meals
        )
        previous_end = None
        for item in day.get("timeline", []):
            if previous_end is not None:
                timeline_no_overlaps = timeline_no_overlaps and item["start_minute"] >= previous_end
                timeline_travel_feasible = timeline_travel_feasible and item["start_minute"] >= previous_end + item.get(
                    "travel_minutes_from_previous", 0
                )
            previous_end = item["end_minute"]
    locked = set(locked_items or [])
    exact_place_ids = {item["place_id"] for item in items} | {
        meal["restaurant_id"] for day in days for meal in day.get("meals", []) if meal.get("restaurant_id")
    }
    locked_preserved = locked.issubset(exact_place_ids)
    links_grounded = all(item.get("maps_url", "").startswith("https://") for item in items)
    checks = {
        "exact_places": exact_places,
        "no_duplicates": no_duplicates,
        "no_overlaps": no_overlaps,
        "travel_feasible": travel_feasible,
        "realistic_durations": realistic_durations,
        "opening_hours_feasible": opening_hours,
        "sensible_day_bounds": sensible_day_bounds,
        "nightlife_timing": nightlife_timing,
        "meal_timing": meal_timing,
        "breakfast_lunch_dinner_daily": three_meals_daily,
        "exact_restaurant_records": exact_restaurants,
        "meal_opening_hours_feasible": meal_opening_hours,
        "meal_aware_schedule_no_overlaps": timeline_no_overlaps,
        "meal_aware_travel_feasible": timeline_travel_feasible,
        "long_nature_duration": long_nature_time,
        "locked_items_preserved": locked_preserved,
        "hard_budget": expected_budget <= target_budget,
        "provider_links": links_grounded,
        "budget_reconciled": reconciliation is None or reconciliation.get("valid") is True,
    }
    return {"valid": all(checks.values()), "checks": checks}
