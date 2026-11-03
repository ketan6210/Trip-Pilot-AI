from __future__ import annotations

import logging

from backend.orchestration.schemas import TripState
from backend.parsing.preference_parser import Preference

logger = logging.getLogger(__name__)


def apply_question_guard(state: TripState, question_key: str | None, raw_answer: str) -> TripState:
    if not question_key:
        state.last_question_key = None
        state.question_attempt_count = 0
        return state
    normalized_answer = raw_answer.strip().casefold()
    if raw_answer.strip().endswith("?") or normalized_answer.startswith(
        (
            "what ",
            "why ",
            "how ",
            "when ",
            "where ",
            "who ",
            "which ",
            "can ",
            "could ",
            "should ",
            "is ",
            "are ",
        )
    ):
        return state
    if state.last_question_key == question_key:
        state.question_attempt_count += 1
    else:
        state.last_question_key = question_key
        state.question_attempt_count = 1

    if state.question_attempt_count < 2:
        return state
    if question_key == "preferences":
        state.custom_preferences.append(
            Preference(
                raw_text=raw_answer.strip() or "varied experiences",
                weight=0.6,
                confidence=0.25,
                is_custom=True,
            )
        )
        state.ambiguity_log.append("Preference answer stayed ambiguous; stored the raw answer as a custom preference.")
        logger.info(
            "clarification_fallback field=preferences action=stored_custom attempts=%d", state.question_attempt_count
        )
    elif question_key == "budget_currency" and (state.budget_amount or state.total_budget):
        # Repetition is never consent to a currency. Keep the field missing.
        logger.info("clarification_pending field=budget_currency attempts=%d", state.question_attempt_count)
    return state
