from datetime import date
from types import SimpleNamespace

import pytest

from backend.itinerary.duration_model import estimate_duration
from backend.naming.plan_name_generator import generate_plan_names
from backend.optimization.schedule_optimizer import optimize_day
from backend.parsing.currency_parser import parse_currency
from backend.parsing.preference_parser import parse_preferences
from backend.validation.itinerary_validator import validate_itinerary


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("2000 rupees", 2000, "INR"),
        ("₹3000", 3000, "INR"),
        ("$3000", 3000, "USD"),
        ("1500 pounds", 1500, "GBP"),
        ("€1800", 1800, "EUR"),
        ("2k usd", 2000, "USD"),
    ],
)
def test_currency_parser_preserves_explicit_currency(text, amount, currency):
    parsed = parse_currency(text, "Delhi")
    assert parsed.amount == amount
    assert parsed.currency == currency
    assert parsed.explicit is True
    assert parsed.needs_clarification is False


def test_numeric_only_budget_requires_currency_clarification():
    parsed = parse_currency("2000", "Delhi")
    assert parsed.amount == 2000
    assert parsed.currency is None
    assert parsed.needs_clarification is True
    assert parsed.alternatives[0] == "INR"


def test_all_preferences_and_emphasis_are_retained():
    parsed = parse_preferences("I am open to everything but mostly nightlife")
    weights = {item.canonical_category: item.weight for item in parsed}
    assert weights["nightlife"] == 1.0
    assert weights["nature"] == 0.7
    assert weights["museum"] == 0.7


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("more greenery and quiet scenic outdoor places", {"nature", "outdoor"}),
        ("I want to experience local life and local markets", {"local_experience"}),
        ("parks and hiking", {"nature"}),
    ],
)
def test_flexible_preference_mapping(text, expected):
    categories = {item.canonical_category for item in parse_preferences(text)}
    assert categories & expected


def test_unknown_preference_is_saved_as_custom():
    parsed = parse_preferences("weird underground projection spaces")
    assert parsed
    assert parsed[0].is_custom is True
    assert parsed[0].raw_text == "weird underground projection spaces"


def test_duration_priors_are_realistic_and_distinct():
    museum = estimate_duration("museums", "art")
    hike = estimate_duration("nature", "hiking")
    nightlife = estimate_duration("nightlife", "nightclub")
    restaurant = estimate_duration("food", "restaurant")
    assert museum.expected == 150
    assert hike.expected == 300
    assert nightlife.expected >= 180
    assert restaurant.expected == 90
    assert len({museum.expected, hike.expected, nightlife.expected, restaurant.expected}) == 4


def _place(identifier, category, subcategory="general", hours=None, latitude=40.0, longitude=-73.0):
    return SimpleNamespace(
        id=identifier,
        category=category,
        subcategory=subcategory,
        name=f"Place {identifier}",
        tags=[],
        latitude=latitude,
        longitude=longitude,
        opening_hours={"raw": hours} if hours else {},
    )


def test_scheduler_uses_category_times_durations_and_route_gaps():
    places = [
        _place(1, "museums", "art", latitude=40.000),
        _place(2, "food", "restaurant", latitude=40.006),
        _place(3, "nightlife", "nightclub", latitude=40.012),
    ]
    schedule, departure, finish = optimize_day(
        places,
        trip_date=date(2026, 9, 10),
        profile_key="experience_heavy",
        pace="packed",
        transport_mode="public transit",
        interests={"museums": 0.8, "nightlife": 1.0},
    )
    assert departure == 7 * 60 + 45
    assert schedule[0].start_minute >= 9 * 60
    assert schedule[-1].start_minute >= 18 * 60
    for previous, current in zip(schedule, schedule[1:]):
        assert current.start_minute >= previous.end_minute + current.travel_minutes + current.break_minutes
    for item in schedule:
        if item.place.category == "food":
            assert 8 * 60 <= item.start_minute <= 15 * 60 or 17 * 60 <= item.start_minute <= 22 * 60
    assert finish == schedule[-1].end_minute


def test_scheduler_marks_provider_hours_conflict():
    schedule, _, _ = optimize_day(
        [_place(1, "museums", "art", "09:00-10:00")],
        trip_date=date(2026, 9, 10),
        profile_key="balanced",
        pace="balanced",
        transport_mode="walking",
        interests={},
    )
    assert schedule[0].opening_feasible is False


def test_validator_rejects_overlap_budget_and_missing_lock():
    item = {
        "place_id": 1,
        "name": "A",
        "address": "Street",
        "neighborhood": "Area",
        "start_minute": 600,
        "end_minute": 690,
        "duration_min": 60,
        "duration_minutes": 90,
        "duration_max": 120,
        "travel_minutes_from_previous": 0,
        "break_minutes_after_previous": 0,
        "opening_feasible": True,
        "category": "museums",
        "subcategory": "art",
        "maps_url": "https://maps.google.com/a",
    }
    overlapping = {**item, "place_id": 2, "name": "B", "start_minute": 650, "end_minute": 740}
    result = validate_itinerary(
        [{"items": [item, overlapping]}], expected_budget=120, target_budget=100, locked_items=[3]
    )
    assert result["valid"] is False
    assert result["checks"]["no_overlaps"] is False
    assert result["checks"]["hard_budget"] is False
    assert result["checks"]["locked_items_preserved"] is False


def test_dynamic_names_change_with_destination():
    nyc = generate_plan_names(
        "New York City", ["nightlife"], {key: [] for key in ("balanced", "experience_heavy", "budget", "relaxed")}
    )
    delhi = generate_plan_names(
        "Delhi", ["history"], {key: [] for key in ("balanced", "experience_heavy", "budget", "relaxed")}
    )
    assert nyc["balanced"].title != delhi["balanced"].title
    assert "New York" in nyc["balanced"].title
    assert "Delhi" in delhi["balanced"].title


def _message(client, session_id, text):
    response = client.post(f"/orchestrator/sessions/{session_id}/messages", json={"message": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_delhi_conversation_preserves_inr_and_does_not_loop(client):
    session_id = client.post("/orchestrator/sessions").json()["id"]
    first = _message(
        client,
        session_id,
        "I want to go to Delhi for 3 days with 4 people. Budget around 20000 rupees. "
        "I want nature, local markets and history.",
    )
    assert first["state"]["destination"] == "Delhi"
    assert first["state"]["traveler_count"] == 4
    assert first["state"]["budget_amount"] == 20000
    assert first["state"]["budget_currency"] == "INR"
    assert "nature" in first["state"]["broad_preferences"]
    assert "local_experience" in first["state"]["broad_preferences"]
    second = _message(client, session_id, "from Newark")
    assert "start_date" in second["command"]["missing_fields"]
    final = _message(client, session_id, "2026-09-10")
    assert final["ready"] is False
    confirmed = _message(client, session_id, "2026-09-12")
    assert confirmed["ready"] is True
    assert confirmed["command"]["followup_question"] is None


def test_all_of_them_conversation_weights_nightlife_higher(client):
    session_id = client.post("/orchestrator/sessions").json()["id"]
    response = _message(
        client,
        session_id,
        "I want to go to New York City from Chicago from 2026-09-10 to 2026-09-13 with 4 people. "
        "Budget $3000. I want all of them but more nightlife.",
    )
    assert response["ready"] is True
    broad = response["state"]["broad_preferences"]
    assert broad["nightlife"] == 1.0
    assert broad["nature"] == 0.7


def test_custom_preference_completes_conversation_without_repeat(client):
    session_id = client.post("/orchestrator/sessions").json()["id"]
    initial = _message(
        client,
        session_id,
        "Plan a trip to Boston from Chicago for 3 days with 2 people, starting September 10 2026. Budget $2500.",
    )
    assert initial["command"]["missing_fields"] == ["end_date", "preferences"]
    dates = _message(client, session_id, "2026-09-12")
    assert dates["command"]["missing_fields"] == ["preferences"]
    result = _message(client, session_id, "weird underground projection spaces")
    assert result["ready"] is True
    assert result["state"]["custom_preferences"][0]["is_custom"] is True
