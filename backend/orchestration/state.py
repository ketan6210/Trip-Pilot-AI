from datetime import timedelta

from backend.orchestration.schemas import OrchestratorCommand, TripState

CRITICAL_FIELDS = (
    "destination",
    "origin",
    "start_date",
    "end_date",
    "traveler_count",
    "total_budget",
    "budget_currency",
    "preferences",
)
UNKNOWN_TEXT = {"unknown", "none", "null", "n/a", "na", "not provided", "not specified", "unspecified", "tbd"}


def apply_command(state: TripState, command: OrchestratorCommand) -> TripState:
    """Merge validated slots into durable state without relying on chat history."""
    slots = command.extracted_slots
    previous_days = state.days
    if slots.end_date is not None:
        state.date_range_confirmed = True
    elif slots.start_date is not None:
        # A duration can provide a useful provisional return date, but the user
        # still needs to confirm the actual return date before generation.
        state.date_range_confirmed = False
    values = slots.model_dump(
        exclude_none=True,
        exclude={
            "preferences",
            "flexible_preferences",
            "dietary_requirements",
            "accessibility_requirements",
            "must_visit",
            "avoid",
        },
    )
    for key, value in values.items():
        if key == "budget_delta":
            if state.total_budget is not None:
                state.total_budget = max(0, state.total_budget + value)
        elif hasattr(state, key):
            setattr(state, key, value)
    if slots.budget_amount is not None:
        state.budget_amount = slots.budget_amount
        state.total_budget = slots.budget_amount
    elif slots.total_budget is not None:
        state.total_budget = slots.total_budget
        state.budget_amount = slots.total_budget
    if slots.budget_currency:
        state.budget_currency = slots.budget_currency.upper()
        state.currency = slots.budget_currency.upper()
    elif slots.currency:
        state.currency = slots.currency.upper()
        state.budget_currency = slots.currency.upper()
    for preference in slots.preferences:
        key = preference.category.lower().strip()
        if preference.subcategory:
            key = f"{key}:{preference.subcategory.lower().strip()}"
            target = state.subcategory_preferences
        else:
            target = state.preferences
        if preference.remove:
            target.pop(key, None)
        else:
            target[key] = preference.score
            state.broad_preferences[preference.category.lower().strip()] = preference.score / 10
    for preference in slots.flexible_preferences:
        if preference.canonical_category:
            state.broad_preferences[preference.canonical_category] = preference.weight
            planner_category = preference.planner_category
            if planner_category:
                state.preferences[planner_category] = round(preference.weight * 10)
            for subcategory in preference.subcategories:
                if planner_category:
                    state.subcategory_preferences[f"{planner_category}:{subcategory}"] = round(preference.weight * 10)
        elif preference.is_custom:
            existing = {item.raw_text.casefold() for item in state.custom_preferences}
            if preference.raw_text.casefold() not in existing:
                state.custom_preferences.append(preference)
    for field in ("dietary_requirements", "accessibility_requirements", "must_visit", "avoid"):
        incoming = getattr(slots, field)
        current = getattr(state, field)
        setattr(state, field, list(dict.fromkeys([*current, *incoming])))
    if slots.start_date and slots.end_date is None and (slots.days or previous_days):
        state.days = slots.days or previous_days
        state.number_of_days = state.days
        state.end_date = state.start_date + timedelta(days=state.days - 1)
    if state.start_date and state.days and not state.end_date:
        state.end_date = state.start_date + timedelta(days=state.days - 1)
    if state.start_date and state.end_date:
        state.days = (state.end_date - state.start_date).days + 1
        state.number_of_days = state.days
    elif state.days:
        state.number_of_days = state.days
    return state


def find_missing_fields(state: TripState) -> list[str]:
    missing = []
    if not state.destination or state.destination.strip().lower() in UNKNOWN_TEXT:
        missing.append("destination")
    if not state.origin or state.origin.strip().lower() in UNKNOWN_TEXT:
        missing.append("origin")
    if not state.start_date:
        missing.append("start_date")
    if not state.end_date or not state.date_range_confirmed:
        missing.append("end_date")
    if not state.traveler_count:
        missing.append("traveler_count")
    if not (state.budget_amount or state.total_budget):
        missing.append("total_budget")
    if (state.budget_amount or state.total_budget) and not (state.budget_currency or state.currency):
        missing.append("budget_currency")
    if (
        not state.preferences
        and not state.broad_preferences
        and not state.subcategory_preferences
        and not state.custom_preferences
        and not state.must_visit
    ):
        missing.append("preferences")
    return missing


QUESTIONS = {
    "destination": "Where would you like to go?",
    "origin": "Where will you be traveling from?",
    "start_date": "What date would you like to depart?",
    "end_date": "What date would you like to return?",
    "traveler_count": "How many people are traveling?",
    "total_budget": "What approximate total budget should I plan around, including the currency?",
    "budget_currency": "Which currency is that budget in—for example INR, USD, GBP, or EUR?",
    "preferences": "What activities or places does your group enjoy? For example: museums, food, parks, nightlife, landmarks, or specific must-visit places.",
}


def followup_for(missing_fields: list[str]) -> str | None:
    return QUESTIONS[missing_fields[0]] if missing_fields else None
