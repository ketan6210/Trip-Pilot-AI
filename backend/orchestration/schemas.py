from datetime import date, time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.parsing.preference_parser import Preference


class Intent(StrEnum):
    CREATE_TRIP = "CREATE_TRIP"
    ADD_PREFERENCE = "ADD_PREFERENCE"
    REMOVE_PREFERENCE = "REMOVE_PREFERENCE"
    CHANGE_PREFERENCE = "CHANGE_PREFERENCE"
    SET_BUDGET = "SET_BUDGET"
    CHANGE_BUDGET = "CHANGE_BUDGET"
    CHANGE_CATEGORY_BUDGET = "CHANGE_CATEGORY_BUDGET"
    SET_TRIP_DATES = "SET_TRIP_DATES"
    CHANGE_TRIP_DATES = "CHANGE_TRIP_DATES"
    SET_TRAVELERS = "SET_TRAVELERS"
    GENERATE_PLANS = "GENERATE_PLANS"
    COMPARE_PLANS = "COMPARE_PLANS"
    SELECT_PLAN = "SELECT_PLAN"
    VOTE_PLAN = "VOTE_PLAN"
    LOCK_ACTIVITY = "LOCK_ACTIVITY"
    UNLOCK_ACTIVITY = "UNLOCK_ACTIVITY"
    REPLACE_ACTIVITY = "REPLACE_ACTIVITY"
    REMOVE_ACTIVITY = "REMOVE_ACTIVITY"
    MOVE_ACTIVITY = "MOVE_ACTIVITY"
    ADD_ACTIVITY = "ADD_ACTIVITY"
    ADD_CATEGORY = "ADD_CATEGORY"
    REMOVE_CATEGORY = "REMOVE_CATEGORY"
    CHANGE_HOTEL = "CHANGE_HOTEL"
    CHANGE_FLIGHT = "CHANGE_FLIGHT"
    MAKE_DAY_RELAXED = "MAKE_DAY_RELAXED"
    MAKE_DAY_PACKED = "MAKE_DAY_PACKED"
    REPLAN_DAY = "REPLAN_DAY"
    REPLAN_TRIP = "REPLAN_TRIP"
    EXPLAIN_PLAN = "EXPLAIN_PLAN"
    EXPLAIN_ACTIVITY = "EXPLAIN_ACTIVITY"
    EXPLAIN_BUDGET = "EXPLAIN_BUDGET"
    UNKNOWN = "UNKNOWN"


class PreferenceSlot(BaseModel):
    category: str
    subcategory: str | None = None
    score: int = Field(default=8, ge=0, le=10)
    remove: bool = False


class TripSlots(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    destination: str | None = None
    origin: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    traveler_count: int | None = Field(default=None, ge=1, le=20)
    total_budget: float | None = Field(default=None, gt=0)
    budget_amount: float | None = Field(default=None, gt=0)
    budget_currency: str | None = None
    budget_source_text: str | None = None
    budget_confidence: float | None = Field(default=None, ge=0, le=1)
    budget_delta: float | None = None
    currency: str | None = None
    flight_included: bool | None = None
    pace: str | None = None
    transport_mode: str | None = None
    walking_limit: int | None = Field(default=None, ge=0, le=1000)
    preferred_start_time: time | None = None
    preferred_end_time: time | None = None
    hotel_style: str | None = None
    preferences: list[PreferenceSlot] = Field(default_factory=list)
    flexible_preferences: list[Preference] = Field(default_factory=list)
    dietary_requirements: list[str] = Field(default_factory=list)
    accessibility_requirements: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class Operation(BaseModel):
    type: Intent
    target: str | None = None
    target_item_id: int | None = None
    category: str | None = None
    amount: float | None = None
    day_number: int | None = None
    constraints: dict[str, bool | float | str] = Field(default_factory=dict)


class OrchestratorCommand(BaseModel):
    intent: Intent = Intent.UNKNOWN
    operations: list[Operation] = Field(default_factory=list)
    extracted_slots: TripSlots = Field(default_factory=TripSlots)
    requires_followup: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    followup_question: str | None = None


class TripState(BaseModel):
    trip_id: int | None = None
    destination: str | None = None
    origin: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    date_range_confirmed: bool = False
    days: int | None = None
    number_of_days: int | None = None
    traveler_count: int | None = None
    total_budget: float | None = None
    currency: str | None = None
    budget_amount: float | None = None
    budget_currency: str | None = None
    budget_source_text: str | None = None
    budget_confidence: float = 0
    flight_included: bool = True
    preferences: dict[str, int] = Field(default_factory=dict)
    broad_preferences: dict[str, float] = Field(default_factory=dict)
    subcategory_preferences: dict[str, int] = Field(default_factory=dict)
    custom_preferences: list[Preference] = Field(default_factory=list)
    dietary_requirements: list[str] = Field(default_factory=list)
    accessibility_requirements: list[str] = Field(default_factory=list)
    pace: str = "balanced"
    transport_mode: str = "mixed"
    walking_limit: int = 120
    preferred_start_time: time | None = None
    preferred_end_time: time | None = None
    hotel_style: str = "midrange"
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    locked_items: list[int] = Field(default_factory=list)
    category_budgets: dict[str, float] = Field(default_factory=dict)
    selected_plan_id: int | None = None
    itinerary_version: int = 0
    last_question_key: str | None = None
    question_attempt_count: int = 0
    ambiguity_log: list[str] = Field(default_factory=list)


class SessionRead(BaseModel):
    id: int
    status: str
    state: TripState


class OrchestratorMessageInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class OrchestratorResponse(BaseModel):
    session_id: int
    parser_mode: str
    assistant_message: str
    ready: bool
    command: OrchestratorCommand
    state: TripState


class StoredMessageRead(BaseModel):
    id: int
    role: str
    content: str
    structured_command: dict | None
    model_config = ConfigDict(from_attributes=True)
