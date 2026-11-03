import json
import re
from datetime import date, timedelta
from difflib import get_close_matches

import httpx

from backend.orchestration.schemas import Intent, OrchestratorCommand, PreferenceSlot, TripSlots, TripState
from backend.parsing.currency_parser import CURRENCY_ALIASES as STRUCTURED_CURRENCY_ALIASES
from backend.parsing.currency_parser import parse_currency
from backend.parsing.preference_parser import Preference, parse_preferences
from backend.settings import settings

SYSTEM_PROMPT = """You are the single TripPilot orchestration parser. Extract intent and trip slots only.
Return the supplied structured schema. Never invent dates, prices, venue IDs, URLs, opening hours, routes,
or availability. Leave unknown fields null or empty and do not copy defaults from the current state into
extracted_slots. Do not calculate an end date from a duration. "Plan a trip" is CREATE_TRIP. Preference
scores use 0-10. Use canonical city names. The backend—not you—detects required fields, invokes tools,
enforces budgets, derives dates, and validates constraints."""

DESTINATIONS = {
    "nyc": "New York City",
    "new york": "New York City",
    "new york city": "New York City",
    "boston": "Boston",
    "chicago": "Chicago",
    "san francisco": "San Francisco",
    "los angeles": "Los Angeles",
    "la": "Los Angeles",
    "miami": "Miami",
    "washington dc": "Washington DC",
}
DESTINATION_PREFIX = re.compile(
    r"^(?:(?:(?:i|we)(?:['’]d)?\s+)?(?:wanna|want\s+to|would\s+like\s+to|plan\s+to)\s+)?"
    r"(?:(?:go(?:ing)?|travel(?:l?ing)?|fly(?:ing)?)\s+(?:to\s+)?|visit(?:ing)?\s+)(?:the\s+)?",
    re.IGNORECASE,
)
PREFERENCE_CATEGORIES = (
    "nightlife",
    "museums",
    "parks",
    "food",
    "nature",
    "shopping",
    "landmarks",
    "photography",
    "adventure",
    "history",
    "entertainment",
    "sports",
    "relaxation",
)
PLANNABLE_ACTIVITY_CATEGORIES = (
    "nightlife",
    "museums",
    "food",
    "nature",
    "shopping",
    "landmarks",
    "history",
    "entertainment",
    "sports",
)
PREFERENCE_PATTERNS = {
    "nightlife": (
        r"night\s*life",
        r"nightclubs?",
        r"clubs?",
        r"clubbing",
        r"pubs?",
        r"bars?",
        r"cocktails?",
        r"part(?:y|ies|ying)",
    ),
    "museums": (r"museums?", r"musems?", r"mesuems?", r"galler(?:y|ies)", r"art exhibits?", r"exhibitions?"),
    "parks": (r"parks?", r"gardens?", r"green spaces?"),
    "food": (r"food", r"restaurants?", r"dining", r"cuisine", r"caf(?:e|es|\u00e9|\u00e9s)", r"street food"),
    "nature": (r"nature", r"outdoors?", r"hikes?", r"hiking", r"trails?", r"beaches?", r"scenic views?"),
    "shopping": (r"shopping", r"shops?", r"markets?", r"boutiques?"),
    "landmarks": (r"landmarks?", r"monuments?", r"sightseeing", r"architecture"),
    "photography": (r"photography", r"photo spots?", r"taking photos?"),
    "adventure": (r"adventure", r"thrill(?:s|ing)?", r"extreme activities"),
    "history": (r"history", r"historic(?:al)?(?: sites?)?", r"histroic(?:al)?(?: places?| sites?)?", r"heritage"),
    "entertainment": (r"entertainment", r"concerts?", r"live music", r"shows?", r"theat(?:er|re)", r"comedy"),
    "sports": (r"sports?", r"games?", r"stadiums?"),
    "relaxation": (r"relaxation", r"spas?", r"wellness", r"relaxing"),
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
UNKNOWN_TEXT = {"unknown", "none", "null", "n/a", "na", "not provided", "not specified", "unspecified", "tbd"}
CURRENCY_ALIASES = {
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "₹": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "inr": "INR",
    "£": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "€": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "¥": "JPY",
    "yen": "JPY",
    "jpy": "JPY",
    "cad": "CAD",
    "aud": "AUD",
}


class OrchestratorParser:
    answer_source: str | None = None
    answer_notice: str | None = None

    def parse(self, message: str, state: TripState) -> tuple[OrchestratorCommand, str]:
        contextual = self._parse_contextual_answer(message, state)
        if contextual:
            return contextual, "contextual_followup"
        if self.is_question(message) and not self.is_update_request(message):
            return OrchestratorCommand(), "conversation"
        provider = settings.orchestrator_provider.strip().lower()
        if provider in {"huggingface", "hf"} or (provider == "auto" and settings.hf_token):
            try:
                from backend.llm.providers import HuggingFaceProvider
                from backend.llm.service import structured

                command = structured(
                    OrchestratorCommand,
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "system", "content": f"Validated TripState: {state.model_dump_json()}"},
                        {"role": "user", "content": message},
                    ],
                    purpose="constraint_extraction",
                    provider=HuggingFaceProvider(),
                )
                return self._ground_command(command, message, state), "huggingface"
            except Exception:
                return self._parse_fallback(message, state), "fallback_cloud_unavailable"
        if provider in {"ollama", "auto"}:
            try:
                return self._ground_command(self._parse_with_ollama(message, state), message, state), "ollama_local"
            except Exception:
                # A stopped local runtime must not make trip collection unusable.
                if provider == "ollama":
                    return self._parse_fallback(message, state), "fallback_local_unavailable"
        if provider in {"openai", "auto"} and settings.openai_api_key:
            try:
                return self._ground_command(self._parse_with_openai(message, state), message, state), "openai"
            except Exception:
                return self._parse_fallback(message, state), "fallback_cloud_unavailable"
        return self._parse_fallback(message, state), "deterministic_only"

    def _parse_with_ollama(self, message: str, state: TripState) -> OrchestratorCommand:
        from backend.llm.providers import OllamaProvider
        from backend.llm.service import structured

        return structured(
            OrchestratorCommand,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"Validated TripState: {state.model_dump_json()}"},
                {"role": "user", "content": message},
            ],
            purpose="constraint_extraction",
            provider=OllamaProvider(),
        )

    @staticmethod
    def is_question(message: str) -> bool:
        """Recognize direct questions and common question-like requests."""
        text = message.strip().lower()
        return bool(
            text.endswith("?")
            or re.match(
                r"^(?:what|why|how|when|where|who|which|can|could|would|should|is|are|do|does|did|will|may)\b",
                text,
            )
            or re.match(r"^(?:tell me|explain|help me|recommend|suggest|compare)\b", text)
        )

    @staticmethod
    def is_update_request(message: str) -> bool:
        """Distinguish a requested state change from an informational question."""
        text = message.strip().lower()
        return bool(
            re.search(
                r"\b(?:change|set|update|add|remove|replace|move|make|replan|plan|generate|select|lock|unlock|increase|decrease|raise|lower|switch|include|exclude)\b",
                text,
            )
            or re.search(r"\b(?:go|travel|leave|fly)\s+(?:to|from)\b", text)
            or re.search(r"\b(?:spend|budget)\s+(?:is|to|at|around|under|over)?\s*\$?\d", text)
        )

    def answer_question(self, message: str, state: TripState, live_context: list[dict] | None = None) -> str:
        from backend.llm.answers import ANSWER_PROMPT, GroundedAnswer
        from backend.llm.providers import get_llm_provider
        from backend.llm.service import structured
        from backend.llm.telemetry import active_trace

        context = live_context or []
        self.answer_source = "unavailable"
        self.answer_notice = None
        try:
            provider = get_llm_provider()
            result = structured(
                GroundedAnswer,
                [
                    {
                        "role": "system",
                        "content": ANSWER_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": message,
                                "trip": state.model_dump(mode="json"),
                                "places": context,
                                "history": getattr(self, "conversation_history", []),
                                "retrieval_status": getattr(self, "retrieval_status", "no_verified_live_data"),
                            }
                        ),
                    },
                ],
                purpose="grounded_question",
                provider=provider,
            )
            result.check_evidence(context, state.model_dump(mode="json"))
            self.answer_source = provider.name
            sources = []
            for index in dict.fromkeys(result.place_indices):
                place = context[index]
                url = place.get("ticket_url") or place.get("official_url") or place.get("source_url")
                if url and url.startswith(("https://", "http://")):
                    sources.append(f"{place['name']}: {url}")
            return result.answer.strip() + ("\n\nSources:\n" + "\n".join(sources) if sources else "")
        except Exception as exc:
            trace = active_trace.get()
            if trace:
                trace.fallback = True
            self.answer_notice = {
                "LLM_NOT_CONFIGURED": "Add HF_TOKEN to the backend .env file and restart the API to enable Hugging Face answers.",
                "LLM_AUTH_FAILED": "Hugging Face rejected the token. Check its Inference Providers permission.",
                "LLM_CREDITS_EXHAUSTED": "Hugging Face inference credits are exhausted. Wait for renewal or choose the local model.",
                "LLM_RATE_LIMITED": "Hugging Face is rate limiting requests. Please try again shortly.",
                "LLM_MODEL_UNSUPPORTED": "The configured Hugging Face model or output format is unavailable. Check HF_MODEL.",
            }.get(
                getattr(exc, "code", ""),
                "The language model could not produce a verified response. Please try again shortly.",
            )
        return self.answer_notice + " Your trip details are saved."

    @staticmethod
    def _fallback_answer(message: str, state: TripState, live_context: list[dict] | None = None) -> str:
        """Useful offline answers for common planning questions when no model is running."""
        lowered = message.lower()
        if live_context:
            names = ", ".join(
                str(place["name"]) + (" (free entry)" if place.get("cost_status") == "FREE" else "")
                for place in live_context[:5]
            )
            unknown_count = sum(place.get("cost_status") == "UNKNOWN" for place in live_context[:5])
            verification = (
                f" {unknown_count} of these listings do not publish a verified admission price, so use their "
                "official or ticket links before booking."
                if unknown_count
                else ""
            )
            return (
                f"{names} appear in the provider results for {state.destination or 'your destination'}.{verification}"
            )
        if "excluding flight" in lowered or "without flight" in lowered:
            return "Excluding flights means the trip budget will be used for lodging, food, local transportation, and activities; airfare will stay outside that total."
        if "including flight" in lowered:
            return "Including flights means airfare must fit inside the total trip budget, leaving the remainder for lodging, food, local transportation, and activities."
        if any(word in lowered for word in ("nightlife", "club", "pub", "bar")):
            destination = f" in {state.destination}" if state.destination else ""
            return f"Nightlife{destination} can include clubs, pubs, cocktail bars, live music, and late-night food. TripPilot can favor the styles your group prefers."
        if any(word in lowered for word in ("relaxed", "balanced", "packed", "pace")):
            return "A relaxed plan leaves more downtime, a balanced plan mixes highlights with breaks, and a packed plan fits in more stops with tighter timing."
        if "budget" in lowered:
            return "TripPilot uses the total budget as a constraint across lodging, food, transport, activities, and any included flights, with a buffer for uncertainty."
        return "I can help with this question and use your saved trip context. The local language model is temporarily unavailable, so live or highly specific details should be verified before booking."

    def _ground_command(self, command: OrchestratorCommand, message: str, state: TripState) -> OrchestratorCommand:
        """Reconcile model output with high-confidence facts explicitly present in this turn."""
        explicit_command = self._parse_fallback(message, state)
        explicit = explicit_command.extracted_slots
        slots = command.extracted_slots
        scalar_fields = (
            "destination",
            "origin",
            "start_date",
            "end_date",
            "days",
            "traveler_count",
            "total_budget",
            "budget_delta",
            "currency",
            "flight_included",
            "pace",
            "transport_mode",
            "walking_limit",
            "preferred_start_time",
            "preferred_end_time",
            "hotel_style",
        )
        for field in scalar_fields:
            value = getattr(explicit, field)
            if value is not None:
                setattr(slots, field, value)
            else:
                # A model may echo current state or reinterpret a short answer as a
                # different slot. Existing validated values can only be changed by
                # an explicit deterministic match or the contextual-answer path.
                setattr(slots, field, None)
        # Budget aliases must not reintroduce values absent from the user's text.
        if explicit.budget_amount is None:
            slots.budget_amount = None
        if explicit.budget_currency is None:
            slots.budget_currency = None
        for field in ("destination", "origin", "currency", "pace", "transport_mode", "hotel_style"):
            value = getattr(slots, field)
            if isinstance(value, str) and value.strip().lower() in UNKNOWN_TEXT:
                setattr(slots, field, None)

        lowered = message.lower()
        if "flight" not in lowered:
            slots.flight_included = None
        people_context = re.search(r"\b(?:people|persons|travelers|travellers|guests|of us|we are|group of)\b", lowered)
        if explicit.traveler_count is None and not people_context:
            slots.traveler_count = None
        if not any(
            token in lowered for token in ("relaxed", "less busy", "balanced", "packed", "fast-paced", "slow-paced")
        ):
            slots.pace = None
        if not any(
            token in lowered
            for token in (
                "transport",
                "transit",
                "subway",
                "metro",
                "train",
                "bus",
                "taxi",
                "rideshare",
                "walking",
                "drive",
                "car",
            )
        ):
            slots.transport_mode = None
        if not any(token in lowered for token in ("walk", "walking")):
            slots.walking_limit = None
        if not any(token in lowered for token in ("hotel", "lodging", "hostel", "resort", "accommodation")):
            slots.hotel_style = None
        if explicit.start_date and explicit.days and explicit.end_date is None:
            slots.end_date = None

        for item in slots.preferences:
            if item.subcategory and item.subcategory.lower().strip() == item.category.lower().strip():
                item.subcategory = None
        allowed_model_categories = set(PREFERENCE_CATEGORIES)
        preferences = {
            (item.category.lower(), (item.subcategory or "").lower()): item
            for item in slots.preferences
            if item.category.lower() in allowed_model_categories
        }
        for item in explicit.preferences:
            preferences = {key: value for key, value in preferences.items() if key[0] != item.category.lower()}
            preferences[(item.category.lower(), (item.subcategory or "").lower())] = item
        slots.preferences = list(preferences.values())
        if explicit.flexible_preferences:
            slots.flexible_preferences = explicit.flexible_preferences
            # Deterministic explicit matches are authoritative and already
            # converted to the planner's category vocabulary.
            slots.preferences = explicit.preferences
        if explicit_command.intent != Intent.UNKNOWN:
            command.intent = explicit_command.intent
        if command.intent == Intent.CREATE_TRIP:
            command.operations = []
        command.requires_followup = False
        command.missing_fields = []
        command.followup_question = None
        return command

    def _parse_contextual_answer(self, message: str, state: TripState) -> OrchestratorCommand | None:
        """Handle concise answers to the current follow-up without spending an LLM call."""
        answer = re.sub(r"[,.!?'’\"]+$", "", message.strip())
        if not answer:
            return None
        if self.is_question(message):
            return None
        origin_missing = not state.origin or state.origin.strip().lower() in UNKNOWN_TEXT
        destination_missing = not state.destination or state.destination.strip().lower() in UNKNOWN_TEXT
        # A destination statement is not an answer to an origin follow-up.
        # Check its target before treating a short sentence as a bare city name.
        if DESTINATION_PREFIX.match(answer):
            destination = self._short_place(answer)
            if destination:
                return OrchestratorCommand(extracted_slots=TripSlots(destination=destination))
            return None
        if state.destination and origin_missing:
            place = self._short_place(answer)
            if place:
                return OrchestratorCommand(extracted_slots=TripSlots(origin=place))
        if destination_missing:
            place = self._short_place(answer)
            if place:
                return OrchestratorCommand(extracted_slots=TripSlots(destination=place))
        if state.destination and state.origin:
            concise_date = re.sub(
                r"^(?:(?:return|returning|come back|depart|departing|leave|leaving)(?:\s+on)?|until|to)\s+",
                "",
                answer,
                flags=re.IGNORECASE,
            )
            parsed_date = self._short_date(concise_date)
            if parsed_date:
                intent = Intent.CHANGE_TRIP_DATES if state.start_date else Intent.SET_TRIP_DATES
                if state.start_date and not state.date_range_confirmed:
                    return OrchestratorCommand(intent=intent, extracted_slots=TripSlots(end_date=parsed_date))
                return OrchestratorCommand(intent=intent, extracted_slots=TripSlots(start_date=parsed_date))
        if state.destination and state.origin and state.start_date and not state.days and not state.end_date:
            duration = re.fullmatch(r"(\d{1,2})(?:\s*(?:days?|nights?))?", answer.lower())
            if duration:
                return OrchestratorCommand(
                    intent=Intent.SET_TRIP_DATES, extracted_slots=TripSlots(days=int(duration.group(1)))
                )
        if state.traveler_count is None and re.fullmatch(r"(?:[1-9]|1[0-9]|20)", answer):
            return OrchestratorCommand(
                intent=Intent.SET_TRAVELERS, extracted_slots=TripSlots(traveler_count=int(answer))
            )
        if state.total_budget is None and state.budget_amount is None:
            short_budget = self._short_budget(answer)
            budget = parse_currency(answer, state.destination) if short_budget else None
            if budget and budget.amount:
                return OrchestratorCommand(
                    intent=Intent.SET_BUDGET,
                    extracted_slots=TripSlots(
                        total_budget=budget.amount,
                        budget_amount=budget.amount,
                        currency=budget.currency,
                        budget_currency=budget.currency,
                        budget_source_text=budget.source_text,
                        budget_confidence=budget.confidence,
                    ),
                )
        if (state.total_budget or state.budget_amount) and not (state.currency or state.budget_currency):
            token = answer.casefold().strip().rstrip(".")
            currency = STRUCTURED_CURRENCY_ALIASES.get(token)
            if currency:
                return OrchestratorCommand(
                    intent=Intent.SET_BUDGET,
                    extracted_slots=TripSlots(
                        currency=currency, budget_currency=currency, budget_confidence=1.0, budget_source_text=answer
                    ),
                )
        core_trip_complete = bool(
            state.destination
            and state.origin
            and state.start_date
            and state.end_date
            and state.date_range_confirmed
            and state.traveler_count
            and state.total_budget
        )
        if core_trip_complete and not state.preferences and not state.subcategory_preferences and not state.must_visit:
            flexible = parse_preferences(message)
            preferences = self._preference_slots(flexible)
            if flexible:
                return OrchestratorCommand(
                    intent=Intent.ADD_PREFERENCE,
                    extracted_slots=TripSlots(preferences=preferences, flexible_preferences=flexible),
                )
        return None

    @staticmethod
    def _means_all_activities(answer: str) -> bool:
        normalized = re.sub(r"\s+", " ", answer.lower()).strip()
        return bool(
            re.fullmatch(
                r"(?:all|all of (?:them|those|the above)|all (?:activities|categories|options)|"
                r"everything|anything|any of them|a (?:bit|little) of everything)",
                normalized,
            )
        )

    @staticmethod
    def _short_budget(answer: str) -> tuple[float, str | None] | None:
        parsed = parse_currency(answer)
        if parsed.amount is None:
            return None
        remaining = answer.casefold().replace(parsed.source_text.casefold(), "", 1)
        remaining = re.sub(r"[^a-z]+", " ", remaining).strip()
        if remaining not in {
            "",
            "budget",
            "budget is",
            "budget of",
            "budget around",
            "around",
            "about",
            "under",
            "approximately",
        }:
            return None
        return parsed.amount, parsed.currency

    @staticmethod
    def _explicit_preferences(message: str, allow_custom: bool = False) -> list[PreferenceSlot]:
        """Map everyday activity wording to planner categories without relying on an LLM."""
        parsed = parse_preferences(message)
        if not allow_custom:
            parsed = [item for item in parsed if not item.is_custom]
        return OrchestratorParser._preference_slots(parsed)

    @staticmethod
    def _preference_slots(preferences: list[Preference]) -> list[PreferenceSlot]:
        slots: list[PreferenceSlot] = []
        for preference in preferences:
            category = preference.planner_category
            if not category:
                continue
            slots.append(
                PreferenceSlot(
                    category=category,
                    subcategory=preference.subcategories[0] if preference.subcategories else None,
                    score=round(preference.weight * 10),
                    remove=preference.weight == 0,
                )
            )
        return slots

    @staticmethod
    def _short_place(answer: str) -> str | None:
        raw = re.sub(r"^from\s+", "", answer.strip(), flags=re.IGNORECASE)
        raw = DESTINATION_PREFIX.sub("", raw).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", raw) or len(raw.split()) > 4:
            return None
        if any(
            token in raw.lower().split()
            for token in (
                "travel",
                "traveling",
                "travelling",
                "start",
                "budget",
                "people",
                "days",
                "want",
                "wanna",
                "go",
                "going",
                "with",
                "from",
                "for",
            )
        ):
            return None
        normalized = raw.lower().strip()
        if normalized in UNKNOWN_TEXT or normalized in {
            "not sure",
            "undecided",
            "anything",
            "wherever",
            "maybe",
            "later",
        }:
            return None
        if normalized in DESTINATIONS:
            return DESTINATIONS[normalized]
        close = get_close_matches(normalized, DESTINATIONS.keys(), n=1, cutoff=0.76)
        return DESTINATIONS[close[0]] if close else raw.title()

    @staticmethod
    def _short_date(answer: str, today: date | None = None) -> date | None:
        """Parse common concise travel dates, choosing the next occurrence when year is omitted."""
        reference = today or date.today()
        lowered = re.sub(r"[,.!?'’\"]+$", "", answer.lower().strip())
        if lowered == "today":
            return reference
        if lowered == "tomorrow":
            return reference + timedelta(days=1)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", lowered):
            try:
                return date.fromisoformat(lowered)
            except ValueError:
                return None

        numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?", lowered)
        month_first = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(20\d{2}))?", lowered)
        day_first = re.fullmatch(
            r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?:the\s+)?([a-z]+)(?:\s*,?\s*(20\d{2}))?", lowered
        )
        if numeric:
            month, day, year_text = int(numeric.group(1)), int(numeric.group(2)), numeric.group(3)
        elif month_first and month_first.group(1) in MONTHS:
            month, day, year_text = MONTHS[month_first.group(1)], int(month_first.group(2)), month_first.group(3)
        elif day_first and day_first.group(2) in MONTHS:
            month, day, year_text = MONTHS[day_first.group(2)], int(day_first.group(1)), day_first.group(3)
        else:
            return None
        year = int(year_text) if year_text else reference.year
        try:
            parsed = date(year, month, day)
            if not year_text and parsed < reference:
                parsed = date(year + 1, month, day)
            return parsed
        except ValueError:
            return None

    def _parse_with_openai(self, message: str, state: TripState) -> OrchestratorCommand:
        from backend.llm.providers import OpenAIProvider
        from backend.llm.service import structured

        return structured(
            OrchestratorCommand,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"Validated TripState: {state.model_dump_json()}"},
                {"role": "user", "content": message},
            ],
            purpose="constraint_extraction",
            provider=OpenAIProvider(),
        )

    def _parse_fallback(self, message: str, state: TripState | None = None) -> OrchestratorCommand:
        """Conservative local parser: extract only explicit facts and never infer provider data."""
        lowered = message.lower()
        slots = TripSlots()
        number_words = {
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "ten": "10",
        }
        lowered = re.sub(r"\b(" + "|".join(number_words) + r")\b", lambda m: number_words[m.group()], lowered)
        intent = self._intent(lowered)
        aliases = sorted(DESTINATIONS.items(), key=lambda item: -len(item[0]))
        for alias, canonical in aliases:
            if re.search(rf"(?:\bto|\bvisit|\bvisiting)\s+(?:the\s+)?{re.escape(alias)}\b", lowered):
                slots.destination = canonical
                break
        if not slots.destination:
            for alias, canonical in aliases:
                match = re.search(rf"\b{re.escape(alias)}\b", lowered)
                if match and not lowered[max(0, match.start() - 6) : match.start()].strip().endswith("from"):
                    slots.destination = canonical
                    break
        if not slots.destination:
            destination_patterns = (
                r"\b(?:go|travel|fly)(?:ing)?\s+(?:to\s+)?(?:the\s+)?(?!from\b)([a-z][a-z .'-]{1,40}?)(?=\s+(?:from|for|with|on|starting|excluding|including)\b|[,.;]|$)",
                r"\bplan\s+(?:a\s+trip\s+to\s+)?(?:the\s+)?([a-z][a-z .'-]{1,40}?)(?=\s+(?:from|for|with|on|starting|excluding|including)\b|[,.;]|$)",
                r"\b(?:visit|visiting)\s+(?:the\s+)?([a-z][a-z .'-]{1,40}?)(?=\s+(?:from|for|with|on|starting|excluding|including)\b|[,.;]|$)",
            )
            for pattern in destination_patterns:
                match = re.search(pattern, lowered)
                if match and (parsed_destination := self._short_place(match.group(1))):
                    slots.destination = parsed_destination
                    break
        origin = re.search(
            r"\bfrom\s+([a-z][a-z .'-]{1,40}?)(?=\s+(?:to|from|for|with|on|starting|departing|returning|budget)\b|[,.;]|$)",
            lowered,
        )
        if origin:
            raw_origin = origin.group(1).strip().title()
            slots.origin = DESTINATIONS.get(raw_origin.lower(), raw_origin)
        days = re.search(r"\b(\d{1,2})\s*(?:day|days|night|nights)\b", lowered)
        if days:
            slots.days = int(days.group(1))
        travelers = re.search(r"\b(\d{1,2})\s*(?:people|persons|travelers|travellers|of us)\b", lowered)
        if travelers:
            slots.traveler_count = int(travelers.group(1))
        elif friends := re.search(r"\b(?:i\s+.*?with|me\s+and)\s+(\d{1,2})\s+friends?\b", lowered):
            slots.traveler_count = int(friends.group(1)) + 1
        elif re.search(r"\b(?:solo|alone|just me)\b", lowered):
            slots.traveler_count = 1
        iso_dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
        if iso_dates:
            slots.start_date = date.fromisoformat(iso_dates[0])
            if len(iso_dates) > 1:
                slots.end_date = date.fromisoformat(iso_dates[1])
        if not slots.start_date:
            natural_start = re.search(
                r"\b(?:start|starting|leave|leaving)(?:\s+on)?\s+"
                r"((?:[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*20\d{2})?)|"
                r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:the\s+)?[a-z]+(?:\s*,?\s*20\d{2})?))",
                lowered,
            )
            if natural_start:
                slots.start_date = self._short_date(natural_start.group(1))
        budget = parse_currency(message, state.destination if state else None)
        if budget.amount:
            slots.total_budget = slots.budget_amount = budget.amount
            slots.currency = slots.budget_currency = budget.currency
            slots.budget_source_text = budget.source_text
            slots.budget_confidence = budget.confidence
        if "excluding flight" in lowered or "without flight" in lowered or "flights not included" in lowered:
            slots.flight_included = False
        elif "including flight" in lowered or "flights included" in lowered:
            slots.flight_included = True
        if "relaxed" in lowered or "less busy" in lowered:
            slots.pace = "relaxed"
        elif "packed" in lowered:
            slots.pace = "packed"
        if re.search(r"(?:not|n't|less|minimal|limited|avoid|little).*?walk|not too much walking", lowered):
            slots.walking_limit = 30
            slots.accessibility_requirements.append("minimal_walking")
        if "wheelchair" in lowered:
            slots.accessibility_requirements.append("wheelchair_accessible")
        for diet in ("vegan", "vegetarian", "halal", "kosher", "gluten-free", "dairy-free"):
            if re.search(rf"\b{diet}\b", lowered):
                slots.dietary_requirements.append(diet)
        for style in ("budget", "midrange", "luxury", "premium"):
            if re.search(rf"\b{style}\s+(?:hotel|accommodation|lodging)", lowered):
                slots.hotel_style = style
        flexible = [item for item in parse_preferences(message) if not item.is_custom]
        slots.flexible_preferences = flexible
        slots.preferences = self._preference_slots(flexible)
        if slots.preferences and intent == Intent.UNKNOWN:
            intent = (
                Intent.REMOVE_PREFERENCE if all(item.remove for item in slots.preferences) else Intent.ADD_PREFERENCE
            )
        return OrchestratorCommand(intent=intent, extracted_slots=slots)

    @staticmethod
    def _intent(message: str) -> Intent:
        if (
            re.match(r"^\s*plan\s+(?!to\b)", message)
            or re.search(r"\bplan (?:me )?a\b.*\btrip\b", message)
            or any(
                phrase in message
                for phrase in (
                    "create a trip",
                    "plan a trip",
                    "plan me a trip",
                    "want to go",
                    "want to visit",
                    "planning a trip",
                )
            )
        ):
            return Intent.CREATE_TRIP
        if "generate" in message and "plan" in message:
            return Intent.GENERATE_PLANS
        if "select" in message and "plan" in message:
            return Intent.SELECT_PLAN
        if "vote" in message:
            return Intent.VOTE_PLAN
        if "unlock" in message:
            return Intent.UNLOCK_ACTIVITY
        if "lock" in message or message.startswith("keep "):
            return Intent.LOCK_ACTIVITY
        if "replace" in message or "too expensive" in message:
            return Intent.REPLACE_ACTIVITY
        if "remove" in message:
            return Intent.REMOVE_ACTIVITY
        if "move" in message:
            return Intent.MOVE_ACTIVITY
        if "budget" in message or "spend" in message:
            return Intent.CHANGE_BUDGET
        if any(category in message for category in PREFERENCE_CATEGORIES):
            return Intent.ADD_PREFERENCE
        return Intent.UNKNOWN


def local_llm_status() -> dict[str, str | bool]:
    """Report local inference readiness without sending a prompt or pulling a model."""
    configured = settings.orchestrator_provider.strip().lower() in {"ollama", "auto"}
    status: dict[str, str | bool] = {
        "provider": "ollama",
        "configured": configured,
        "available": False,
        "model": settings.ollama_model,
        "privacy": "local_device",
        "requires_api_key": False,
    }
    if not configured:
        status["detail"] = "Local inference is not the configured provider."
        return status
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2.0)
        response.raise_for_status()
        names = {model.get("name") for model in response.json().get("models", [])}
        names.update(model.get("model") for model in response.json().get("models", []))
        status["available"] = settings.ollama_model in names
        status["detail"] = (
            "Local model is ready."
            if status["available"]
            else "Ollama is running; the configured model is not downloaded."
        )
    except Exception:
        status["detail"] = "Ollama is not running; deterministic parsing remains available."
    return status
