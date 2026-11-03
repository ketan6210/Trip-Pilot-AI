"""Deterministic natural-language boundary; downstream code receives typed data."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.core.errors import AppError
from backend.orchestration.parser import OrchestratorParser
from backend.orchestration.schemas import TripState
from backend.schemas import Currency, HotelStyle, Pace, TripCreate


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origin: str | None = Field(default=None, min_length=2, max_length=100)
    destination: str | None = Field(default=None, min_length=2, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=30)
    travelers: int | None = Field(default=None, ge=1, le=20)
    budget_amount: Decimal | None = Field(default=None, gt=0, le=999999999, max_digits=12, decimal_places=2)
    currency: Currency | None = None
    interests: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(default_factory=list, max_length=100)
    dietary_preferences: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    mobility_preferences: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    nightlife_preference: bool | None = None
    accommodation_preference: HotelStyle | None = None
    activity_intensity: Pace | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.duration_days and not self.end_date:
            self.end_date = self.start_date + timedelta(days=self.duration_days - 1)
        if self.start_date and self.end_date:
            duration = (self.end_date - self.start_date).days + 1
            if not 1 <= duration <= 30:
                raise ValueError("Date range must be between 1 and 30 days")
            if self.duration_days and self.duration_days != duration:
                raise ValueError("Duration conflicts with the supplied dates")
            self.duration_days = duration
        return self

    def missing_fields(self) -> list[str]:
        return [
            key
            for key in ("destination", "origin", "start_date", "end_date", "travelers", "budget_amount", "currency")
            if getattr(self, key) is None
        ]

    def to_trip_create(self) -> TripCreate:
        if missing := self.missing_fields():
            raise AppError(422, "MISSING_TRIP_FIELDS", "Complete the trip details first.", {"missing_fields": missing})
        return TripCreate(
            destination_city=self.destination,
            origin=self.origin,
            start_date=self.start_date,
            end_date=self.end_date,
            traveler_count=self.travelers,
            total_budget=float(self.budget_amount),
            currency=self.currency,
            pace=self.activity_intensity or "balanced",
            max_walking_per_day=30 if "minimal_walking" in self.mobility_preferences else 120,
            hotel_style=self.accommodation_preference or "midrange",
        )


class NormalizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    known: TripRequest = Field(default_factory=TripRequest)


class NormalizeRead(BaseModel):
    status: Literal["ready", "needs_input"]
    request: TripRequest
    missing_fields: list[str]
    question: str | None


def normalize(payload: NormalizeInput) -> NormalizeRead:
    try:
        slots = OrchestratorParser()._parse_fallback(payload.text, TripState()).extracted_slots
        extracted = {
            "origin": slots.origin,
            "destination": slots.destination,
            "start_date": slots.start_date,
            "end_date": slots.end_date,
            "duration_days": slots.days,
            "travelers": slots.traveler_count,
            "budget_amount": slots.budget_amount,
            "currency": slots.budget_currency,
            "activity_intensity": slots.pace,
            "accommodation_preference": slots.hotel_style,
        }
        values = payload.known.model_dump()
        values.update({key: value for key, value in extracted.items() if value is not None})
        for key, additions in (
            ("interests", [p.category for p in slots.preferences if not p.remove]),
            ("dietary_preferences", slots.dietary_requirements),
            ("mobility_preferences", slots.accessibility_requirements),
        ):
            values[key] = list(dict.fromkeys([*values[key], *additions]))
        if slots.walking_limit is not None and slots.walking_limit <= 30:
            values["mobility_preferences"] = list(dict.fromkeys([*values["mobility_preferences"], "minimal_walking"]))
        for pref in slots.preferences:
            if pref.category == "nightlife":
                values["nightlife_preference"] = not pref.remove and pref.score > 0
        request = TripRequest.model_validate(values)
    except ValueError as exc:
        raise AppError(
            422, "INVALID_TRIP_INPUT", "The trip contains an invalid date, amount or traveler count."
        ) from exc
    missing = request.missing_fields()
    questions = {
        "destination": "Where would you like to go?",
        "origin": "Where are you traveling from?",
        "start_date": "What is your departure date?",
        "end_date": "What is your return date or trip duration?",
        "travelers": "How many people are traveling, including you?",
        "budget_amount": "What is your total budget?",
        "currency": "Which currency is your budget in?",
    }
    return NormalizeRead(
        status="needs_input" if missing else "ready",
        request=request,
        missing_fields=missing,
        question=questions[missing[0]] if missing else None,
    )
