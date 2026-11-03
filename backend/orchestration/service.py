import logging
import re

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.conversation.missing_fields import MissingFieldPolicy
from backend.geography.maps import google_maps_place_url
from backend.models import (
    OrchestrationMessage,
    PlanVariant,
    Traveler,
    TravelerPreference,
    Trip,
    TripBudget,
    TripStateRecord,
)
from backend.orchestration.parser import DESTINATION_PREFIX, OrchestratorParser
from backend.orchestration.schemas import OrchestratorResponse, SessionRead, TripState
from backend.orchestration.state import apply_command, find_missing_fields
from backend.parsing.currency_parser import parse_currency
from backend.planning import generate_plan_variants, serialize_plan
from backend.providers import get_place_provider
from backend.providers.base import PlaceSearch

logger = logging.getLogger(__name__)


class OrchestrationService:
    def __init__(self, db: Session):
        self.db = db
        self.parser = OrchestratorParser()
        self.missing_policy = MissingFieldPolicy()

    def create_session(self, trip_id: int | None = None, owner_id: str | None = None) -> SessionRead:
        state = TripState(trip_id=trip_id)
        record = TripStateRecord(
            trip_id=trip_id, owner_id=owner_id, state=state.model_dump(mode="json"), status="collecting"
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return SessionRead(id=record.id, status=record.status, state=state)

    def get_session(self, session_id: int) -> SessionRead | None:
        record = self.db.get(TripStateRecord, session_id)
        if not record:
            return None
        return SessionRead(id=record.id, status=record.status, state=TripState.model_validate(record.state))

    def process_message(self, session_id: int, message: str) -> OrchestratorResponse:
        from backend.llm.telemetry import Trace, tracing

        trace = Trace()
        with tracing(trace):
            result = self._process_message(session_id, message)
        record = self.db.get(TripStateRecord, session_id)
        trace.save(
            self.db, trip_id=record.trip_id, session_id=session_id, user_id=record.owner_id, operation="conversation"
        )
        self.db.commit()
        return result

    def _process_message(self, session_id: int, message: str) -> OrchestratorResponse:
        record = self.db.get(TripStateRecord, session_id)
        if not record:
            raise LookupError("orchestration session not found")
        current = TripState.model_validate(record.state)
        command, parser_mode = self.parser.parse(message, current)
        updated = apply_command(current, command)
        explicit_budget = parse_currency(message, updated.destination)
        if explicit_budget.amount and explicit_budget.explicit:
            updated.budget_amount = updated.total_budget = explicit_budget.amount
            updated.budget_currency = updated.currency = explicit_budget.currency
            updated.budget_source_text = explicit_budget.source_text
            updated.budget_confidence = explicit_budget.confidence
        decision = self.missing_policy.evaluate(updated, message)
        logger.info(
            "trip_message_parsed mode=%s budget_confidence=%.3f broad_preferences=%d custom_preferences=%d question_key=%s attempts=%d",
            parser_mode,
            updated.budget_confidence,
            len(updated.broad_preferences),
            len(updated.custom_preferences),
            decision.question_key,
            updated.question_attempt_count,
        )
        missing = decision.missing_fields
        command.missing_fields = missing
        command.requires_followup = decision.requires_followup
        command.followup_question = decision.question
        ready = not decision.requires_followup
        record.state = updated.model_dump(mode="json")
        record.status = "ready" if ready else "collecting"
        direct_answer = None
        if self.parser.is_question(message):
            direct_answer = self.parser.answer_question(
                message,
                updated,
                live_context=self._live_question_context(message, updated),
            )
        if direct_answer and command.followup_question:
            assistant_message = f"{direct_answer}\n\nTo keep building your itinerary: {command.followup_question}"
        elif direct_answer:
            assistant_message = (
                f"{direct_answer}\n\nYour trip brief is complete, so you can generate four plans whenever you're ready."
            )
        elif command.followup_question:
            assistant_message = command.followup_question
        else:
            assistant_message = "Great — I have the destination, dates, group size, budget, and interests. No traveler names are required. Use the Generate four plans button below."
        self.db.add(OrchestrationMessage(session_id=session_id, role="user", content=message))
        self.db.add(
            OrchestrationMessage(
                session_id=session_id,
                role="assistant",
                content=assistant_message,
                structured_command=command.model_dump(mode="json"),
            )
        )
        self.db.commit()
        return OrchestratorResponse(
            session_id=session_id,
            parser_mode=parser_mode,
            assistant_message=assistant_message,
            ready=ready,
            command=command,
            state=updated,
        )

    def _live_question_context(self, message: str, state: TripState) -> list[dict]:
        """Retrieve authoritative provider records for destination-specific travel questions."""
        if not state.destination:
            return []
        lowered = message.lower()
        if not re.search(
            r"\b(?:place|places|visit|see|do|activity|activities|attraction|museum|gallery|club|pub|bar|"
            r"nightlife|restaurant|food|cafe|park|garden|nature|landmark|historic|history|shopping|shop|"
            r"sport|stadium|entertainment|theatre|theater|cinema|ticket|tickets|cost|price|free|open|hours)\b",
            lowered,
        ):
            return []

        category = None
        category_patterns = (
            ("nightlife", r"\b(?:nightlife|nightclub|club|pub|bar|cocktail)"),
            ("museums", r"\b(?:museum|gallery|exhibition)"),
            ("food", r"\b(?:restaurant|food|dining|cafe|café|cuisine)"),
            ("nature", r"\b(?:park|garden|nature|outdoor|trail|hike)"),
            ("history", r"\b(?:historic|history|heritage)"),
            ("shopping", r"\b(?:shopping|shop|market|mall)"),
            ("sports", r"\b(?:sport|stadium|game)"),
            ("entertainment", r"\b(?:entertainment|theatre|theater|cinema|concert|music|show)"),
            ("landmarks", r"\b(?:landmark|attraction|sightseeing|monument)"),
        )
        for candidate, pattern in category_patterns:
            if re.search(pattern, lowered):
                category = candidate
                break

        try:
            places = get_place_provider(self.db).search(PlaceSearch(city=state.destination, category=category, limit=8))
        except Exception:
            return []

        context: list[dict] = []
        for place in places:
            published_cost = None
            if place.cost_status in {"FREE", "PUBLISHED"}:
                published_cost = float(place.cost_expected)
            context.append(
                {
                    "name": place.name,
                    "category": place.category,
                    "subcategory": place.subcategory,
                    "neighborhood": place.neighborhood,
                    "cost_status": place.cost_status,
                    "cost": published_cost,
                    "currency": place.cost_currency,
                    "opening_hours": (place.opening_hours or {}).get("raw"),
                    "official_url": place.official_url,
                    "ticket_url": place.ticket_url,
                    "maps_url": google_maps_place_url(place.name, place.address, place.latitude, place.longitude),
                    "provider": place.external_provider,
                }
            )
        return context

    def messages(self, session_id: int) -> list[OrchestrationMessage]:
        return list(
            self.db.scalars(
                select(OrchestrationMessage)
                .where(OrchestrationMessage.session_id == session_id)
                .order_by(OrchestrationMessage.id)
            )
        )

    def generate_plans(self, session_id: int) -> dict:
        record = self.db.get(TripStateRecord, session_id)
        if not record:
            raise LookupError("orchestration session not found")
        state = TripState.model_validate(record.state)
        # Repair already-saved conversational prefixes from older parser versions.
        # Restrict repair to explicit travel wording; do not reinterpret bare cities.
        if state.destination and DESTINATION_PREFIX.match(state.destination):
            cleaned = self.parser._short_place(state.destination)
            if cleaned:
                state.destination = cleaned
                record.state = state.model_dump(mode="json")
        missing = find_missing_fields(state)
        if missing:
            raise ValueError(f"Complete these trip details first: {', '.join(missing)}")
        from backend.schemas import TripCreate

        TripCreate(
            destination_city=state.destination,
            origin=state.origin,
            start_date=state.start_date,
            end_date=state.end_date,
            traveler_count=state.traveler_count,
            total_budget=state.budget_amount or state.total_budget,
            currency=state.budget_currency or state.currency,
            pace=state.pace,
            transport_mode=state.transport_mode,
            max_walking_per_day=state.walking_limit,
            hotel_style=state.hotel_style,
            flight_included=state.flight_included,
        )

        trip = self.db.get(Trip, record.trip_id) if record.trip_id else None
        if not trip:
            trip = Trip(
                destination_city=state.destination,
                origin=state.origin,
                start_date=state.start_date,
                end_date=state.end_date,
                traveler_count=state.traveler_count,
                total_budget=state.budget_amount or state.total_budget,
                currency=state.budget_currency or state.currency,
                pace=state.pace,
                transport_mode=state.transport_mode,
                max_walking_per_day=state.walking_limit,
                hotel_style=state.hotel_style,
                flight_included=state.flight_included,
            )
            self.db.add(trip)
            self.db.flush()
            if record.owner_id:
                from backend.domain.trips import attach_owner

                attach_owner(self.db, trip, record.owner_id)
            preference_rows: list[tuple[str, str, int]] = []
            for category, score in state.preferences.items():
                if category == "parks":
                    preference_rows.append(("nature", "parks", score))
                elif category == "photography":
                    preference_rows.append(("landmarks", "photography", score))
                else:
                    preference_rows.append((category, "general", score))
            for key, score in state.subcategory_preferences.items():
                category, _, subcategory = key.partition(":")
                preference_rows.append((category, subcategory or "general", score))
            for index in range(state.traveler_count):
                traveler = Traveler(
                    trip_id=trip.id,
                    display_name=f"Traveler {index + 1}",
                    walking_tolerance=state.walking_limit,
                    dietary_restrictions=state.dietary_requirements,
                    accessibility_requirements=state.accessibility_requirements,
                )
                self.db.add(traveler)
                self.db.flush()
                for category, subcategory, score in preference_rows:
                    self.db.add(
                        TravelerPreference(
                            traveler_id=traveler.id,
                            category=category,
                            subcategory=subcategory,
                            score=score,
                            is_must_do=False,
                            is_avoid=False,
                        )
                    )
            flight_share = 0.20 if state.flight_included else 0.0
            shares = {"flight": flight_share, "hotel": 0.35, "food": 0.20, "nightlife": 0.08, "attraction": 0.12}
            shares["buffer"] = 1.0 - sum(shares.values())
            trip.budget = TripBudget(
                mode="fixed",
                categories={key: round(state.total_budget * share, 2) for key, share in shares.items()},
            )
            state.trip_id = trip.id
            record.trip_id = trip.id
            record.state = state.model_dump(mode="json")
            # Make the trip, anonymous travelers, and budget durable before slower
            # live-provider work begins. This also makes a retry after provider
            # failure reuse the same complete trip instead of holding stale ORM rows.
            record.status = "generating"
            self.db.commit()
            trip = self.db.get(Trip, trip.id)
            record = self.db.get(TripStateRecord, session_id)
        else:
            self._sync_existing_trip(trip, state)
            record.status = "generating"
            self.db.commit()
            self.db.refresh(trip)

        if not trip.budget:
            flight_share = 0.20 if state.flight_included else 0.0
            shares = {"flight": flight_share, "hotel": 0.35, "food": 0.20, "nightlife": 0.08, "attraction": 0.12}
            shares["buffer"] = 1.0 - sum(shares.values())
            trip.budget = TripBudget(
                mode="fixed",
                categories={key: round(state.total_budget * share, 2) for key, share in shares.items()},
            )
            self.db.commit()
            self.db.refresh(trip)

        try:
            plans = generate_plan_variants(self.db, trip.id, record.owner_id)
        except Exception:
            self.db.rollback()
            record = self.db.get(TripStateRecord, session_id)
            record.status = "collecting"
            self.db.commit()
            raise
        record.status = "plans_ready"
        self.db.commit()
        travelers = list(self.db.scalars(select(Traveler).where(Traveler.trip_id == trip.id).order_by(Traveler.id)))
        return {
            "trip": {
                "id": trip.id,
                "destination_city": trip.destination_city,
                "origin": trip.origin,
                "start_date": trip.start_date,
                "end_date": trip.end_date,
                "traveler_count": trip.traveler_count,
                "total_budget": float(trip.total_budget),
                "currency": trip.currency,
                "pace": trip.pace,
                "transport_mode": trip.transport_mode,
                "max_walking_per_day": trip.max_walking_per_day,
                "hotel_style": trip.hotel_style,
                "flight_included": trip.flight_included,
                "trip_version": trip.trip_version,
            },
            "travelers": [{"id": traveler.id, "display_name": traveler.display_name} for traveler in travelers],
            "plans": [serialize_plan(plan) for plan in plans],
            "flight_quote": plans[0].summary.get("flight_quote") if plans else None,
        }

    def _sync_existing_trip(self, trip: Trip, state: TripState) -> None:
        """Apply chat corrections before retrying a previously created trip."""
        values = {
            "destination_city": state.destination,
            "origin": state.origin,
            "start_date": state.start_date,
            "end_date": state.end_date,
            "traveler_count": state.traveler_count,
            "total_budget": state.budget_amount or state.total_budget,
            "currency": (state.budget_currency or state.currency or trip.currency).upper(),
            "pace": state.pace,
            "transport_mode": state.transport_mode,
            "max_walking_per_day": state.walking_limit,
            "hotel_style": state.hotel_style,
            "flight_included": state.flight_included,
        }
        changed = any(getattr(trip, key) != value for key, value in values.items())
        target_preferences = {
            ("nature" if category == "parks" else category, "parks" if category == "parks" else "general"): score
            for category, score in state.preferences.items()
        }
        for key, score in state.subcategory_preferences.items():
            category, _, subcategory = key.partition(":")
            target_preferences[(category, subcategory or "general")] = score
        old_budget = float(trip.total_budget)
        old_count = trip.traveler_count
        for key, value in values.items():
            setattr(trip, key, value)
        if trip.budget and old_budget > 0 and float(trip.total_budget) != old_budget:
            ratio = float(trip.total_budget) / old_budget
            trip.budget.categories = {
                key: round(float(value) * ratio, 2) for key, value in trip.budget.categories.items()
            }
        if trip.traveler_count > old_count:
            for index in range(old_count, trip.traveler_count):
                self.db.add(
                    Traveler(
                        trip_id=trip.id, display_name=f"Traveler {index + 1}", walking_tolerance=state.walking_limit
                    )
                )
        elif trip.traveler_count < old_count:
            travelers = list(self.db.scalars(select(Traveler).where(Traveler.trip_id == trip.id).order_by(Traveler.id)))
            removed = travelers[trip.traveler_count :]
            if removed:
                self.db.execute(
                    delete(TravelerPreference).where(TravelerPreference.traveler_id.in_([item.id for item in removed]))
                )
                for traveler in removed:
                    self.db.delete(traveler)
        self.db.flush()
        for traveler in self.db.scalars(select(Traveler).where(Traveler.trip_id == trip.id)):
            current_preferences = {(pref.category, pref.subcategory): pref.score for pref in traveler.preferences}
            if current_preferences != target_preferences:
                for pref in list(traveler.preferences):
                    self.db.delete(pref)
                self.db.flush()
                for (category, subcategory), score in target_preferences.items():
                    self.db.add(
                        TravelerPreference(
                            traveler_id=traveler.id, category=category, subcategory=subcategory, score=score
                        )
                    )
                changed = True
                self.db.expire(traveler, ["preferences"])
            for field, desired in (
                ("dietary_restrictions", state.dietary_requirements),
                ("accessibility_requirements", state.accessibility_requirements),
                ("walking_tolerance", state.walking_limit),
            ):
                if getattr(traveler, field) != desired:
                    setattr(traveler, field, desired)
                    changed = True
        if not changed:
            return
        trip.trip_version += 1
        self.db.execute(
            update(PlanVariant)
            .where(PlanVariant.trip_id == trip.id, PlanVariant.status != "stale")
            .values(status="stale")
        )
