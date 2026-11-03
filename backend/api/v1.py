from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.core.auth import current_user, require_plan, require_trip
from backend.core.errors import AppError
from backend.db import get_db
from backend.domain.itineraries import read_itinerary, rename_itinerary, select_itinerary
from backend.domain.normalization import NormalizeInput, NormalizeRead, TripRequest, normalize
from backend.domain.trips import create_from_request, update_trip
from backend.models import Group, GroupMember, GroupVote, PlanVariant, Trip, User
from backend.planning import generate_plan_variants
from backend.schemas import PlanRead, TripRead, TripUpdate

router = APIRouter(dependencies=[Depends(current_user)])


class ActivityRead(BaseModel):
    id: int
    place_id: int
    place: str
    start_time: str
    end_time: str
    duration_minutes: int
    estimated_cost: Decimal | None
    currency: str
    latitude: float
    longitude: float
    category: str
    reason_for_selection: str


class DayRead(BaseModel):
    id: int
    day_number: int
    date: date
    activities: list[ActivityRead]


class BudgetRead(BaseModel):
    currency: str
    estimated_total: Decimal
    remaining_budget: Decimal
    category_breakdown: dict[str, Decimal]
    per_person_cost: Decimal
    pricing_complete: bool
    unknown_categories: list[str]


class ItineraryRead(PlanRead):
    strategy: str
    days: list[DayRead]
    budget: BudgetRead


class ItineraryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: Literal["EDITOR", "VIEWER"] = "VIEWER"


class MemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    role: str


class GroupRead(BaseModel):
    id: str
    trip_id: int
    members: list[MemberRead]


class VoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itinerary_id: int = Field(gt=0)


class VoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    group_id: str
    user_id: str
    plan_id: int


class RouteLookup(BaseModel):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)


class LocationRead(BaseModel):
    name: str
    country: str | None
    latitude: float
    longitude: float


class RouteLocationsRead(BaseModel):
    origin: LocationRead | None
    destination: LocationRead | None


@router.post("/locations/resolve", response_model=RouteLocationsRead, tags=["locations"])
async def resolve_locations(payload: RouteLookup):
    from backend.providers.locations import resolve_route_locations

    return await resolve_route_locations(payload.origin, payload.destination)


@router.post("/trips/normalize", response_model=NormalizeRead, tags=["trips"])
def normalize_trip(payload: NormalizeInput):
    return normalize(payload)


@router.post("/trips", response_model=TripRead, status_code=201, tags=["trips"])
def create_trip(payload: TripRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return create_from_request(db, payload, user)


@router.get("/trips", response_model=list[TripRead], tags=["trips"])
def list_trips(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    shared = select(Group.trip_id).join(GroupMember).where(GroupMember.user_id == user.id)
    return list(
        db.scalars(
            select(Trip)
            .where((Trip.owner_id == user.id) | Trip.id.in_(shared))
            .order_by(Trip.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/trips/{trip_id}", response_model=TripRead, tags=["trips"])
def get_trip(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return require_trip(db, trip_id, user)


@router.patch("/trips/{trip_id}", response_model=TripRead, tags=["trips"])
def patch_trip(trip_id: int, payload: TripUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user, edit=True)
    return update_trip(db, trip_id, payload)


@router.post("/trips/{trip_id}/generate", response_model=list[ItineraryRead], tags=["itineraries"])
def generate(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user, edit=True)
    try:
        plans = generate_plan_variants(db, trip_id, user.id)
    except ValueError as exc:
        raise AppError(422, "INFEASIBLE_TRIP", str(exc)) from exc
    return [read_itinerary(db, plan) for plan in plans]


@router.get("/trips/{trip_id}/itineraries", response_model=list[ItineraryRead], tags=["itineraries"])
def itineraries(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    trip = require_trip(db, trip_id, user)
    plans = db.scalars(
        select(PlanVariant)
        .where(
            PlanVariant.trip_id == trip.id,
            PlanVariant.plan_set_version == trip.trip_version,
            PlanVariant.status != "stale",
        )
        .order_by(PlanVariant.id)
    )
    return [read_itinerary(db, plan) for plan in plans]


@router.get("/itineraries/{itinerary_id}", response_model=ItineraryRead, tags=["itineraries"])
def itinerary(itinerary_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return read_itinerary(db, require_plan(db, itinerary_id, user))


@router.patch("/itineraries/{itinerary_id}", response_model=ItineraryRead, tags=["itineraries"])
def patch_itinerary(
    itinerary_id: int, payload: ItineraryPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    plan = require_plan(db, itinerary_id, user, edit=True)
    return read_itinerary(db, rename_itinerary(db, plan, payload.title))


@router.post("/itineraries/{itinerary_id}/select", response_model=ItineraryRead, tags=["itineraries"])
def select_plan(itinerary_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    plan = require_plan(db, itinerary_id, user, owner=True)
    return read_itinerary(db, select_itinerary(db, plan))


@router.get("/trips/{trip_id}/group", response_model=GroupRead, tags=["groups"])
def trip_group(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user)
    group = db.scalar(select(Group).where(Group.trip_id == trip_id))
    if not group:
        raise AppError(404, "GROUP_NOT_FOUND", "Group not found.")
    return {
        "id": group.id,
        "trip_id": trip_id,
        "members": list(db.scalars(select(GroupMember).where(GroupMember.group_id == group.id))),
    }


def permitted_group(db: Session, group_id: str, user: User, owner: bool = False) -> Group:
    group = db.get(Group, group_id)
    if not group:
        raise AppError(404, "GROUP_NOT_FOUND", "Group not found.")
    require_trip(db, group.trip_id, user, owner=owner)
    return group


@router.put("/groups/{group_id}/members", response_model=MemberRead, tags=["groups"])
def invite_member(
    group_id: str, payload: MemberInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    permitted_group(db, group_id, user, owner=True)
    invitee = db.scalar(select(User).where(User.email == payload.email.strip().casefold()))
    if not invitee:
        raise AppError(404, "USER_NOT_FOUND", "The invitee must register an account first.")
    if invitee.id == user.id:
        raise AppError(409, "OWNER_ROLE_FIXED", "The trip owner's role cannot be changed.")
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == invitee.id))
    if member:
        member.role = payload.role
    else:
        member = GroupMember(group_id=group_id, user_id=invitee.id, role=payload.role)
        db.add(member)
    db.commit()
    return member


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204, tags=["groups"])
def remove_member(group_id: str, user_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    permitted_group(db, group_id, user, owner=True)
    if user_id == user.id:
        raise AppError(409, "OWNER_ROLE_FIXED", "The trip owner cannot be removed.")
    db.execute(delete(GroupVote).where(GroupVote.group_id == group_id, GroupVote.user_id == user_id))
    db.execute(delete(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    db.commit()


@router.post("/groups/{group_id}/votes", response_model=VoteRead, tags=["groups"])
def vote(group_id: str, payload: VoteInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    group = permitted_group(db, group_id, user)
    plan = require_plan(db, payload.itinerary_id, user)
    if plan.trip_id != group.trip_id:
        raise AppError(422, "ITINERARY_GROUP_MISMATCH", "This itinerary belongs to another group.")
    if plan.status == "stale" or plan.plan_set_version != plan.trip.trip_version:
        raise AppError(409, "STALE_ITINERARY", "Vote on a current itinerary.")
    row = db.scalar(select(GroupVote).where(GroupVote.group_id == group_id, GroupVote.user_id == user.id))
    if row:
        row.plan_id = plan.id
    else:
        row = GroupVote(group_id=group_id, user_id=user.id, plan_id=plan.id)
        db.add(row)
    db.commit()
    return row


@router.get("/groups/{group_id}/votes", response_model=list[VoteRead], tags=["groups"])
def votes(group_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    permitted_group(db, group_id, user)
    return list(
        db.scalars(
            select(GroupVote).join(PlanVariant).where(GroupVote.group_id == group_id, PlanVariant.status != "stale")
        )
    )
