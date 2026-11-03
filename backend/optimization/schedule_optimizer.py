from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from backend.itinerary.best_time import best_time, day_bounds
from backend.itinerary.duration_model import estimate_duration
from backend.optimization.constraints import schedule_constraints
from backend.optimization.route_optimizer import nearest_neighbor_order, route_matrix


@dataclass(frozen=True)
class ScheduledActivity:
    place: object
    start_minute: int
    end_minute: int
    duration_min: int
    duration_expected: int
    duration_max: int
    travel_minutes: int
    break_minutes: int
    best_time_of_day: str
    opening_feasible: bool


def _opening_window(raw: str | None, category: str) -> tuple[int, int]:
    defaults = {
        "museums": (9 * 60, 18 * 60),
        "history": (9 * 60, 18 * 60),
        "nature": (6 * 60, 21 * 60),
        "nightlife": (18 * 60, 26 * 60),
        "food": (8 * 60, 23 * 60),
        "shopping": (10 * 60, 20 * 60),
        "entertainment": (10 * 60, 24 * 60),
        "landmarks": (8 * 60, 21 * 60),
    }
    if not raw:
        return defaults.get(category, (8 * 60, 22 * 60))
    if "24/7" in raw:
        return 0, 27 * 60
    match = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", raw)
    if not match:
        return defaults.get(category, (8 * 60, 22 * 60))
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    if end <= start:
        end += 24 * 60
    return start, end


def optimize_day(
    places: list,
    *,
    trip_date: date,
    profile_key: str,
    pace: str,
    transport_mode: str,
    interests: dict[str, float],
    preferred_start=None,
    preferred_end=None,
    previous_end: int | None = None,
) -> tuple[list[ScheduledActivity], int, int]:
    if not places:
        start, end = day_bounds(
            profile_key, "", preferred_start=preferred_start, preferred_end=preferred_end, previous_end=previous_end
        )
        return [], start, start
    time_sorted = sorted(
        places, key=lambda place: best_time(place.category, place.subcategory, place.name).preferred_start
    )
    # Preserve time-of-day groups while minimizing travel inside each group.
    ordered: list = []
    for bucket in ((0, 11 * 60), (11 * 60, 17 * 60), (17 * 60, 28 * 60)):
        group = [
            place
            for place in time_sorted
            if bucket[0] <= best_time(place.category, place.subcategory, place.name).preferred_start < bucket[1]
        ]
        ordered.extend(nearest_neighbor_order(group))
    matrix = route_matrix(ordered, transport_mode)
    day_start, day_end = day_bounds(
        profile_key,
        ordered[0].category,
        ordered[0].subcategory,
        preferred_start=preferred_start,
        preferred_end=preferred_end,
        previous_end=previous_end,
    )
    cursor = day_start
    schedule: list[ScheduledActivity] = []
    previous = None
    for place in ordered:
        window = best_time(place.category, place.subcategory, place.name)
        interest = interests.get(
            place.category, interests.get("museum" if place.category == "museums" else place.category, 0.5)
        )
        duration = estimate_duration(
            place.category, place.subcategory, name=place.name, tags=place.tags, interest=interest, pace=pace
        )
        travel = matrix[(previous.id, place.id)] if previous else 0
        break_minutes = 20 if schedule and pace == "relaxed" else 10 if schedule else 0
        raw_hours = (place.opening_hours or {}).get("raw")
        open_at, close_at = _opening_window(raw_hours, place.category)
        start = max(cursor + travel + break_minutes, open_at, window.earliest)
        # Soft preference: use it when it does not create an excessive idle gap.
        if window.preferred_start - start <= 90:
            start = max(start, window.preferred_start)
        # A food stop that no longer fits lunch becomes dinner instead of an
        # implausible 4 PM meal. The validator enforces the same windows.
        if place.category == "food" and 15 * 60 < start < 17 * 60:
            start = 17 * 60
        chosen_duration = duration.expected
        if raw_hours and start + chosen_duration > close_at and start + duration.minimum <= close_at:
            chosen_duration = duration.minimum
        end = start + chosen_duration
        # Category priors guide placement when a provider has no hours, but only
        # provider-supplied hours are treated as a hard opening-hours claim.
        feasible = end <= day_end and (not raw_hours or start >= open_at and end <= close_at)
        schedule.append(
            ScheduledActivity(
                place,
                start,
                end,
                duration.minimum,
                chosen_duration,
                duration.maximum,
                travel,
                break_minutes,
                window.label,
                feasible,
            )
        )
        cursor, previous = end, place
    # The full validator repeats these checks before the API may mark a plan READY.
    schedule_constraints(schedule)
    return schedule, day_start, cursor
