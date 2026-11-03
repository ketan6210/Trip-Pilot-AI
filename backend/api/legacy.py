import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.budget import minimum_budget_estimate
from backend.comparison import compare_plans
from backend.context import get_or_create_place_context, serialize_place_context
from backend.core.auth import current_user, protect_legacy_resources, require_plan, require_trip
from backend.db import get_db
from backend.detail.service import generate_plan_detail, get_plan_detail
from backend.domain import trips as trip_service
from backend.expenses import expense_breakdown, expense_dashboard, serialize_expense
from backend.flights import FlightSearchRequest
from backend.geography.cities import search_cities
from backend.geography.neighborhoods import build_location_hierarchy, rank_neighborhoods
from backend.meals.service import meal_alternatives, replace_meal, restaurant_candidates, serialize_meal
from backend.models import (
    ExpenseLineItem,
    Meal,
    Place,
    PlanCostEntry,
    PlanVariant,
    Traveler,
    TravelerPreference,
    Trip,
    TripBudget,
    User,
    Vote,
)
from backend.orchestration.parser import local_llm_status
from backend.orchestration.schemas import OrchestratorMessageInput, OrchestratorResponse, SessionRead, StoredMessageRead
from backend.orchestration.service import OrchestrationService
from backend.planning import generate_plan_variants, serialize_plan
from backend.pricing.optimizer import bring_plan_under_budget
from backend.providers import get_place_provider
from backend.providers.base import PlaceSearch
from backend.providers.flights import search_flights
from backend.schemas import (
    BudgetMinimumInput,
    BudgetUpdate,
    CitySuggestion,
    FlightSearchInput,
    MealReplaceInput,
    NeighborhoodRead,
    PlaceRead,
    PlanCompareInput,
    PlanRead,
    PreferencesUpdate,
    TravelerCreate,
    TravelerRead,
    TripCreate,
    TripRead,
    TripUpdate,
    VoteCreate,
    VoteRead,
)
from backend.services import generate_itinerary

router = APIRouter(dependencies=[Depends(protect_legacy_resources)])


@router.post("/trips", response_model=TripRead, status_code=status.HTTP_201_CREATED)
def create_trip(payload: TripCreate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Trip:
    return trip_service.create_trip(db, payload, user.id)


@router.get("/trips/{trip_id}", response_model=TripRead)
def get_trip(trip_id: int, db: Session = Depends(get_db)) -> Trip:
    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip


@router.put("/trips/{trip_id}", response_model=TripRead)
def update_trip(trip_id: int, payload: TripUpdate, db: Session = Depends(get_db)) -> Trip:
    return trip_service.update_trip(db, trip_id, payload)


@router.post("/trips/{trip_id}/travelers", response_model=TravelerRead, status_code=201)
def add_traveler(trip_id: int, payload: TravelerCreate, db: Session = Depends(get_db)) -> Traveler:
    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    current = db.scalar(select(func.count()).select_from(Traveler).where(Traveler.trip_id == trip_id)) or 0
    if current >= trip.traveler_count:
        raise HTTPException(409, "Traveler count limit reached")
    traveler = Traveler(trip_id=trip_id, **payload.model_dump())
    db.add(traveler)
    db.commit()
    return db.scalar(select(Traveler).where(Traveler.id == traveler.id).options(selectinload(Traveler.preferences)))


@router.get("/trips/{trip_id}/travelers", response_model=list[TravelerRead])
def list_travelers(trip_id: int, db: Session = Depends(get_db)) -> list[Traveler]:
    return list(
        db.scalars(select(Traveler).where(Traveler.trip_id == trip_id).options(selectinload(Traveler.preferences)))
    )


@router.put("/travelers/{traveler_id}/preferences", response_model=TravelerRead)
def save_preferences(traveler_id: int, payload: PreferencesUpdate, db: Session = Depends(get_db)) -> Traveler:
    from backend.ai.group import invalidate_advanced

    traveler = db.get(Traveler, traveler_id)
    if not traveler:
        raise HTTPException(404, "Traveler not found")
    for existing in list(traveler.preferences):
        db.delete(existing)
    db.flush()
    for pref in payload.preferences:
        db.add(TravelerPreference(traveler_id=traveler_id, **pref.model_dump()))
    invalidate_advanced(db, traveler.trip_id)
    db.commit()
    return db.scalar(select(Traveler).where(Traveler.id == traveler_id).options(selectinload(Traveler.preferences)))


@router.put("/trips/{trip_id}/budget")
def save_budget(trip_id: int, payload: BudgetUpdate, db: Session = Depends(get_db)) -> dict:
    from backend.ai.group import invalidate_advanced

    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    if payload.mode == "fixed" and sum(payload.categories.values()) > float(trip.total_budget):
        raise HTTPException(422, "Category budgets exceed total trip budget")
    budget = db.scalar(select(TripBudget).where(TripBudget.trip_id == trip_id))
    if budget:
        budget.mode, budget.categories = payload.mode, payload.categories
    else:
        budget = TripBudget(trip_id=trip_id, **payload.model_dump())
        db.add(budget)
    invalidate_advanced(db, trip_id)
    db.commit()
    return {"trip_id": trip_id, **payload.model_dump()}


@router.post("/trips/{trip_id}/generate-itinerary")
def itinerary(trip_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return generate_itinerary(db, trip_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/trips/{trip_id}/generate-plans", response_model=list[PlanRead])
def generate_plans(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict]:
    try:
        return [serialize_plan(plan) for plan in generate_plan_variants(db, trip_id, user.id)]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/trips/{trip_id}/regenerate-plans", response_model=list[PlanRead])
def regenerate_plans(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict]:
    return generate_plans(trip_id, db, user)


@router.get("/trips/{trip_id}/plans", response_model=list[PlanRead])
def list_plans(trip_id: int, db: Session = Depends(get_db)) -> list[dict]:
    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    plans = db.scalars(
        select(PlanVariant)
        .where(
            PlanVariant.trip_id == trip_id,
            PlanVariant.plan_set_version == trip.trip_version,
            PlanVariant.status != "stale",
        )
        .options(selectinload(PlanVariant.votes))
        .order_by(PlanVariant.id)
    ).all()
    return [serialize_plan(plan) for plan in plans]


@router.get("/plans/{plan_id}", response_model=PlanRead)
def get_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.scalar(select(PlanVariant).where(PlanVariant.id == plan_id).options(selectinload(PlanVariant.votes)))
    if not plan:
        raise HTTPException(404, "Plan not found")
    return serialize_plan(plan)


@router.post("/plans/{plan_id}/vote", response_model=VoteRead)
def vote_for_plan(plan_id: int, payload: VoteCreate, db: Session = Depends(get_db)) -> Vote:
    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    traveler = db.get(Traveler, payload.traveler_id)
    if not traveler or traveler.trip_id != plan.trip_id:
        raise HTTPException(422, "Traveler does not belong to this trip")
    existing = db.scalar(
        select(Vote).where(
            Vote.trip_id == plan.trip_id, Vote.traveler_id == traveler.id, Vote.vote_type == payload.vote_type
        )
    )
    if existing:
        existing.plan_variant_id = plan_id
        vote = existing
    else:
        vote = Vote(trip_id=plan.trip_id, plan_variant_id=plan_id, **payload.model_dump())
        db.add(vote)
    db.commit()
    db.refresh(vote)
    return vote


@router.get("/trips/{trip_id}/votes", response_model=list[VoteRead])
def list_votes(trip_id: int, db: Session = Depends(get_db)) -> list[Vote]:
    return list(db.scalars(select(Vote).where(Vote.trip_id == trip_id).order_by(Vote.created_at)))


@router.post("/plans/{plan_id}/select", response_model=PlanRead)
def select_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    from backend.domain.itineraries import select_itinerary

    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return serialize_plan(select_itinerary(db, plan))


@router.get("/places/search", response_model=list[PlaceRead])
def search_places(
    city: str,
    category: str | None = None,
    subcategory: str | None = None,
    neighborhood: str | None = None,
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    radius_km: float | None = Query(default=None, gt=0, le=200),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 300:
        raise HTTPException(422, "limit must be between 1 and 300")
    coordinates = (latitude, longitude, radius_km)
    if any(value is not None for value in coordinates) and not all(value is not None for value in coordinates):
        raise HTTPException(422, "latitude, longitude, and radius_km must be provided together")
    provider = get_place_provider(db)
    return provider.search(
        PlaceSearch(
            city=city,
            category=category,
            subcategory=subcategory,
            neighborhood=neighborhood,
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            limit=limit,
        )
    )


@router.get("/places/{place_id}", response_model=PlaceRead)
def get_place(place_id: int, db: Session = Depends(get_db)):
    place = get_place_provider(db).details(place_id)
    if not place:
        raise HTTPException(404, "Place not found")
    return place


@router.get("/trips/{trip_id}/neighborhoods", response_model=list[NeighborhoodRead])
def trip_neighborhoods(
    trip_id: int,
    category: str | None = None,
    subcategory: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
) -> list[dict]:
    trip = db.scalar(
        select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.travelers).selectinload(Traveler.preferences))
    )
    if not trip:
        raise HTTPException(404, "Trip not found")
    places = get_place_provider(db).search(
        PlaceSearch(city=trip.destination_city, category=category, subcategory=subcategory, limit=300)
    )
    return rank_neighborhoods(places, trip.travelers, limit=max(1, min(limit, 50)))


@router.get("/locations/hierarchy")
def location_hierarchy(city: str, db: Session = Depends(get_db)) -> dict:
    places = get_place_provider(db).search(PlaceSearch(city=city, limit=300))
    if not places:
        raise HTTPException(404, f"No places available for {city}")
    return build_location_hierarchy(places)


@router.get("/locations/search", response_model=list[CitySuggestion])
def city_search(q: str = Query(min_length=2, max_length=100), limit: int = Query(default=6, ge=1, le=10)) -> list[dict]:
    try:
        return search_cities(q, limit)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503, "Live city search is temporarily unavailable; you can still type the city manually."
        ) from exc


@router.post("/flights/search")
def flight_search(payload: FlightSearchInput) -> dict:
    request = FlightSearchRequest(
        origin=payload.origin,
        destination=payload.destination,
        departure_date=payload.departure_date,
        return_date=payload.return_date,
        adults=payload.adults,
        cabin=payload.cabin,
        currency=payload.currency,
    )
    return search_flights(request, refresh=payload.refresh)


@router.post("/budget/minimum-estimate")
def budget_minimum(payload: BudgetMinimumInput) -> dict:
    try:
        return minimum_budget_estimate(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/plans/compare")
def plans_compare(payload: PlanCompareInput, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    require_plan(db, payload.plan_a_id, user)
    require_plan(db, payload.plan_b_id, user)
    try:
        return compare_plans(db, payload.plan_a_id, payload.plan_b_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/plans/{plan_id}/costs")
def plan_costs(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    rows = list(db.scalars(select(PlanCostEntry).where(PlanCostEntry.plan_id == plan_id).order_by(PlanCostEntry.id)))
    return {
        "plan_id": plan.id,
        "currency": plan.summary.get("currency"),
        "total": float(plan.estimated_total_cost),
        "range": plan.summary.get("cost_range"),
        "budget_status": plan.summary.get("budget_status"),
        "budget_delta": plan.summary.get("budget_delta"),
        "breakdown": plan.summary.get("cost_breakdown"),
        "entries": [
            {
                "cost_id": row.cost_id,
                "category": row.category,
                "item_id": row.item_id,
                "minimum": None if row.minimum is None else float(row.minimum),
                "expected": None if row.expected is None else float(row.expected),
                "maximum": None if row.maximum is None else float(row.maximum),
                "currency": row.currency,
                "shared_or_personal": row.shared_or_personal,
                "price_type": row.price_type,
                "confidence": row.confidence,
                "provider": row.provider,
                "source": row.source,
                "fetched_at": row.fetched_at,
                "metadata": row.metadata_json,
            }
            for row in rows
        ],
    }


@router.post("/plans/{plan_id}/bring-under-budget")
def plan_bring_under_budget(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return bring_plan_under_budget(db, plan_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/orchestrator/sessions", response_model=SessionRead, status_code=201)
def create_orchestration_session(
    trip_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> SessionRead:
    if trip_id is not None:
        require_trip(db, trip_id, user, edit=True)
    return OrchestrationService(db).create_session(trip_id, owner_id=user.id)


@router.get("/orchestrator/status")
def orchestrator_status() -> dict[str, str | bool]:
    return local_llm_status()


@router.get("/orchestrator/sessions/{session_id}", response_model=SessionRead)
def get_orchestration_session(session_id: int, db: Session = Depends(get_db)) -> SessionRead:
    session = OrchestrationService(db).get_session(session_id)
    if not session:
        raise HTTPException(404, "Orchestration session not found")
    return session


@router.post("/orchestrator/sessions/{session_id}/messages", response_model=OrchestratorResponse)
def orchestrator_message(
    session_id: int, payload: OrchestratorMessageInput, db: Session = Depends(get_db)
) -> OrchestratorResponse:
    try:
        return OrchestrationService(db).process_message(session_id, payload.message)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "The message contains an invalid date, budget or traveler count") from exc


@router.get("/orchestrator/sessions/{session_id}/messages", response_model=list[StoredMessageRead])
def orchestration_messages(session_id: int, db: Session = Depends(get_db)):
    service = OrchestrationService(db)
    if not service.get_session(session_id):
        raise HTTPException(404, "Orchestration session not found")
    return service.messages(session_id)


@router.post("/orchestrator/sessions/{session_id}/generate-plans")
def generate_orchestrated_plans(session_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return OrchestrationService(db).generate_plans(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/plans/{plan_id}/generate-detail")
def generate_detail(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return generate_plan_detail(db, plan_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/plans/{plan_id}/detail")
def plan_detail(plan_id: int, db: Session = Depends(get_db)) -> dict:
    detail = get_plan_detail(db, plan_id)
    if not detail:
        raise HTTPException(404, "Detailed plan has not been generated")
    return detail


@router.get("/plans/{plan_id}/budget")
def plan_budget(plan_id: int, db: Session = Depends(get_db)) -> dict:
    detail = get_plan_detail(db, plan_id)
    if not detail:
        raise HTTPException(404, "Detailed plan has not been generated")
    return detail["budget"]


@router.get("/places/{place_id}/context")
def place_context(place_id: int, db: Session = Depends(get_db)) -> dict:
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(404, "Place not found")
    return serialize_place_context(get_or_create_place_context(db, place))


@router.get("/trips/{trip_id}/restaurants/search")
def restaurants_search(
    trip_id: int,
    day_number: int | None = None,
    neighborhood: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    limit: int = Query(default=10, ge=1, le=25),
    db: Session = Depends(get_db),
) -> list[dict]:
    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    center = (latitude, longitude) if latitude is not None and longitude is not None else None
    places = restaurant_candidates(db, trip, center, max(limit * 3, 25))
    if neighborhood:
        local = [place for place in places if place.neighborhood.casefold() == neighborhood.casefold()]
        places = local or places
    provider = get_place_provider(db)
    places = [provider.enrich(place) for place in places[:limit]]
    return [
        {
            "id": place.id,
            "name": place.name,
            "cuisine": place.tags,
            "neighborhood": place.neighborhood,
            "address": place.address,
            "photo_url": place.image_url or "/placeholders/food.svg",
            "cost_status": place.cost_status,
            "cost_currency": place.cost_currency,
            "maps_url": place.maps_url,
            "official_url": place.official_url,
            "menu_url": (place.source_metadata or {}).get("osm_tags", {}).get("menu"),
            "reservation_url": place.reservation_url,
            "day_number": day_number,
        }
        for place in places
    ]


@router.get("/restaurants/{restaurant_id}", response_model=PlaceRead)
def restaurant_detail(restaurant_id: int, db: Session = Depends(get_db)) -> Place:
    place = db.get(Place, restaurant_id)
    if not place or place.category != "food":
        raise HTTPException(404, "Restaurant not found")
    return place


@router.get("/restaurants/{restaurant_id}/context")
def restaurant_context(restaurant_id: int, db: Session = Depends(get_db)) -> dict:
    place = db.get(Place, restaurant_id)
    if not place or place.category != "food":
        raise HTTPException(404, "Restaurant not found")
    return serialize_place_context(get_or_create_place_context(db, place))


@router.get("/plans/{plan_id}/days/{day_number}/meals")
def plan_day_meals(plan_id: int, day_number: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = list(
        db.scalars(select(Meal).where(Meal.plan_id == plan_id, Meal.day_number == day_number).order_by(Meal.start_time))
    )
    if not rows:
        raise HTTPException(404, "Meals have not been generated for this day")
    return [
        serialize_meal(row, serialize_place_context(get_or_create_place_context(db, row.restaurant))) for row in rows
    ]


@router.post("/plans/{plan_id}/days/{day_number}/meals/{meal_type}/replace")
def plan_meal_replace(
    plan_id: int, day_number: int, meal_type: str, payload: MealReplaceInput, db: Session = Depends(get_db)
) -> dict:
    if meal_type not in {"breakfast", "lunch", "dinner"}:
        raise HTTPException(422, "meal_type must be breakfast, lunch, or dinner")
    try:
        if payload.restaurant_id is None:
            return {"alternatives": meal_alternatives(db, plan_id, day_number, meal_type)}
        return replace_meal(db, plan_id, day_number, meal_type, payload.restaurant_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/plans/{plan_id}/expenses")
def plan_expenses(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return expense_dashboard(db, plan_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/plans/{plan_id}/expenses/{category}")
def plan_category_expenses(plan_id: int, category: str, db: Session = Depends(get_db)) -> dict:
    try:
        return expense_breakdown(db, plan_id, category=category)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/plans/{plan_id}/expenses/{category}/days/{day_number}")
def plan_category_day_expenses(plan_id: int, category: str, day_number: int, db: Session = Depends(get_db)) -> dict:
    try:
        return expense_breakdown(db, plan_id, category=category, day_number=day_number)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/plans/{plan_id}/days/{day_number}/expenses")
def plan_day_expenses(plan_id: int, day_number: int, db: Session = Depends(get_db)) -> dict:
    try:
        return expense_breakdown(db, plan_id, day_number=day_number)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/plans/{plan_id}/meals/{meal_id}/expense")
def meal_expense(plan_id: int, meal_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(ExpenseLineItem).where(
            ExpenseLineItem.plan_id == plan_id,
            ExpenseLineItem.itinerary_item_id == f"meal-{meal_id}",
        )
    )
    if not row:
        raise HTTPException(404, "Meal expense was not found")
    return serialize_expense(row)
