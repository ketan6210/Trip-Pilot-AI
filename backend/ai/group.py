from sqlalchemy import delete, select, update

from backend.ai.schemas import PlanningConstraints, PreferenceInput
from backend.core.errors import AppError
from backend.models import (
    Group,
    GroupMember,
    GroupVote,
    PlanningBallot,
    PlanningPolicy,
    PlanVariant,
    Traveler,
    TravelerPlaceConstraint,
    TravelerPreference,
    Trip,
)


def invalidate_advanced(db, trip_id):
    if db.get(PlanningPolicy, trip_id) is not None:
        trip = db.scalar(
            select(Trip).where(Trip.id == trip_id).with_for_update().execution_options(populate_existing=True)
        )
        trip.trip_version += 1
        db.execute(update(PlanVariant).where(PlanVariant.trip_id == trip_id).values(status="stale"))


def policy_for(db, trip) -> PlanningConstraints:
    row = db.get(PlanningPolicy, trip.id)
    return PlanningConstraints.model_validate(row.constraints if row else {})


def group_constraints(db, trip, policy: PlanningConstraints) -> PlanningConstraints:
    values = policy.model_dump()
    must, veto = set(policy.must_place_ids), set(policy.veto_place_ids)
    must_categories, veto_categories = set(policy.must_categories), set(policy.veto_categories)
    dietary = set(policy.dietary_requirements)
    for traveler in trip.travelers:
        dietary.update(traveler.dietary_restrictions)
        values["wheelchair_required"] |= any(
            "wheelchair" in item.casefold() for item in traveler.accessibility_requirements
        )
        for pref in traveler.preferences:
            if pref.subcategory != "general":
                value = f"{pref.category}/{pref.subcategory}"
                if pref.is_must_do:
                    values["must_subcategories"].append(value)
                if pref.is_avoid:
                    values["veto_subcategories"].append(value)
                continue
            if pref.is_must_do:
                must_categories.add(pref.category)
            if pref.is_avoid:
                veto_categories.add(pref.category)
        for constraint in db.scalars(
            select(TravelerPlaceConstraint).where(TravelerPlaceConstraint.traveler_id == traveler.id)
        ):
            (must if constraint.kind == "must" else veto).add(constraint.place_id)
    if must & veto or must_categories & veto_categories:
        raise AppError(
            422,
            "GROUP_CONSTRAINT_CONFLICT",
            "A member's must-have conflicts with a veto. Resolve the group constraint before planning.",
            {"place_ids": sorted(must & veto), "categories": sorted(must_categories & veto_categories)},
        )
    values.update(
        must_place_ids=sorted(must),
        veto_place_ids=sorted(veto),
        must_categories=sorted(must_categories),
        veto_categories=sorted(veto_categories),
        dietary_requirements=sorted(dietary),
    )
    try:
        return PlanningConstraints.model_validate(values)
    except ValueError as exc:
        raise AppError(
            422,
            "UNSUPPORTED_GROUP_CONSTRAINT",
            "A saved group constraint conflicts or uses an unsupported category. Review the member preferences.",
        ) from exc


def save_preference(db, trip: Trip, user, payload: PreferenceInput):
    # Serialize profile allocation so two invited users cannot claim the same slot.
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update().execution_options(populate_existing=True))
    traveler = db.scalar(select(Traveler).where(Traveler.trip_id == trip.id, Traveler.user_id == user.id))
    if traveler is None:
        slots = list(db.scalars(select(Traveler).where(Traveler.trip_id == trip.id).order_by(Traveler.id)))
        traveler = next(
            (
                slot
                for index, slot in enumerate(slots)
                if slot.user_id is None and (index > 0 or user.id == trip.owner_id)
            ),
            None,
        )
    if traveler is None:
        raise AppError(
            409, "NO_TRAVELER_SLOT", "Increase the trip's traveler count before adding another traveler profile."
        )
    traveler.user_id, traveler.display_name = user.id, user.display_name
    traveler.preference_text = payload.description
    traveler.dietary_restrictions = payload.dietary_requirements
    traveler.accessibility_requirements = payload.mobility_requirements
    traveler.budget_preference = payload.budget_preference
    db.execute(delete(TravelerPreference).where(TravelerPreference.traveler_id == traveler.id))
    db.execute(delete(TravelerPlaceConstraint).where(TravelerPlaceConstraint.traveler_id == traveler.id))
    db.flush()
    categories = set(payload.interests) | set(payload.must_categories) | set(payload.veto_categories)
    db.add_all(
        [
            TravelerPreference(
                traveler_id=traveler.id,
                category=category,
                score=payload.interests.get(category, 5),
                is_must_do=category in payload.must_categories,
                is_avoid=category in payload.veto_categories,
            )
            for category in categories
        ]
    )
    db.add_all(
        [
            TravelerPlaceConstraint(traveler_id=traveler.id, place_id=place_id, kind=kind)
            for kind, ids in (("must", payload.must_place_ids), ("veto", payload.veto_place_ids))
            for place_id in set(ids)
        ]
    )
    trip.trip_version += 1
    db.execute(update(PlanVariant).where(PlanVariant.trip_id == trip.id).values(status="stale"))
    db.commit()
    return {"traveler_id": traveler.id, **payload.model_dump(mode="json")}


def vote_scores(db, trip) -> dict[int, float]:
    members = set(db.scalars(select(GroupMember.user_id).join(Group).where(Group.trip_id == trip.id)))
    totals: dict[int, float] = {}
    ballots = list(db.scalars(select(PlanningBallot).where(PlanningBallot.trip_id == trip.id)))
    for ballot in ballots:
        if ballot.user_id not in members:
            continue
        points = ballot.value + (1 / ballot.rank if ballot.rank else 0)
        targets = [ballot.place_id] if ballot.place_id else []
        if ballot.plan_id:
            plan = db.get(PlanVariant, ballot.plan_id)
            # A ballot is durable, but only the current plan-set is considered.
            if plan and plan.plan_set_version == trip.trip_version and plan.status != "stale":
                targets = plan.summary.get("ai_place_ids", [])
        for place_id in targets:
            totals[place_id] = totals.get(place_id, 0) + points
    for vote in db.scalars(select(GroupVote).join(Group).where(Group.trip_id == trip.id)):
        plan = db.get(PlanVariant, vote.plan_id)
        if vote.user_id in members and plan and plan.plan_set_version == trip.trip_version and plan.status != "stale":
            for place_id in plan.summary.get("ai_place_ids", []):
                totals[place_id] = totals.get(place_id, 0) + 1
    return {key: max(-1, min(1, value / max(1, len(members)))) for key, value in totals.items()}
