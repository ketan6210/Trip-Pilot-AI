from __future__ import annotations

from dataclasses import dataclass

from backend.conversation.anti_loop import apply_question_guard
from backend.orchestration.schemas import TripState
from backend.orchestration.state import QUESTIONS, find_missing_fields


@dataclass(frozen=True)
class MissingFieldDecision:
    missing_fields: list[str]
    requires_followup: bool
    question_key: str | None
    question: str | None


class MissingFieldPolicy:
    def evaluate(self, state: TripState, raw_answer: str = "") -> MissingFieldDecision:
        missing = find_missing_fields(state)
        key = missing[0] if missing else None
        apply_question_guard(state, key, raw_answer)
        missing = find_missing_fields(state)
        key = missing[0] if missing else None
        if not key:
            state.last_question_key = None
            state.question_attempt_count = 0
            return MissingFieldDecision([], False, None, None)
        question = QUESTIONS[key]
        if key == "end_date" and state.end_date and state.days:
            question = (
                f"What date would you like to return? A {state.days}-day trip departing "
                f"{state.start_date.isoformat()} would return {state.end_date.isoformat()}, but please confirm or change it."
            )
        if key == "budget_currency" and state.budget_amount:
            question = f"You entered {state.budget_amount:,.0f}. Do you mean INR, USD, GBP, EUR, or another currency?"
        return MissingFieldDecision(missing, True, key, question)
