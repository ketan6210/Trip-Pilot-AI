"""CP-SAT selection, day assignment and directed routes with real time constraints."""

from datetime import timedelta
from math import ceil
from time import perf_counter

from ortools.sat.python import cp_model

from backend.ai.ranking import STRATEGIES
from backend.ai.schemas import ScheduleDay, ScheduleResult, Stop
from backend.core.errors import AppError
from backend.geography.distance import distance_km
from backend.settings import settings


def leg(first, second, mode):
    km = distance_km(first.latitude, first.longitude, second.latitude, second.longitude)
    speed = {"walking": 4.8, "public transit": 18, "mixed": 16, "taxi": 24, "rental car": 22}.get(mode, 16)
    return max(1, ceil(km / speed * 60 + (0 if mode == "walking" else 8))), km


def anchor_for(constraints):
    if constraints.anchor_latitude is None:
        return None
    from types import SimpleNamespace

    return SimpleNamespace(latitude=constraints.anchor_latitude, longitude=constraints.anchor_longitude)


def optimize(
    ranked, trip, constraints, strategy, activity_budget_cents, *, day_numbers=None, previous_sets=(), earliest=None
):
    started = perf_counter()
    profile = STRATEGIES[strategy]
    candidates = [row.candidate for row in ranked]
    ids = {row.place_id for row in candidates}
    missing = set(constraints.must_place_ids) - ids
    if missing:
        raise AppError(
            422,
            "MUST_HAVE_UNAVAILABLE",
            "A required place failed retrieval or hard filtering.",
            {"place_ids": sorted(missing)},
        )
    days = day_numbers or list(range(1, (trip.end_date - trip.start_date).days + 2))
    if not candidates or activity_budget_cents < 0:
        raise AppError(422, "NO_FEASIBLE_SCHEDULE", "No candidates or budget remain after hard constraints.")
    model = cp_model.CpModel()
    selected, starts, ends, arcs_by_day = {}, {}, {}, {}
    all_selected = {i: model.new_bool_var(f"selected_{i}") for i in range(len(candidates))}
    anchor = anchor_for(constraints)
    points = [anchor, *candidates]
    matrix = {}
    for i, first in enumerate(points):
        for j, second in enumerate(points):
            matrix[i, j] = (
                (0, 0) if i == j or first is None or second is None else leg(first, second, trip.transport_mode)
            )
    route_terms = []
    start_terms = []
    for day_number in days:
        day = trip.start_date + timedelta(days=day_number - 1)
        interval_vars = []
        for i, place in enumerate(candidates):
            key = day_number, i
            chosen = selected[key] = model.new_bool_var(f"visit_{day_number}_{i}")
            if place.category in constraints.daily_veto_categories.get(day_number, []):
                model.add(chosen == 0)
            start = starts[key] = model.new_int_var(
                constraints.day_start_minute, constraints.return_by_minute, f"start_{day_number}_{i}"
            )
            end = ends[key] = model.new_int_var(
                constraints.day_start_minute, constraints.return_by_minute, f"end_{day_number}_{i}"
            )
            interval_vars.append(
                model.new_optional_interval_var(
                    start, place.duration_minutes, end, chosen, f"interval_{day_number}_{i}"
                )
            )
            choices = []
            for window_index, (opens, closes) in enumerate(place.opening_windows.get(day, [])):
                low = max(
                    opens,
                    constraints.day_start_minute,
                    constraints.earliest_start_by_place.get(place.place_id, 0),
                    (earliest or {}).get(place.place_id, 0),
                )
                # The current scheduling policy includes evening nightlife and one dinner per day.
                if place.category == "nightlife":
                    low = max(low, 18 * 60)
                if place.category == "food":
                    low = max(low, 17 * 60)
                high = min(closes, constraints.return_by_minute)
                if high - low < place.duration_minutes:
                    continue
                window = model.new_bool_var(f"window_{day_number}_{i}_{window_index}")
                model.add(start >= low).only_enforce_if(window)
                model.add(end <= high).only_enforce_if(window)
                choices.append(window)
            model.add(sum(choices) == chosen)
            # Keep absent activities out of tie-breaking objective.
            active_start = model.new_int_var(0, 1620, f"active_start_{day_number}_{i}")
            model.add(active_start == start).only_enforce_if(chosen)
            model.add(active_start == 0).only_enforce_if(chosen.Not())
            start_terms.append(active_start)
        model.add_no_overlap(interval_vars)
        model.add(sum(selected[day_number, i] for i in range(len(candidates))) >= 1)
        model.add(sum(selected[day_number, i] for i in range(len(candidates))) <= profile["max_stops"])
        # One priced dinner replaces the dinner portion of the daily food reserve.
        model.add(sum(selected[day_number, i] for i, place in enumerate(candidates) if place.category == "food") == 1)
        arcs = []
        day_arcs = {}
        # No depot self-loop: every requested day contains at least one visit.
        for i in range(1, len(points)):
            arcs.append((i, i, selected[day_number, i - 1].Not()))
        for i in range(len(points)):
            for j in range(len(points)):
                if i == j:
                    continue
                travel, km = matrix[i, j]
                if km > constraints.max_leg_km:
                    continue
                arc = model.new_bool_var(f"arc_{day_number}_{i}_{j}")
                day_arcs[i, j] = arc
                arcs.append((i, j, arc))
                if i:
                    model.add_implication(arc, selected[day_number, i - 1])
                if j:
                    model.add_implication(arc, selected[day_number, j - 1])
                if i and j:
                    pause = 20 if strategy == "relaxed" else 10
                    model.add(starts[day_number, j - 1] >= ends[day_number, i - 1] + travel + pause).only_enforce_if(
                        arc
                    )
                elif j:
                    model.add(starts[day_number, j - 1] >= constraints.day_start_minute + travel).only_enforce_if(arc)
                elif i:
                    model.add(ends[day_number, i - 1] + travel <= constraints.return_by_minute).only_enforce_if(arc)
                route_terms.append(travel * arc)
        model.add_circuit(arcs)
        travel_limit = min(constraints.max_daily_travel_minutes, constraints.daily_travel_minutes.get(day_number, 720))
        if trip.transport_mode == "walking":
            travel_limit = min(travel_limit, trip.max_walking_per_day, *(t.walking_tolerance for t in trip.travelers))
        model.add(sum(matrix[i, j][0] * arc for (i, j), arc in day_arcs.items()) <= travel_limit)
        if day_number in constraints.daily_activity_budget:
            from backend.ai.constraints import cents

            model.add(
                sum(candidates[i].cost_cents * selected[day_number, i] for i in range(len(candidates)))
                <= cents(constraints.daily_activity_budget[day_number])
            )
        arcs_by_day[day_number] = day_arcs
    for i, place in enumerate(candidates):
        model.add(sum(selected[day, i] for day in days) == all_selected[i])
        if place.place_id in constraints.must_place_ids:
            model.add(all_selected[i] == 1)
    for category in constraints.must_categories:
        model.add(sum(all_selected[i] for i, place in enumerate(candidates) if place.category == category) >= 1)
    for subcategory in constraints.must_subcategories:
        model.add(
            sum(
                all_selected[i]
                for i, place in enumerate(candidates)
                if f"{place.category}/{place.subcategory}" == subcategory
            )
            >= 1
        )
    model.add(sum(place.cost_cents * all_selected[i] for i, place in enumerate(candidates)) <= activity_budget_cents)
    for previous in previous_sets:
        model.add(
            sum((1 if place.place_id in previous else -1) * all_selected[i] for i, place in enumerate(candidates))
            <= len(previous) - 1
        )
    max_cost = max((place.cost_cents for place in candidates), default=1) or 1
    utility = sum(
        (int((row.score + 0.25) * 1000) - profile["cost_penalty"] * round(row.candidate.cost_cents / max_cost * 100))
        * all_selected[i]
        for i, row in enumerate(ranked)
    )
    # Scaling keeps deterministic early-start tie-breaking below the main objective.
    model.maximize(10000 * (utility - profile["travel_penalty"] * sum(route_terms)) - sum(start_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = settings.ai_solver_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise AppError(
            422,
            "NO_FEASIBLE_SCHEDULE" if status == cp_model.INFEASIBLE else "SOLVER_LIMIT",
            "No validated schedule was found within the constraints and solver limit.",
            {"solver_status": solver.status_name(status)},
        )
    output, travel_total, km_total, cost_total = [], 0, 0.0, 0
    for day in days:
        successors = {i: j for (i, j), arc in arcs_by_day[day].items() if solver.value(arc)}
        node, stops = successors[0], []
        previous = 0
        while node:
            place, ranked_place = candidates[node - 1], ranked[node - 1]
            minutes, km = matrix[previous, node]
            travel_total += minutes
            km_total += km
            cost_total += place.cost_cents
            stops.append(
                Stop(
                    place_id=place.place_id,
                    start_minute=solver.value(starts[day, node - 1]),
                    end_minute=solver.value(ends[day, node - 1]),
                    travel_minutes=minutes,
                    cost_cents=place.cost_cents,
                    reason="; ".join(ranked_place.reason_codes),
                )
            )
            previous, node = node, successors[node]
        back, km = matrix[previous, 0]
        travel_total += back
        km_total += km
        output.append(
            ScheduleDay(
                day_number=day, date=trip.start_date + timedelta(days=day - 1), stops=stops, return_travel_minutes=back
            )
        )
    return ScheduleResult(
        days=output,
        status=solver.status_name(status),
        objective=solver.objective_value,
        best_bound=solver.best_objective_bound,
        solver_seconds=round(perf_counter() - started, 4),
        travel_minutes=travel_total,
        distance_km=round(km_total, 4),
        activity_cost_cents=cost_total,
    )


def validate_schedule(schedule, candidates, trip, constraints, budget_cents, strategy, *, expected_days=None):
    """Independent check: never treat a solver status alone as quality validation."""
    by_id = {place.place_id: place for place in candidates}
    seen, violations = set(), []
    total, categories = 0, set()
    anchor = anchor_for(constraints)
    for day in schedule.days:
        previous, previous_end, daily_travel = anchor, constraints.day_start_minute, 0
        daily_cost = 0
        if not day.stops or day.date != trip.start_date + timedelta(days=day.day_number - 1):
            violations.append("day_coverage")
        if len(day.stops) > STRATEGIES[strategy]["max_stops"]:
            violations.append("daily_activity_limit")
        if sum(by_id[stop.place_id].category == "food" for stop in day.stops if stop.place_id in by_id) != 1:
            violations.append("daily_meal")
        for index, stop in enumerate(day.stops):
            place = by_id.get(stop.place_id)
            if place is None:
                violations.append("unsupported_place")
                continue
            if stop.place_id in seen:
                violations.append("duplicate_place")
            seen.add(stop.place_id)
            categories.add(place.category)
            if place.place_id in constraints.veto_place_ids or place.category in constraints.veto_categories:
                violations.append("veto")
            if place.category in constraints.daily_veto_categories.get(day.day_number, []):
                violations.append("daily_veto")
            if (place.category == "food" and stop.start_minute < 1020) or (
                place.category == "nightlife" and stop.start_minute < 1080
            ):
                violations.append("evening_activity_time")
            travel, km = leg(previous, place, trip.transport_mode) if previous else (0, 0)
            pause = (20 if strategy == "relaxed" else 10) if index else 0
            if km > constraints.max_leg_km:
                violations.append("geography")
            if (
                stop.start_minute < previous_end + travel + pause
                or stop.end_minute - stop.start_minute != place.duration_minutes
                or stop.start_minute < constraints.day_start_minute
            ):
                violations.append("schedule_time")
            if stop.start_minute < constraints.earliest_start_by_place.get(place.place_id, 0):
                violations.append("earliest_start")
            if not any(
                stop.start_minute >= a and stop.end_minute <= b for a, b in place.opening_windows.get(day.date, [])
            ):
                violations.append("opening_hours")
            if stop.cost_cents != place.cost_cents:
                violations.append("ungrounded_cost")
            if stop.travel_minutes != travel:
                violations.append("ungrounded_travel")
            daily_travel += travel
            daily_cost += place.cost_cents
            previous, previous_end = place, stop.end_minute
        back, back_km = leg(previous, anchor, trip.transport_mode) if previous and anchor else (0, 0)
        if back_km > constraints.max_leg_km:
            violations.append("geography")
        if day.return_travel_minutes != back:
            violations.append("ungrounded_travel")
        daily_travel += back
        if previous_end + back > constraints.return_by_minute:
            violations.append("return_by")
        if daily_travel > min(
            constraints.max_daily_travel_minutes, constraints.daily_travel_minutes.get(day.day_number, 720)
        ):
            violations.append("travel_limit")
        if trip.transport_mode == "walking" and daily_travel > min(
            trip.max_walking_per_day, *(t.walking_tolerance for t in trip.travelers)
        ):
            violations.append("walking_limit")
        if day.day_number in constraints.daily_activity_budget:
            from backend.ai.constraints import cents

            if daily_cost > cents(constraints.daily_activity_budget[day.day_number]):
                violations.append("daily_budget")
        total += daily_cost
    wanted_days = set(expected_days or range(1, (trip.end_date - trip.start_date).days + 2))
    if {day.day_number for day in schedule.days} != wanted_days or len(schedule.days) != len(wanted_days):
        violations.append("day_coverage")
    if set(constraints.must_place_ids) - seen or set(constraints.must_categories) - categories:
        violations.append("must_have")
    subcategories = {f"{by_id[key].category}/{by_id[key].subcategory}" for key in seen if key in by_id}
    if set(constraints.must_subcategories) - subcategories or set(constraints.veto_subcategories) & subcategories:
        violations.append("subcategory_constraint")
    if total > budget_cents:
        violations.append("budget")
    return sorted(set(violations))
