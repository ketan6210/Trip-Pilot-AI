from __future__ import annotations


def schedule_constraints(schedule: list) -> dict[str, bool]:
    """Evaluate the hard constraints shared by deterministic schedulers."""
    no_overlap = True
    enough_route_time = True
    opens = True
    previous_end = None
    for activity in schedule:
        if previous_end is not None:
            no_overlap = no_overlap and activity.start_minute >= previous_end
            enough_route_time = enough_route_time and activity.start_minute >= (
                previous_end + activity.travel_minutes + activity.break_minutes
            )
        opens = opens and activity.opening_feasible
        previous_end = activity.end_minute
    return {
        "no_overlap": no_overlap,
        "enough_route_time": enough_route_time,
        "opening_hours": opens,
    }
