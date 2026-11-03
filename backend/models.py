from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_trip_dates"),
        CheckConstraint("traveler_count BETWEEN 1 AND 20", name="ck_trip_travelers"),
        CheckConstraint("total_budget > 0", name="ck_trip_budget"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()), unique=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    destination_city: Mapped[str] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    traveler_count: Mapped[int] = mapped_column(Integer)
    total_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    pace: Mapped[str] = mapped_column(String(20), default="balanced")
    transport_mode: Mapped[str] = mapped_column(String(30), default="mixed")
    max_walking_per_day: Mapped[int] = mapped_column(Integer, default=120)
    hotel_style: Mapped[str] = mapped_column(String(20), default="midrange")
    flight_included: Mapped[bool] = mapped_column(Boolean, default=True)
    trip_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    travelers: Mapped[list[Traveler]] = relationship(back_populates="trip", cascade="all, delete-orphan")
    budget: Mapped[TripBudget | None] = relationship(back_populates="trip", cascade="all, delete-orphan", uselist=False)
    plans: Mapped[list[PlanVariant]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class Traveler(Base):
    __tablename__ = "travelers"
    __table_args__ = (UniqueConstraint("trip_id", "user_id", name="uq_traveler_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    preference_text: Mapped[str] = mapped_column(Text, default="")
    budget_preference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    walking_tolerance: Mapped[int] = mapped_column(Integer, default=120)
    preferred_start_time: Mapped[time] = mapped_column(Time, default=time(9, 0))
    preferred_end_time: Mapped[time] = mapped_column(Time, default=time(22, 0))
    dietary_restrictions: Mapped[list[str]] = mapped_column(JSON, default=list)
    accessibility_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    trip: Mapped[Trip] = relationship(back_populates="travelers")
    preferences: Mapped[list[TravelerPreference]] = relationship(
        back_populates="traveler", cascade="all, delete-orphan"
    )


class TravelerPreference(Base):
    __tablename__ = "traveler_preferences"
    __table_args__ = (UniqueConstraint("traveler_id", "category", "subcategory"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    traveler_id: Mapped[int] = mapped_column(ForeignKey("travelers.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    subcategory: Mapped[str] = mapped_column(String(50), default="general")
    score: Mapped[int] = mapped_column(Integer)
    is_must_do: Mapped[bool] = mapped_column(Boolean, default=False)
    is_avoid: Mapped[bool] = mapped_column(Boolean, default=False)
    traveler: Mapped[Traveler] = relationship(back_populates="preferences")


class TripBudget(Base):
    __tablename__ = "trip_budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), unique=True)
    mode: Mapped[str] = mapped_column(String(20), default="fixed")
    categories: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    trip: Mapped[Trip] = relationship(back_populates="budget")


class PlanVariant(Base):
    __tablename__ = "plan_variants"
    __table_args__ = (
        Index(
            "uq_selected_plan_per_trip",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'selected'"),
            sqlite_where=text("status = 'selected'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    plan_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(100))
    estimated_total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    estimated_per_person_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    mean_group_satisfaction: Mapped[float] = mapped_column(Float)
    minimum_traveler_satisfaction: Mapped[float] = mapped_column(Float)
    satisfaction_variance: Mapped[float] = mapped_column(Float)
    fairness_score: Mapped[float] = mapped_column(Float)
    travel_minutes: Mapped[int] = mapped_column(Integer)
    walking_intensity: Mapped[str] = mapped_column(String(20))
    activity_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    plan_set_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    trip: Mapped[Trip] = relationship(back_populates="plans")
    votes: Mapped[list[Vote]] = relationship(back_populates="plan", cascade="all, delete-orphan")
    cost_entries: Mapped[list[PlanCostEntry]] = relationship(back_populates="plan", cascade="all, delete-orphan")
    meals: Mapped[list[Meal]] = relationship(back_populates="plan", cascade="all, delete-orphan")
    expense_items: Mapped[list[ExpenseLineItem]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class PlanCostEntry(Base):
    __tablename__ = "plan_cost_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    cost_id: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    item_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expected: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    maximum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    shared_or_personal: Mapped[str] = mapped_column(String(20))
    price_type: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    plan: Mapped[PlanVariant] = relationship(back_populates="cost_entries")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("plan_variant_id", "traveler_id", "vote_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    plan_variant_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    traveler_id: Mapped[int] = mapped_column(ForeignKey("travelers.id", ondelete="CASCADE"), index=True)
    vote_type: Mapped[str] = mapped_column(String(20), default="vote")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    plan: Mapped[PlanVariant] = relationship(back_populates="votes")


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (
        UniqueConstraint("external_provider", "external_place_id"),
        Index("ix_places_city_category", "city", "category"),
        Index("ix_places_city_neighborhood", "city", "neighborhood"),
        Index("ix_places_coordinates", "latitude", "longitude"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    external_provider: Mapped[str] = mapped_column(String(40))
    external_place_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100))
    borough_or_district: Mapped[str] = mapped_column(String(100))
    neighborhood: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(50))
    subcategory: Mapped[str] = mapped_column(String(50))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    rating: Mapped[float] = mapped_column(Float)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    rating_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    google_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone_source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    price_level: Mapped[int] = mapped_column(Integer, default=1)
    cost_min: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_expected: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_max: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    opening_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    indoor_outdoor: Mapped[str] = mapped_column(String(20), default="mixed")
    age_restriction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reservation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    official_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    maps_url: Mapped[str] = mapped_column(String(500))
    ticket_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reservation_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    image_source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_attribution: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost_status: Mapped[str] = mapped_column(String(30), default="ESTIMATED")
    cost_source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    context: Mapped[PlaceContext | None] = relationship(
        back_populates="place", cascade="all, delete-orphan", uselist=False
    )

    def _duration_estimate(self):
        from backend.itinerary.duration_model import estimate_duration

        return estimate_duration(
            self.category,
            self.subcategory,
            name=self.name,
            tags=self.tags,
        )

    @property
    def duration_min(self) -> int:
        return self._duration_estimate().minimum

    @property
    def duration_expected(self) -> int:
        return self._duration_estimate().expected

    @property
    def duration_max(self) -> int:
        return self._duration_estimate().maximum

    @property
    def best_time_of_day(self) -> str:
        from backend.itinerary.best_time import best_time

        return best_time(self.category, self.subcategory, self.name).label

    @property
    def accessibility(self) -> str:
        metadata = self.source_metadata or {}
        return str(metadata.get("accessibility") or metadata.get("wheelchair") or "unknown")

    @property
    def popularity(self) -> dict[str, float | int]:
        return {"rating": self.rating, "review_count": self.review_count}


class TripStateRecord(Base):
    __tablename__ = "trip_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"), unique=True, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="collecting")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class OrchestrationMessage(Base):
    __tablename__ = "orchestration_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trip_states.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    structured_command: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class PlanDetail(Base):
    __tablename__ = "plan_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), unique=True, index=True)
    stage: Mapped[str] = mapped_column(String(40), default="SELECTED")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class PlaceContext(Base):
    __tablename__ = "place_contexts"
    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), unique=True, index=True)
    short_description: Mapped[str] = mapped_column(Text, default="")
    why_visit: Mapped[list[str]] = mapped_column(JSON, default=list)
    known_for: Mapped[list[str]] = mapped_column(JSON, default=list)
    historical_significance: Mapped[str | None] = mapped_column(Text, nullable=True)
    cultural_significance: Mapped[str | None] = mapped_column(Text, nullable=True)
    architectural_significance: Mapped[str | None] = mapped_column(Text, nullable=True)
    famous_people: Mapped[list[str]] = mapped_column(JSON, default=list)
    notable_events: Mapped[list[str]] = mapped_column(JSON, default=list)
    movie_tv_connections: Mapped[list[str]] = mapped_column(JSON, default=list)
    pop_culture_connections: Mapped[list[str]] = mapped_column(JSON, default=list)
    local_tips: Mapped[list[str]] = mapped_column(JSON, default=list)
    interesting_facts: Mapped[list[str]] = mapped_column(JSON, default=list)
    best_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_visit_focus: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    place: Mapped[Place] = relationship(back_populates="context")
    sources: Mapped[list[PlaceContextSource]] = relationship(back_populates="context", cascade="all, delete-orphan")


class PlaceContextSource(Base):
    __tablename__ = "place_context_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("place_contexts.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(160))
    source_url: Mapped[str] = mapped_column(String(1000))
    claim_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    context: Mapped[PlaceContext] = relationship(back_populates="sources")


class Meal(Base):
    __tablename__ = "meals"
    __table_args__ = (UniqueConstraint("plan_id", "day_number", "meal_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    day_number: Mapped[int] = mapped_column(Integer, index=True)
    meal_type: Mapped[str] = mapped_column(String(20))
    restaurant_place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="RESTRICT"), index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    traveler_count: Mapped[int] = mapped_column(Integer)
    min_cost_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    expected_cost_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_cost_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    expected_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason_selected: Mapped[str] = mapped_column(Text)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_quality: Mapped[str] = mapped_column(String(30), default="ESTIMATED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    plan: Mapped[PlanVariant] = relationship(back_populates="meals")
    restaurant: Mapped[Place] = relationship()


class ExpenseLineItem(Base):
    __tablename__ = "expense_line_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    subcategory: Mapped[str] = mapped_column(String(60), default="general")
    day_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    itinerary_item_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(60), default="group")
    unit_cost_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    unit_cost_expected: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    unit_cost_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    subtotal_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    subtotal_expected: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    subtotal_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    shared_or_personal: Mapped[str] = mapped_column(String(20), default="SHARED")
    traveler_count: Mapped[int] = mapped_column(Integer, default=1)
    taxes: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tips: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_expected: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3))
    calculation_expression: Mapped[str] = mapped_column(Text)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_type: Mapped[str] = mapped_column(String(40), default="ESTIMATED")
    source_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_quality: Mapped[str] = mapped_column(String(30), default="ESTIMATED")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    plan: Mapped[PlanVariant] = relationship(back_populates="expense_items")


class BudgetSnapshot(Base):
    __tablename__ = "budget_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    expected_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    category_totals: Mapped[dict] = mapped_column(JSON, default=dict)
    reconciliation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), unique=True)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id"),
        CheckConstraint("role IN ('OWNER', 'EDITOR', 'VIEWER')", name="ck_member_role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))


class GroupVote(Base):
    __tablename__ = "group_votes"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)


class ItineraryDay(Base):
    __tablename__ = "itinerary_days"
    __table_args__ = (UniqueConstraint("plan_id", "day_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    day_number: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    activities: Mapped[list[ItineraryActivity]] = relationship(
        cascade="all, delete-orphan", order_by="ItineraryActivity.position"
    )


class ItineraryActivity(Base):
    __tablename__ = "itinerary_activities"
    __table_args__ = (
        UniqueConstraint("day_id", "position"),
        CheckConstraint("end_minute > start_minute AND start_minute >= 0", name="ck_activity_time"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="ck_activity_cost"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("itinerary_days.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    category: Mapped[str] = mapped_column(String(50))
    reason_for_selection: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    place: Mapped[Place] = relationship()


class PlanningPolicy(Base):
    __tablename__ = "planning_policies"
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)


class TravelerPlaceConstraint(Base):
    __tablename__ = "traveler_place_constraints"
    __table_args__ = (UniqueConstraint("traveler_id", "place_id"), CheckConstraint("kind IN ('must', 'veto')"))
    id: Mapped[int] = mapped_column(primary_key=True)
    traveler_id: Mapped[int] = mapped_column(ForeignKey("travelers.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(8))


class PlanningBallot(Base):
    __tablename__ = "planning_ballots"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", "plan_id"),
        UniqueConstraint("trip_id", "user_id", "place_id"),
        CheckConstraint("(plan_id IS NULL) != (place_id IS NULL)", name="ck_ballot_target"),
        CheckConstraint("value BETWEEN -1 AND 1", name="ck_ballot_value"),
        CheckConstraint("rank IS NULL OR rank BETWEEN 1 AND 20", name="ck_ballot_rank"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    value: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)


class PlanningRun(Base):
    __tablename__ = "planning_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("trip_states.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("planning_runs.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    latency_ms: Mapped[float] = mapped_column(Float)


class ItineraryVersion(Base):
    __tablename__ = "itinerary_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan_variants.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trip_version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(500))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
