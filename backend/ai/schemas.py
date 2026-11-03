from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Money = Annotated[Decimal, Field(ge=0, le=999999999, decimal_places=2, allow_inf_nan=False)]
Category = Literal[
    "museums", "nature", "nightlife", "food", "landmarks", "history", "shopping", "entertainment", "sports"
]


class RankingWeights(StrictModel):
    preference: float = Field(default=0.30, ge=0, le=5)
    group_fit: float = Field(default=0.20, ge=0, le=5)
    rating: float = Field(default=0.10, ge=0, le=5)
    price_fit: float = Field(default=0.10, ge=0, le=5)
    semantic: float = Field(default=0.10, ge=0, le=5)
    novelty: float = Field(default=0.05, ge=0, le=5)
    votes: float = Field(default=0.10, ge=0, le=5)
    time_fit: float = Field(default=0.05, ge=0, le=5)
    distance: float = Field(default=0.10, ge=0, le=5)


class PlanningConstraints(StrictModel):
    day_start_minute: int = Field(default=540, ge=0, le=1439)
    return_by_minute: int = Field(default=1320, ge=1, le=1620)
    max_daily_travel_minutes: int = Field(default=180, ge=0, le=720)
    max_leg_km: float = Field(default=25, gt=0, le=75, allow_inf_nan=False)
    require_known_hours: bool = True
    wheelchair_required: bool = False
    youngest_age: int | None = Field(default=None, ge=0, le=120)
    must_place_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=30)
    veto_place_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=100)
    veto_categories: list[Category] = Field(default_factory=list)
    must_categories: list[Category] = Field(default_factory=list)
    must_subcategories: list[Annotated[str, Field(min_length=3, max_length=120)]] = Field(
        default_factory=list, max_length=30
    )
    veto_subcategories: list[Annotated[str, Field(min_length=3, max_length=120)]] = Field(
        default_factory=list, max_length=100
    )
    dietary_requirements: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=20
    )
    # Explicit group allowances cover non-scheduled costs, never advertised as quotes.
    fixed_costs: dict[Literal["transportation", "hotel", "food", "local_transport", "miscellaneous"], Money] = Field(
        default_factory=dict
    )
    daily_activity_budget: dict[Annotated[int, Field(ge=1, le=30)], Money] = Field(default_factory=dict)
    daily_travel_minutes: dict[Annotated[int, Field(ge=1, le=30)], Annotated[int, Field(ge=0, le=720)]] = Field(
        default_factory=dict
    )
    daily_veto_categories: dict[Annotated[int, Field(ge=1, le=30)], list[Category]] = Field(default_factory=dict)
    earliest_start_by_place: dict[Annotated[int, Field(gt=0)], Annotated[int, Field(ge=0, le=1560)]] = Field(
        default_factory=dict
    )
    anchor_latitude: float | None = Field(default=None, ge=-90, le=90)
    anchor_longitude: float | None = Field(default=None, ge=-180, le=180)
    ranking_weights: RankingWeights = Field(default_factory=RankingWeights)

    @model_validator(mode="after")
    def coherent(self):
        if self.return_by_minute <= self.day_start_minute:
            raise ValueError("Return time must follow the start time; use minutes above 1440 for the next morning")
        if set(self.must_place_ids) & set(self.veto_place_ids) or set(self.must_categories) & set(self.veto_categories):
            raise ValueError("A must-have cannot also be vetoed")
        if set(self.must_subcategories) & set(self.veto_subcategories):
            raise ValueError("A required subcategory cannot also be vetoed")
        if (self.anchor_latitude is None) != (self.anchor_longitude is None):
            raise ValueError("Provide both anchor coordinates")
        return self


class PreferenceInput(StrictModel):
    interests: dict[Annotated[str, Field(min_length=1, max_length=50)], Annotated[int, Field(ge=0, le=10)]] = Field(
        default_factory=dict
    )
    description: str = Field(default="", max_length=1000)
    dietary_requirements: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=20
    )
    mobility_requirements: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=20
    )
    must_place_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=30)
    veto_place_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=100)
    must_categories: list[Category] = Field(default_factory=list)
    veto_categories: list[Category] = Field(default_factory=list)
    budget_preference: Money | None = None

    @model_validator(mode="after")
    def no_conflict(self):
        if set(self.must_place_ids) & set(self.veto_place_ids) or set(self.must_categories) & set(self.veto_categories):
            raise ValueError("Must-have and veto constraints conflict")
        return self


class BallotInput(StrictModel):
    plan_id: int | None = Field(default=None, gt=0)
    place_id: int | None = Field(default=None, gt=0)
    value: Literal[-1, 0, 1] = 1
    rank: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def one_target(self):
        if (self.plan_id is None) == (self.place_id is None):
            raise ValueError("Supply exactly one plan_id or place_id")
        return self


class GenerateInput(StrictModel):
    use_llm: bool = True


class EditCommand(StrictModel):
    action: Literal["remove", "add", "cheaper", "reduce_walking", "move_later"]
    day_number: int | None = Field(default=None, ge=1, le=30)
    place_id: int | None = Field(default=None, gt=0)
    category: Category | None = None
    earliest_minute: int | None = Field(default=None, ge=0, le=1560)


class ReplanInput(StrictModel):
    expected_version: int = Field(ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    command: EditCommand | None = None

    @model_validator(mode="after")
    def input_mode(self):
        if (self.text is None) == (self.command is None):
            raise ValueError("Supply one of text or command")
        return self


class RollbackInput(StrictModel):
    expected_version: int = Field(ge=1)
    target_version: int = Field(ge=1)


class Candidate(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    place_id: int
    external_id: str
    name: str
    category: str
    subcategory: str
    description: str
    latitude: float
    longitude: float
    neighborhood: str
    duration_minutes: int
    cost_cents: int
    currency: str
    price_source: str
    price_type: str
    rating: float | None
    provider: str
    opening_windows: dict[date, list[tuple[int, int]]]
    hours_known: bool
    source: str | None


class RankedCandidate(StrictModel):
    candidate: Candidate
    score: float
    components: dict[str, float]
    traveler_scores: list[float]
    reason_codes: list[str]


class Stop(StrictModel):
    place_id: int
    start_minute: int
    end_minute: int
    travel_minutes: int
    cost_cents: int
    reason: str


class ScheduleDay(StrictModel):
    day_number: int
    date: date
    stops: list[Stop]
    return_travel_minutes: int = 0


class ScheduleResult(StrictModel):
    days: list[ScheduleDay]
    status: str
    objective: float
    best_bound: float
    solver_seconds: float
    travel_minutes: int
    distance_km: float
    activity_cost_cents: int
    route_source: str = "Coordinate-based travel estimate"


class AdviceItem(StrictModel):
    place_id: int
    relevance: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: Literal["preference_match", "group_compromise", "geographic_fit", "variety", "budget_fit"]


class RankingAdvice(StrictModel):
    recommendations: list[AdviceItem] = Field(max_length=40)
