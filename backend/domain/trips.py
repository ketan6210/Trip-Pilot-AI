from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from backend.domain.normalization import TripRequest
from backend.models import (
    Group,
    GroupMember,
    PlanVariant,
    Traveler,
    TravelerPreference,
    Trip,
    TripStateRecord,
    User,
    Vote,
)
from backend.schemas import TripCreate, TripUpdate


def attach_owner(db: Session, trip: Trip, owner_id: str) -> None:
    trip.owner_id = owner_id
    db.flush()
    group = Group(trip_id=trip.id)
    db.add(group)
    db.flush()
    db.add(GroupMember(group_id=group.id, user_id=owner_id, role="OWNER"))


def create_trip(db: Session, payload: TripCreate, owner_id: str) -> Trip:
    trip = Trip(**payload.model_dump())
    db.add(trip)
    attach_owner(db, trip, owner_id)
    db.commit()
    db.refresh(trip)
    return trip


def update_trip(db: Session, trip_id: int, payload: TripUpdate) -> Trip:
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == trip_id)
        .options(selectinload(Trip.travelers), selectinload(Trip.budget))
        .with_for_update()
    )
    if not trip:
        raise HTTPException(404, "Trip not found")
    changes = payload.model_dump(exclude_none=True)
    try:
        TripCreate.model_validate({**{key: getattr(trip, key) for key in TripCreate.model_fields}, **changes})
    except ValidationError as exc:
        raise HTTPException(422, "The updated trip has invalid dates, budget or constraints") from exc
    proposed_start = changes.get("start_date", trip.start_date)
    proposed_end = changes.get("end_date", trip.end_date)
    if proposed_end < proposed_start:
        raise HTTPException(422, "end_date must be on or after start_date")
    old_budget = float(trip.total_budget)
    new_count = int(changes.get("traveler_count", trip.traveler_count))
    for key, value in changes.items():
        setattr(trip, key, value.upper() if key == "currency" else value)
    current = sorted(trip.travelers, key=lambda traveler: traveler.id)
    if new_count > len(current):
        for index in range(len(current), new_count):
            db.add(Traveler(trip_id=trip.id, display_name=f"Traveler {index + 1}"))
    elif new_count < len(current):
        removed = current[new_count:]
        db.execute(delete(Vote).where(Vote.traveler_id.in_([traveler.id for traveler in removed])))
        for traveler in removed:
            db.delete(traveler)
    if trip.budget and "total_budget" in changes and old_budget > 0:
        ratio = float(trip.total_budget) / old_budget
        trip.budget.categories = {key: round(float(value) * ratio, 2) for key, value in trip.budget.categories.items()}
    trip.status = "draft"
    trip.trip_version += 1
    db.execute(
        update(PlanVariant).where(PlanVariant.trip_id == trip.id, PlanVariant.status != "stale").values(status="stale")
    )
    state_record = db.scalar(select(TripStateRecord).where(TripStateRecord.trip_id == trip.id))
    if state_record:
        state = dict(state_record.state or {})
        state.update(
            {
                "destination": trip.destination_city,
                "origin": trip.origin,
                "start_date": trip.start_date.isoformat(),
                "end_date": trip.end_date.isoformat(),
                "traveler_count": trip.traveler_count,
                "total_budget": float(trip.total_budget),
                "budget_amount": float(trip.total_budget),
                "currency": trip.currency,
                "budget_currency": trip.currency,
                "pace": trip.pace,
                "transport_mode": trip.transport_mode,
                "walking_limit": trip.max_walking_per_day,
                "hotel_style": trip.hotel_style,
                "flight_included": trip.flight_included,
            }
        )
        state_record.state = state
    db.commit()
    db.refresh(trip)
    return trip


def create_from_request(db: Session, payload: "TripRequest", user: User) -> Trip:
    validated = payload.to_trip_create()
    trip = Trip(**validated.model_dump())
    db.add(trip)
    attach_owner(db, trip, user.id)
    for index in range(trip.traveler_count):
        traveler = Traveler(
            trip_id=trip.id,
            user_id=user.id if index == 0 else None,
            display_name=user.display_name if index == 0 else f"Traveler {index + 1}",
            walking_tolerance=trip.max_walking_per_day,
            dietary_restrictions=payload.dietary_preferences,
            accessibility_requirements=payload.mobility_preferences,
        )
        db.add(traveler)
        db.flush()
        categories = {"parks": "nature", "museum": "museums"}
        preferences = {categories.get(item, item): 8 for item in payload.interests}
        if payload.nightlife_preference is not None:
            preferences["nightlife"] = 8 if payload.nightlife_preference else 0
        for category, score in preferences.items():
            db.add(TravelerPreference(traveler_id=traveler.id, category=category, score=score, is_avoid=score == 0))
    db.commit()
    return trip
