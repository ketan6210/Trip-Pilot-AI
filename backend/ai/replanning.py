import json
import re
from copy import deepcopy
from decimal import Decimal
from math import floor

from sqlalchemy import select

from backend.ai.budget import budget_result
from backend.ai.constraints import cents, prepare_candidates
from backend.ai.group import group_constraints, policy_for, vote_scores
from backend.ai.optimizer import anchor_for, leg, optimize, validate_schedule
from backend.ai.persistence import append_version, latest_version, persist_costs, persist_days
from backend.ai.pipeline import loaded_trip, plan_data
from backend.ai.ranking import rank_candidates
from backend.ai.retrieval import balanced_pool, retrieve
from backend.ai.schemas import EditCommand, PlanningConstraints, ScheduleResult
from backend.core.errors import AppError
from backend.llm.providers import LLMUnavailable
from backend.llm.service import structured
from backend.llm.telemetry import Trace, tracing
from backend.models import ItineraryVersion, PlanVariant
from backend.settings import settings


def parse_edit(text, candidates):
    lowered = text.casefold().strip()
    day_match = re.search(r"\bday\s+(\d+)\b", lowered)
    day = int(day_match.group(1)) if day_match else None
    categories = {
        "museum": "museums",
        "museums": "museums",
        "nightlife": "nightlife",
        "dinner": "food",
        "restaurant": "food",
        "parks": "nature",
    }
    category = next((value for word, value in categories.items() if re.search(rf"\b{word}\b", lowered)), None)
    matches = [candidate for candidate in candidates if candidate.name.casefold() in lowered]
    if len(matches) > 1:
        raise AppError(422, "AMBIGUOUS_EDIT", "Name one place per edit, or provide its place_id.")
    place_id = matches[0].place_id if matches else None
    if re.search(r"\b(add|include|remove|drop|exclude)\b", lowered) and not place_id:
        target = re.sub(
            r"\b(?:add|include|remove|drop|exclude|the|a|an|all|more|on|to|from|please)\b|\bday\s+\d+\b", "", lowered
        ).strip(" .")
        if not category or target not in categories:
            raise AppError(
                422,
                "EDIT_TARGET_UNKNOWN",
                "That venue was not found in the provider results. Use an exact listed place name or a supported category.",
            )
    if re.search(r"\b(remove|drop|exclude)\b", lowered) and (category or place_id):
        return EditCommand(action="remove", day_number=day, place_id=place_id, category=category)
    if re.search(r"\b(add|include)\b", lowered) and (category or place_id):
        return EditCommand(action="add", day_number=day, place_id=place_id, category=category)
    if "cheaper" in lowered:
        return EditCommand(action="cheaper", day_number=day)
    if "walking" in lowered and re.search(r"\b(reduce|less|lower)\b", lowered):
        return EditCommand(action="reduce_walking", day_number=day)
    if "later" in lowered and (category or place_id):
        return EditCommand(action="move_later", day_number=day, place_id=place_id, category=category)
    try:
        command = structured(
            EditCommand,
            [
                {
                    "role": "system",
                    "content": "Translate one requested edit into the schema. Use only supplied place IDs and explicit day numbers. No factual values may be invented. If the venue is absent, use null place_id and category.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "text": text,
                            "places": [
                                {"id": item.place_id, "name": item.name, "category": item.category}
                                for item in candidates
                            ],
                        }
                    ),
                },
            ],
            purpose="partial_edit",
        )
        if command.place_id and command.place_id not in {row.place_id for row in candidates}:
            raise ValueError("Unsupported place")
        if command.day_number != day:
            raise ValueError("Unsupported day")
        return command
    except (LLMUnavailable, ValueError) as exc:
        raise AppError(
            422,
            "EDIT_NEEDS_CLARIFICATION",
            "Use a known place/category and an edit such as 'Remove museum', 'Make day 2 cheaper', or 'Move dinner later'.",
        ) from exc


def editable(db, plan_id, expected_version):
    plan = db.scalar(
        select(PlanVariant).where(PlanVariant.id == plan_id).with_for_update().execution_options(populate_existing=True)
    )
    if not plan or plan.summary.get("engine") != "cp_sat":
        raise AppError(
            409, "OPTIMIZED_PLAN_REQUIRED", "Generate an advanced itinerary before using versioned schedule edits."
        )
    if latest_version(db, plan.id) != expected_version:
        raise AppError(409, "VERSION_CONFLICT", "The itinerary has changed. Reload it before editing.")
    trip = loaded_trip(db, plan.trip_id)
    if plan.status == "stale" or plan.plan_set_version != trip.trip_version:
        raise AppError(409, "STALE_ITINERARY", "Trip constraints changed; generate a current itinerary.")
    return plan, trip


def _scope(command, schedule, candidates):
    by_id = {candidate.place_id: candidate for candidate in candidates}

    def matches(stop):
        return (
            stop.place_id == command.place_id
            if command.place_id
            else by_id.get(stop.place_id) and by_id[stop.place_id].category == command.category
        )

    if command.day_number:
        if command.day_number not in {day.day_number for day in schedule.days}:
            raise AppError(422, "INVALID_EDIT_DAY", "That day is outside this trip.")
        return {command.day_number}, matches
    if command.action in {"remove", "move_later"}:
        days = {day.day_number for day in schedule.days if any(matches(stop) for stop in day.stops)}
    elif command.action == "add":
        days = {min(schedule.days, key=lambda day: (len(day.stops), day.day_number)).day_number}
    elif command.action == "reduce_walking":
        days = {day.day_number for day in schedule.days if sum(stop.travel_minutes for stop in day.stops) > 0}
    else:
        raise AppError(422, "EDIT_DAY_REQUIRED", "Specify which day to make cheaper.")
    if not days:
        raise AppError(422, "EDIT_TARGET_NOT_FOUND", "No matching scheduled activity was found.")
    return days, matches


def recompute_totals(schedule, candidates, trip, constraints):
    by_id = {row.place_id: row for row in candidates}
    distance, travel, cost = 0.0, 0, 0
    anchor = anchor_for(constraints)
    for day in schedule.days:
        previous = anchor
        for stop in day.stops:
            place = by_id[stop.place_id]
            minutes, km = leg(previous, place, trip.transport_mode) if previous else (0, 0)
            travel += minutes
            distance += km
            cost += place.cost_cents
            previous = place
        if anchor and previous:
            minutes, km = leg(previous, anchor, trip.transport_mode)
            travel += minutes
            distance += km
    return schedule.model_copy(
        update={"travel_minutes": travel, "distance_km": round(distance, 4), "activity_cost_cents": cost}
    )


def replan(db, plan_id, user_id, payload):
    trace = Trace()
    trip_id = None
    try:
        with tracing(trace):
            plan, trip = editable(db, plan_id, payload.expected_version)
            trip_id, initial_trip_version = trip.id, trip.trip_version
            previous = deepcopy(plan.summary["ai"])
            constraints = PlanningConstraints.model_validate(previous["constraints"])
            # Enforce the current group's mandatory and veto rules in addition to edit memory.
            current = group_constraints(db, trip, policy_for(db, trip))
            constraints.must_place_ids = sorted(set(constraints.must_place_ids) | set(current.must_place_ids))
            constraints.veto_place_ids = sorted(set(constraints.veto_place_ids) | set(current.veto_place_ids))
            schedule = ScheduleResult.model_validate(previous["schedule"])
            with trace.stage("retrieve_edit_candidates"):
                _, records = retrieve(db, trip, constraints)
                candidates, _ = prepare_candidates(records, trip, constraints)
            with trace.stage("extract_scoped_edit"):
                command = payload.command or parse_edit(payload.text, candidates)
                if command.action in {"add", "remove", "move_later"} and not (command.place_id or command.category):
                    raise AppError(422, "EDIT_TARGET_REQUIRED", "Specify a known place or activity category.")
                if command.place_id and command.place_id not in {row.place_id for row in candidates}:
                    raise AppError(422, "EDIT_TARGET_UNAVAILABLE", "That place is not an eligible provider result.")
                affected, matches = _scope(command, schedule, candidates)
                fixed_days = [day for day in schedule.days if day.day_number not in affected]
                fixed_ids = {stop.place_id for day in fixed_days for stop in day.stops}
                remove_ids = {
                    stop.place_id
                    for day in schedule.days
                    if day.day_number in affected
                    for stop in day.stops
                    if matches(stop)
                }
                if command.action == "remove":
                    if remove_ids & set(constraints.must_place_ids):
                        raise AppError(
                            422,
                            "MANDATORY_EDIT_CONFLICT",
                            "A required activity cannot be removed until its must-have constraint is changed.",
                        )
                    if not remove_ids:
                        raise AppError(422, "EDIT_TARGET_NOT_FOUND", "No matching activity exists on that day.")
                    constraints.veto_place_ids = sorted(set(constraints.veto_place_ids) | remove_ids)
                    if command.category and not command.place_id:
                        for day_number in affected:
                            constraints.daily_veto_categories[day_number] = sorted(
                                set(constraints.daily_veto_categories.get(day_number, [])) | {command.category}
                            )
                elif command.action == "add":
                    if command.place_id:
                        if command.place_id in fixed_ids:
                            raise AppError(409, "ALREADY_SCHEDULED", "That place is already scheduled on another day.")
                        constraints.must_place_ids = sorted(set(constraints.must_place_ids) | {command.place_id})
                    else:
                        constraints.must_categories = sorted(set(constraints.must_categories) | {command.category})
                elif command.action == "cheaper":
                    for day in schedule.days:
                        if day.day_number in affected:
                            constraints.daily_activity_budget[day.day_number] = (
                                Decimal(sum(stop.cost_cents for stop in day.stops) * 90 // 100) / 100
                            )
                elif command.action == "reduce_walking":
                    for day in schedule.days:
                        if day.day_number in affected:
                            constraints.daily_travel_minutes[day.day_number] = floor(
                                (sum(stop.travel_minutes for stop in day.stops) + day.return_travel_minutes) * 0.75
                            )
                elif command.action == "move_later":
                    if not remove_ids:
                        raise AppError(422, "EDIT_TARGET_NOT_FOUND", "No matching activity exists on that day.")
                    constraints.must_place_ids = sorted(set(constraints.must_place_ids) | remove_ids)
                    for day in schedule.days:
                        for stop in day.stops:
                            if day.day_number in affected and stop.place_id in remove_ids:
                                constraints.earliest_start_by_place[stop.place_id] = command.earliest_minute or min(
                                    1560, stop.start_minute + 60
                                )
                constraints = PlanningConstraints.model_validate(constraints.model_dump())
                candidates, _ = prepare_candidates(records, trip, constraints)
                by_id = {row.place_id: row for row in candidates}
                if fixed_ids - by_id.keys():
                    raise AppError(
                        409,
                        "UNCHANGED_DAY_INVALID",
                        "Provider data invalidated another day. Full regeneration is required; the itinerary was preserved.",
                    )
            with trace.stage("partial_schedule_optimization"):
                local = constraints.model_copy(deep=True)
                local.must_place_ids = [key for key in constraints.must_place_ids if key not in fixed_ids]
                covered_categories = {by_id[key].category for key in fixed_ids}
                local.must_categories = [
                    key
                    for key in constraints.must_categories
                    if key not in covered_categories or command.action == "add" and key == command.category
                ]
                covered_subcategories = {f"{by_id[key].category}/{by_id[key].subcategory}" for key in fixed_ids}
                local.must_subcategories = [
                    key for key in constraints.must_subcategories if key not in covered_subcategories
                ]
                ranked = rank_candidates(
                    [row for row in candidates if row.place_id not in fixed_ids],
                    trip.travelers,
                    constraints,
                    plan.plan_type,
                    votes=vote_scores(db, trip),
                )
                # Retain current choices when equally suitable, without treating them as hard constraints.
                old_ids = {stop.place_id for day in schedule.days if day.day_number in affected for stop in day.stops}
                for row in ranked:
                    row.score += 0.15 if row.candidate.place_id in old_ids else 0
                pool = balanced_pool(
                    ranked, settings.ai_candidate_limit, local.must_place_ids, len(affected), local.must_subcategories
                )
                available = (
                    cents(trip.total_budget)
                    - sum(previous["fixed_costs_cents"].values())
                    - sum(by_id[key].cost_cents for key in fixed_ids)
                )
                partial = optimize(pool, trip, local, plan.plan_type, available, day_numbers=sorted(affected))
                combined = partial.model_copy(
                    update={"days": sorted([*fixed_days, *partial.days], key=lambda day: day.day_number)}
                )
                combined = recompute_totals(combined, candidates, trip, constraints)
                violations = validate_schedule(
                    combined,
                    candidates,
                    trip,
                    constraints,
                    cents(trip.total_budget) - sum(previous["fixed_costs_cents"].values()),
                    plan.plan_type,
                )
                if violations:
                    raise AppError(
                        422,
                        "EDIT_NOT_FEASIBLE",
                        "The edit cannot satisfy all hard constraints; the itinerary was preserved.",
                        {"violations": violations},
                    )
            with trace.stage("versioned_persistence"):
                # Provider calls may commit cache updates; reacquire locks and check both versions.
                plan, trip = editable(db, plan_id, payload.expected_version)
                if trip.trip_version != initial_trip_version:
                    raise AppError(409, "TRIP_CHANGED", "Trip constraints changed during this edit.")
                budget = budget_result(combined, candidates, previous["fixed_costs_cents"], trip)
                all_ranked = rank_candidates(
                    candidates, trip.travelers, constraints, plan.plan_type, votes=vote_scores(db, trip)
                )
                data = plan_data(
                    trip,
                    plan.plan_type,
                    combined,
                    candidates,
                    all_ranked,
                    constraints,
                    previous["fixed_costs_cents"],
                    previous["cost_sources"],
                    budget,
                    [f"Partial edit: {command.action}"],
                )
                data["title"] = plan.title
                for key, value in data.items():
                    setattr(plan, key, value)
                persist_days(db, plan, combined, candidates, only_days=affected)
                persist_costs(db, plan, budget, previous["cost_sources"])
                version = append_version(db, plan, user_id, payload.text or command.model_dump_json())
            run = trace.save(
                db,
                trip_id=trip.id,
                user_id=user_id,
                operation="replan",
                extra={
                    "affected_days": sorted(affected),
                    "unchanged_days": len(fixed_days),
                    "version": version.version,
                    "constraint_violation_rate": 0.0,
                },
            )
            db.commit()
            return plan, run
    except Exception as exc:
        db.rollback()
        if trip_id:
            trace.save(
                db,
                trip_id=trip_id,
                user_id=user_id,
                operation="replan",
                status="failed",
                error_code=exc.code if isinstance(exc, AppError) else "EDIT_FAILED",
            )
            db.commit()
        raise


def rollback(db, plan_id, user_id, payload):
    plan, trip = editable(db, plan_id, payload.expected_version)
    version = db.scalar(
        select(ItineraryVersion).where(
            ItineraryVersion.plan_id == plan.id, ItineraryVersion.version == payload.target_version
        )
    )
    if not version or version.trip_version != trip.trip_version:
        raise AppError(
            409, "ROLLBACK_VERSION_UNAVAILABLE", "That version is unavailable under the current trip constraints."
        )
    snapshot = deepcopy(version.snapshot)
    ai = snapshot["summary"]["ai"]
    constraints = PlanningConstraints.model_validate(ai["constraints"])
    _, records = retrieve(db, trip, constraints)
    candidates, _ = prepare_candidates(records, trip, constraints)
    schedule = ScheduleResult.model_validate(ai["schedule"])
    violations = validate_schedule(
        schedule,
        candidates,
        trip,
        constraints,
        cents(trip.total_budget) - sum(ai["fixed_costs_cents"].values()),
        plan.plan_type,
    )
    if violations:
        raise AppError(
            422,
            "ROLLBACK_NOT_FEASIBLE",
            "Current provider data no longer validates that version.",
            {"violations": violations},
        )
    plan, trip = editable(db, plan_id, payload.expected_version)
    old_days = {row["day_number"]: row for row in plan.summary["ai"]["schedule"]["days"]}
    changed = {day.day_number for day in schedule.days if old_days.get(day.day_number) != day.model_dump(mode="json")}
    for key, value in snapshot["plan"].items():
        setattr(plan, key, value)
    plan.summary = snapshot["summary"]
    persist_days(db, plan, schedule, candidates, only_days=changed)
    persist_costs(db, plan, ai["budget"], ai["cost_sources"])
    append_version(db, plan, user_id, f"Rollback to version {payload.target_version}")
    db.commit()
    return plan
