from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.ai.group import policy_for, save_preference
from backend.ai.pipeline import generate_advanced
from backend.ai.replanning import replan, rollback
from backend.ai.schemas import (
    BallotInput,
    GenerateInput,
    PlanningConstraints,
    PreferenceInput,
    ReplanInput,
    RollbackInput,
)
from backend.api.v1 import ItineraryRead
from backend.core.auth import current_user, require_plan, require_trip
from backend.core.errors import AppError
from backend.db import get_db
from backend.domain.itineraries import read_itinerary
from backend.models import (
    ItineraryVersion,
    LLMCall,
    Place,
    PlanningBallot,
    PlanningPolicy,
    PlanningRun,
    PlanVariant,
    Traveler,
    TravelerPlaceConstraint,
    Trip,
    User,
)

router = APIRouter(tags=["advanced planning"], dependencies=[Depends(current_user)])


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    trip_id: int | None
    operation: str
    status: str
    latency_ms: float
    metrics: dict
    stages: list[dict]
    error_code: str | None
    created_at: datetime


class GenerateRead(BaseModel):
    plans: list[ItineraryRead]
    run_id: str
    metrics: dict


class ReplanRead(BaseModel):
    itinerary: ItineraryRead
    run_id: str


class BallotRead(BallotInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: str
    trip_id: int


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    trip_version: int
    reason: str
    created_at: datetime


class LLMCallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider: str
    model: str
    purpose: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: Decimal | None
    latency_ms: float


class RunDetail(RunRead):
    calls: list[LLMCallRead]


def validate_place_scope(db, trip, place_ids):
    if not place_ids:
        return
    found = set(db.scalars(select(Place.id).where(Place.id.in_(place_ids), Place.city.ilike(trip.destination_city))))
    if set(place_ids) != found:
        raise AppError(422, "PLACE_OUTSIDE_DESTINATION", "Use existing provider places from this trip's destination.")


@router.get("/trips/{trip_id}/ai/constraints", response_model=PlanningConstraints)
def get_constraints(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return policy_for(db, require_trip(db, trip_id, user))


@router.put("/trips/{trip_id}/ai/constraints", response_model=PlanningConstraints)
def put_constraints(
    trip_id: int, payload: PlanningConstraints, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    require_trip(db, trip_id, user, edit=True)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id).with_for_update().execution_options(populate_existing=True))
    validate_place_scope(db, trip, [*payload.must_place_ids, *payload.veto_place_ids, *payload.earliest_start_by_place])
    days = (trip.end_date - trip.start_date).days + 1
    if any(
        day > days
        for day in {*payload.daily_activity_budget, *payload.daily_travel_minutes, *payload.daily_veto_categories}
    ):
        raise AppError(422, "INVALID_CONSTRAINT_DAY", "A daily constraint is outside the trip date range.")
    policy = db.get(PlanningPolicy, trip_id)
    if policy is None:
        policy = PlanningPolicy(trip_id=trip_id)
        db.add(policy)
    policy.constraints = payload.model_dump(mode="json")
    trip.trip_version += 1
    db.execute(update(PlanVariant).where(PlanVariant.trip_id == trip_id).values(status="stale"))
    db.commit()
    return payload


@router.get("/trips/{trip_id}/ai/preferences/me", response_model=PreferenceInput | None)
def get_preferences(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user)
    traveler = db.scalar(select(Traveler).where(Traveler.trip_id == trip_id, Traveler.user_id == user.id))
    if not traveler:
        return None
    places = list(db.scalars(select(TravelerPlaceConstraint).where(TravelerPlaceConstraint.traveler_id == traveler.id)))
    return PreferenceInput(
        interests={p.category: p.score for p in traveler.preferences},
        description=traveler.preference_text,
        dietary_requirements=traveler.dietary_restrictions,
        mobility_requirements=traveler.accessibility_requirements,
        must_place_ids=[p.place_id for p in places if p.kind == "must"],
        veto_place_ids=[p.place_id for p in places if p.kind == "veto"],
        must_categories=[p.category for p in traveler.preferences if p.is_must_do],
        veto_categories=[p.category for p in traveler.preferences if p.is_avoid],
        budget_preference=traveler.budget_preference,
    )


@router.put("/trips/{trip_id}/ai/preferences/me", response_model=PreferenceInput)
def put_preferences(
    trip_id: int, payload: PreferenceInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    trip = require_trip(db, trip_id, user)
    validate_place_scope(db, trip, [*payload.must_place_ids, *payload.veto_place_ids])
    save_preference(db, trip, user, payload)
    return payload


@router.put("/trips/{trip_id}/ai/votes", response_model=BallotRead)
def put_vote(trip_id: int, payload: BallotInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    trip = require_trip(db, trip_id, user)
    db.execute(select(Trip).where(Trip.id == trip_id).with_for_update())
    if payload.place_id:
        validate_place_scope(db, trip, [payload.place_id])
    else:
        plan = require_plan(db, payload.plan_id, user)
        if plan.trip_id != trip_id:
            raise AppError(422, "VOTE_TRIP_MISMATCH", "This itinerary belongs to another trip.")
        if plan.status == "stale":
            raise AppError(409, "STALE_ITINERARY", "Vote on a current itinerary.")
    target = (
        PlanningBallot.place_id == payload.place_id if payload.place_id else PlanningBallot.plan_id == payload.plan_id
    )
    row = db.scalar(
        select(PlanningBallot).where(PlanningBallot.trip_id == trip_id, PlanningBallot.user_id == user.id, target)
    )
    if row is None:
        row = PlanningBallot(trip_id=trip_id, user_id=user.id, **payload.model_dump())
        db.add(row)
    else:
        row.value, row.rank = payload.value, payload.rank
    db.commit()
    return row


@router.get("/trips/{trip_id}/ai/votes", response_model=list[BallotRead])
def get_votes(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user)
    return list(db.scalars(select(PlanningBallot).where(PlanningBallot.trip_id == trip_id).order_by(PlanningBallot.id)))


@router.delete("/trips/{trip_id}/ai/votes/{ballot_id}", status_code=204)
def remove_vote(trip_id: int, ballot_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user)
    db.execute(
        delete(PlanningBallot).where(
            PlanningBallot.id == ballot_id, PlanningBallot.trip_id == trip_id, PlanningBallot.user_id == user.id
        )
    )
    db.commit()


@router.post("/trips/{trip_id}/ai/generate", response_model=GenerateRead)
def generate(trip_id: int, payload: GenerateInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user, edit=True)
    plans, run = generate_advanced(db, trip_id, user.id, payload.use_llm)
    return {"plans": [read_itinerary(db, plan) for plan in plans], "run_id": run.id, "metrics": run.metrics}


@router.post("/itineraries/{plan_id}/replan", response_model=ReplanRead)
def edit(plan_id: int, payload: ReplanInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_plan(db, plan_id, user, edit=True)
    plan, run = replan(db, plan_id, user.id, payload)
    return {"itinerary": read_itinerary(db, plan), "run_id": run.id}


@router.get("/itineraries/{plan_id}/versions", response_model=list[VersionRead])
def versions(plan_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_plan(db, plan_id, user)
    return list(
        db.scalars(
            select(ItineraryVersion).where(ItineraryVersion.plan_id == plan_id).order_by(ItineraryVersion.version)
        )
    )


@router.post("/itineraries/{plan_id}/rollback", response_model=ItineraryRead)
def restore(plan_id: int, payload: RollbackInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_plan(db, plan_id, user, edit=True)
    return read_itinerary(db, rollback(db, plan_id, user.id, payload))


@router.get("/trips/{trip_id}/ai/runs", response_model=list[RunRead])
def runs(
    trip_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    require_trip(db, trip_id, user, owner=True)
    return list(
        db.scalars(
            select(PlanningRun)
            .where(PlanningRun.trip_id == trip_id)
            .order_by(PlanningRun.created_at.desc())
            .limit(limit)
        )
    )


@router.get("/trips/{trip_id}/ai/runs/{run_id}", response_model=RunDetail)
def run_detail(trip_id: int, run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_trip(db, trip_id, user, owner=True)
    row = db.get(PlanningRun, run_id)
    if not row or row.trip_id != trip_id:
        raise AppError(404, "RUN_NOT_FOUND", "Planning run not found.")
    return {
        **RunRead.model_validate(row).model_dump(),
        "calls": list(db.scalars(select(LLMCall).where(LLMCall.run_id == row.id))),
    }
