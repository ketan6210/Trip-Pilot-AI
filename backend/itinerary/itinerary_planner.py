from __future__ import annotations

from datetime import timedelta

from backend.itinerary.day_builder import assign_clusters_to_days
from backend.optimization.schedule_optimizer import optimize_day


def build_daily_schedule(
    places: list, trip, profile, interests: dict[str, float] | None = None, preferred_start=None, preferred_end=None
) -> list[dict]:
    day_count = (trip.end_date - trip.start_date).days + 1
    groups = assign_clusters_to_days(places, day_count)
    result = []
    previous_end = None
    for index, day_places in enumerate(groups):
        trip_date = trip.start_date + timedelta(days=index)
        schedule, departure, finish = optimize_day(
            day_places,
            trip_date=trip_date,
            profile_key=profile.key,
            pace=profile.pace,
            transport_mode=trip.transport_mode,
            interests=interests or {},
            preferred_start=preferred_start,
            preferred_end=preferred_end,
            previous_end=previous_end,
        )
        result.append(
            {"date": trip_date, "schedule": schedule, "hotel_departure_minute": departure, "finish_minute": finish}
        )
        previous_end = finish
    return result
