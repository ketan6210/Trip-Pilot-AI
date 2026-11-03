from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SUPPORTED_CURRENCIES = set(
    "USD INR EUR GBP JPY CAD AUD CHF NZD CNY HKD SGD SEK NOK DKK PLN CZK HUF RON BGN BRL MXN ZAR TRY KRW IDR MYR PHP THB ILS AED SAR".split()
)


def validate_currency(value: str) -> str:
    value = value.strip().upper()
    if value not in SUPPORTED_CURRENCIES:
        raise ValueError("Use a supported ISO currency code, such as INR, USD, GBP or EUR")
    return value


Currency = Annotated[str, AfterValidator(validate_currency)]
Pace = Literal["balanced", "relaxed", "packed"]
TransportMode = Literal["mixed", "walking", "public transit", "taxi", "cab", "rental car"]
HotelStyle = Literal["budget", "midrange", "luxury", "premium", "upscale", "value"]


class TripCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    destination_city: str = Field(min_length=2, max_length=100)
    origin: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    traveler_count: int = Field(ge=1, le=20)
    total_budget: float = Field(gt=0, le=999999999)
    currency: Currency
    pace: Pace = "balanced"
    transport_mode: TransportMode = "mixed"
    max_walking_per_day: int = Field(default=120, ge=0, le=600)
    hotel_style: HotelStyle = "midrange"
    flight_included: bool = True

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days >= 30:
            raise ValueError("Trips may be at most 30 days")
        return self


class TripRead(TripCreate):
    id: int
    public_id: str
    owner_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    trip_version: int = 1
    model_config = ConfigDict(from_attributes=True)


class TripUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    destination_city: str | None = Field(default=None, min_length=2, max_length=100)
    origin: str | None = Field(default=None, min_length=2, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    traveler_count: int | None = Field(default=None, ge=1, le=20)
    total_budget: float | None = Field(default=None, gt=0, le=999999999)
    currency: Currency | None = None
    pace: Pace | None = None
    transport_mode: TransportMode | None = None
    max_walking_per_day: int | None = Field(default=None, ge=0, le=600)
    hotel_style: HotelStyle | None = None
    flight_included: bool | None = None

    @model_validator(mode="after")
    def supplied_dates_are_ordered(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Trip fields cannot be cleared; omit fields that should remain unchanged")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class TravelerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    walking_tolerance: int = Field(default=120, ge=0, le=600)
    preferred_start_time: time = time(9)
    preferred_end_time: time = time(22)
    dietary_restrictions: list[str] = Field(default_factory=list)
    accessibility_requirements: list[str] = Field(default_factory=list)


class PreferenceInput(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    subcategory: str = Field(default="general", min_length=1, max_length=50)
    score: int = Field(ge=0, le=10)
    is_must_do: bool = False
    is_avoid: bool = False


class PreferencesUpdate(BaseModel):
    preferences: list[PreferenceInput] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_preferences(self):
        keys = [(p.category, p.subcategory) for p in self.preferences]
        if len(set(keys)) != len(keys):
            raise ValueError("Preference categories and subcategories must be unique")
        return self


class BudgetUpdate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    mode: str = Field(pattern="^(fixed|flexible)$")
    categories: dict[str, float]

    @model_validator(mode="after")
    def nonnegative_values(self):
        if any(value < 0 for value in self.categories.values()):
            raise ValueError("category budgets must be nonnegative")
        return self


class TravelerRead(TravelerCreate):
    id: int
    trip_id: int
    preferences: list[PreferenceInput] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class PlanRead(BaseModel):
    id: int
    trip_id: int
    plan_type: str
    title: str
    estimated_total_cost: float
    estimated_per_person_cost: float
    mean_group_satisfaction: float
    minimum_traveler_satisfaction: float
    satisfaction_variance: float
    fairness_score: float
    travel_minutes: int
    walking_intensity: str
    activity_count: int
    summary: dict
    status: str
    plan_set_version: int = 1
    vote_count: int = 0
    favorite_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class VoteCreate(BaseModel):
    traveler_id: int
    vote_type: str = Field(default="vote", pattern="^(vote|favorite)$")


class VoteRead(BaseModel):
    id: int
    trip_id: int
    plan_variant_id: int
    traveler_id: int
    vote_type: str
    model_config = ConfigDict(from_attributes=True)


class PlaceRead(BaseModel):
    id: int
    external_provider: str
    external_place_id: str
    name: str
    country: str
    state: str
    city: str
    borough_or_district: str
    neighborhood: str
    address: str
    latitude: float
    longitude: float
    category: str
    subcategory: str
    tags: list[str]
    rating: float
    review_count: int
    rating_source: str | None
    google_place_id: str | None
    phone_number: str | None
    phone_source_url: str | None
    price_level: int
    cost_min: float
    cost_expected: float
    cost_max: float
    duration_minutes: int
    duration_min: int
    duration_expected: int
    duration_max: int
    best_time_of_day: str
    opening_hours: dict
    indoor_outdoor: str
    reservation_required: bool
    age_restriction: int | None
    accessibility: str
    popularity: dict[str, float | int]
    official_url: str | None
    maps_url: str
    ticket_url: str | None
    reservation_url: str | None
    image_url: str | None
    image_source: str | None
    image_attribution: str | None
    cost_status: str
    cost_source: str | None
    cost_currency: str
    model_config = ConfigDict(from_attributes=True)


class CitySuggestion(BaseModel):
    id: int
    name: str
    display_name: str
    country: str
    country_code: str
    region: str
    latitude: float
    longitude: float
    timezone: str | None = None
    population: int | None = None
    source: str


class NeighborhoodRead(BaseModel):
    neighborhood: str
    borough_or_district: str
    score: float
    features: dict[str, float]
    place_count: int
    center: dict[str, float]
    top_places: list[dict]


class FlightSearchInput(BaseModel):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    departure_date: date
    return_date: date
    adults: int = Field(ge=1, le=9)
    cabin: str = "economy"
    currency: Currency
    refresh: bool = False

    @model_validator(mode="after")
    def flight_dates_are_ordered(self):
        if self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        return self


class PlanCompareInput(BaseModel):
    plan_a_id: int
    plan_b_id: int


class MealReplaceInput(BaseModel):
    restaurant_id: int | None = None


class BudgetMinimumInput(BaseModel):
    destination: str = Field(min_length=2, max_length=100)
    origin: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    traveler_count: int = Field(default=2, ge=1, le=20)
    currency: Currency
    hotel_style: str = "budget"
    flight_included: bool = True

    @model_validator(mode="after")
    def optional_dates_are_ordered(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self
