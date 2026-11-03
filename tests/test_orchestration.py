from datetime import date

import pytest
from pydantic import ValidationError

from backend.orchestration.parser import PLANNABLE_ACTIVITY_CATEGORIES, OrchestratorParser
from backend.orchestration.schemas import Intent, OrchestratorCommand, TripSlots, TripState
from backend.settings import settings


def test_natural_language_updates_durable_state_and_asks_only_for_missing_fields(client):
    session = client.post("/orchestrator/sessions").json()
    first = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={
            "message": "I want to go to NYC for 4 days with 4 people. We want nightlife, museums, parks and food. Budget around $3,000 excluding flights."
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["parser_mode"] == "deterministic_only"
    assert body["command"]["intent"] == "CREATE_TRIP"
    assert body["state"]["destination"] == "New York City"
    assert body["state"]["days"] == 4
    assert body["state"]["traveler_count"] == 4
    assert body["state"]["total_budget"] == 3000
    assert body["state"]["flight_included"] is False
    assert all(body["state"]["preferences"][category] >= 8 for category in ("nightlife", "museums", "parks", "food"))
    assert body["command"]["missing_fields"] == ["origin", "start_date", "end_date"]
    assert body["assistant_message"] == "Where will you be traveling from?"

    second = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": "We're traveling from Chicago."}
    ).json()
    assert second["state"]["destination"] == "New York City"
    assert second["state"]["origin"] == "Chicago"
    assert second["command"]["missing_fields"] == ["start_date", "end_date"]
    assert second["assistant_message"] == "What date would you like to depart?"

    third = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": "Start on 2026-09-10."}
    ).json()
    assert third["ready"] is False
    assert third["state"]["end_date"] == "2026-09-13"
    assert third["command"]["missing_fields"] == ["end_date"]
    assert "please confirm or change it" in third["assistant_message"]
    fourth = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": "Return on 2026-09-13."}
    ).json()
    assert fourth["ready"] is True
    assert fourth["state"]["date_range_confirmed"] is True
    assert fourth["command"]["missing_fields"] == []
    stored = client.get(f"/orchestrator/sessions/{session['id']}/messages").json()
    assert len(stored) == 8
    assert stored[-1]["structured_command"]["requires_followup"] is False


def test_structured_slots_reject_invalid_group_size():
    try:
        TripSlots(traveler_count=25)
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid traveler_count was accepted")


def test_fallback_recognizes_edit_intents_without_fabricating_slots(client):
    session = client.post("/orchestrator/sessions").json()
    response = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "That club is too expensive. Find something cheaper nearby."},
    ).json()
    assert response["command"]["intent"] == Intent.REPLACE_ACTIVITY
    assert response["command"]["extracted_slots"]["total_budget"] is None
    assert response["state"]["destination"] is None


def test_local_llm_uses_schema_validated_command(monkeypatch):
    expected = OrchestratorCommand(
        intent=Intent.CREATE_TRIP,
        extracted_slots=TripSlots(destination="Boston", traveler_count=3, days=2, total_budget=900, currency="USD"),
    )

    class LocalResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": expected.model_dump_json()}}

    def local_post(url, json, timeout):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert json["model"] == "qwen3:1.7b"
        assert json["format"]["title"] == "OrchestratorCommand"
        assert json["stream"] is False
        assert timeout == settings.llm_timeout_seconds
        return LocalResponse()

    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")
    monkeypatch.setattr("backend.orchestration.parser.httpx.post", local_post)
    command, mode = OrchestratorParser().parse("Three of us want two days in Boston under $900", TripState())
    assert mode == "ollama_local"
    assert command == expected


def test_local_llm_unknown_placeholder_stays_missing(monkeypatch):
    model_command = OrchestratorCommand(
        intent=Intent.CREATE_TRIP,
        extracted_slots=TripSlots(
            destination="New York City", origin="unknown", days=4, traveler_count=2, total_budget=2000
        ),
    )
    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")
    monkeypatch.setattr(OrchestratorParser, "_parse_with_ollama", lambda *args: model_command)
    command, _ = OrchestratorParser().parse(
        "I want to go to NYC for 4 days with 2 people and a $2,000 budget", TripState()
    )
    assert command.extracted_slots.destination == "New York City"
    assert command.extracted_slots.origin is None


def test_local_llm_does_not_turn_budget_number_into_traveler_count(monkeypatch):
    model_command = OrchestratorCommand(
        intent=Intent.CREATE_TRIP,
        extracted_slots=TripSlots(destination="New York City", traveler_count=2, days=3, total_budget=2400),
    )
    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")
    monkeypatch.setattr(OrchestratorParser, "_parse_with_ollama", lambda *args: model_command)
    command, _ = OrchestratorParser().parse("Plan NYC for 3 days with a $2,400 budget", TripState())
    assert command.extracted_slots.traveler_count is None
    assert command.extracted_slots.total_budget == 2400


def test_stopped_local_llm_keeps_deterministic_parser_available(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")

    def unavailable(*args):
        raise ConnectionError

    monkeypatch.setattr(OrchestratorParser, "_parse_with_ollama", unavailable)
    command, mode = OrchestratorParser().parse("We want to visit Boston for 3 days with 2 people", TripState())
    assert mode == "fallback_local_unavailable"
    assert command.extracted_slots.destination == "Boston"


def test_bare_place_answers_origin_followup_and_preserves_destination(client):
    for answer, expected_origin in (("chicago", "Chicago"), ("chicaigo", "Chicago"), ("london", "London")):
        session = client.post("/orchestrator/sessions").json()
        first = client.post(
            f"/orchestrator/sessions/{session['id']}/messages",
            json={"message": "I want to go to NYC for 4 days with 2 people. Budget around $2,000 including flights."},
        ).json()
        assert first["assistant_message"] == "Where will you be traveling from?"

        second = client.post(
            f"/orchestrator/sessions/{session['id']}/messages",
            json={"message": answer},
        ).json()
        assert second["parser_mode"] == "contextual_followup"
        assert second["state"]["destination"] == "New York City"
        assert second["state"]["origin"] == expected_origin
        assert second["assistant_message"] == "What date would you like to depart?"


def test_natural_date_answer_advances_instead_of_repeating_question(client):
    session = client.post("/orchestrator/sessions").json()
    client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "I want to go to NYC for 4 days with 2 people. Budget around $2,000 including flights."},
    )
    client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "Chicago"})
    response = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": "26 of the august"}
    ).json()
    expected_year = date.today().year if date.today() <= date(date.today().year, 8, 26) else date.today().year + 1
    assert response["parser_mode"] == "contextual_followup"
    assert response["state"]["start_date"] == f"{expected_year}-08-26"
    assert response["state"]["end_date"] == f"{expected_year}-08-29"
    assert response["ready"] is False
    assert response["assistant_message"].startswith("What date would you like to return")

    confirmed = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": f"August 29 {expected_year}"}
    ).json()
    assert confirmed["state"]["date_range_confirmed"] is True
    assert confirmed["assistant_message"].startswith("What activities or places")

    interests = client.post(
        f"/orchestrator/sessions/{session['id']}/messages", json={"message": "museums and food"}
    ).json()
    assert interests["ready"] is True
    assert interests["state"]["preferences"] == {"museums": 8, "food": 8}
    assert "Generate four plans" in interests["assistant_message"]

    corrected = client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "August 27"}).json()
    assert corrected["command"]["intent"] == "CHANGE_TRIP_DATES"
    assert corrected["state"]["start_date"] == f"{expected_year}-08-27"
    assert corrected["state"]["end_date"] == f"{expected_year}-08-30"
    assert corrected["state"]["days"] == 4
    assert corrected["state"]["date_range_confirmed"] is False


def test_common_natural_date_formats():
    parser = OrchestratorParser()
    reference = date(2026, 8, 21)
    assert parser._short_date("August 26", reference) == date(2026, 8, 26)
    assert parser._short_date("26th August 2027", reference) == date(2027, 8, 26)
    assert parser._short_date("08/26/2026", reference) == date(2026, 8, 26)
    assert parser._short_date("August 20", reference) == date(2027, 8, 20)


def test_fallback_accepts_arbitrary_city_and_plain_language_budget():
    command = OrchestratorParser()._parse_fallback(
        "Plan London from Chicago for 4 days starting September 10 2026 for 4 people "
        "with a 3000 dollar budget excluding flights. We like museums, landmarks, pubs, and parks."
    )
    slots = command.extracted_slots
    assert command.intent == Intent.CREATE_TRIP
    assert slots.destination == "London"
    assert slots.origin == "Chicago"
    assert slots.start_date == date(2026, 9, 10)
    assert slots.days == 4
    assert slots.traveler_count == 4
    assert slots.total_budget == 3000
    assert slots.flight_included is False


def test_chat_collects_group_size_and_interests_then_generates_four_plans(client):
    session = client.post("/orchestrator/sessions").json()
    first = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={
            "message": "I want to go to NYC from Chicago for 3 days starting 2026-09-10. Budget around $2,400 including flights."
        },
    ).json()
    assert first["ready"] is False
    assert first["assistant_message"].startswith("What date would you like to return")

    dates = client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "2026-09-12"}).json()
    assert dates["assistant_message"] == "How many people are traveling?"

    group = client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "3"}).json()
    assert group["state"]["traveler_count"] == 3
    assert group["assistant_message"].startswith("What activities or places")

    interests = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "Museums, parks, and local food"},
    ).json()
    assert interests["ready"] is True
    assert set(interests["state"]["preferences"]) == {"museums", "parks", "food"}

    generated = client.post(f"/orchestrator/sessions/{session['id']}/generate-plans")
    assert generated.status_code == 200
    body = generated.json()
    assert body["trip"]["traveler_count"] == 3
    assert len(body["travelers"]) == 3
    assert {plan["plan_type"] for plan in body["plans"]} == {"balanced", "experience_heavy", "budget", "relaxed"}
    stored = client.get(f"/orchestrator/sessions/{session['id']}").json()
    assert stored["status"] == "plans_ready"
    assert stored["state"]["trip_id"] == body["trip"]["id"]

    repeated = client.post(f"/orchestrator/sessions/{session['id']}/generate-plans")
    assert repeated.status_code == 200
    assert repeated.json()["trip"]["id"] == body["trip"]["id"]


def test_everyday_activity_words_complete_the_preferences_step(client):
    session = client.post("/orchestrator/sessions").json()
    first = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "Plan NYC from Chicago for 4 days starting 2026-09-10 for 4 people with a $3,000 budget."},
    ).json()
    assert first["assistant_message"].startswith("What date would you like to return")

    client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "2026-09-13"})

    response = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "clubs and pubs"},
    ).json()
    assert response["parser_mode"] == "contextual_followup"
    assert response["state"]["preferences"] == {"nightlife": 8}
    assert response["ready"] is True
    assert "Generate four plans" in response["assistant_message"]


def test_all_of_them_selects_every_activity_and_does_not_repeat(client):
    session = client.post("/orchestrator/sessions").json()
    prompts = (
        "I want to go to Mumbai",
        "NYC",
        "4 sept'",
        "6",
        "7",
        "6000 dollars",
        "9 september",
    )
    response = None
    for message in prompts:
        response = client.post(
            f"/orchestrator/sessions/{session['id']}/messages",
            json={"message": message},
        ).json()

    assert response["state"]["destination"] == "Mumbai"
    assert response["state"]["origin"] == "New York City"
    assert response["state"]["start_date"].endswith("-09-04")
    assert response["state"]["days"] == 6
    assert response["state"]["traveler_count"] == 7
    assert response["state"]["total_budget"] == 6000
    assert response["assistant_message"].startswith("What activities or places")

    all_activities = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "all of them"},
    ).json()
    assert all_activities["parser_mode"] == "contextual_followup"
    assert set(all_activities["state"]["preferences"]) == set(PLANNABLE_ACTIVITY_CATEGORIES)
    assert all_activities["state"]["destination"] == "Mumbai"
    assert all_activities["ready"] is True
    assert "Generate four plans" in all_activities["assistant_message"]


def test_contextual_budget_understands_rupees_and_activity_typos():
    parser = OrchestratorParser()
    state = TripState(
        destination="Chennai",
        origin="Delhi",
        start_date=date(2026, 8, 26),
        end_date=date(2026, 8, 28),
        date_range_confirmed=True,
        days=3,
        traveler_count=2,
    )
    budget = parser._parse_contextual_answer("20,000 rupees", state)
    assert budget.extracted_slots.total_budget == 20000
    assert budget.extracted_slots.currency == "INR"

    state.total_budget = 20000
    preferences = parser._parse_contextual_answer("histroic places and mesuem", state)
    assert {item.category for item in preferences.extracted_slots.preferences} == {"history", "museums"}


def test_question_gets_answer_without_becoming_a_preference(client, monkeypatch):
    monkeypatch.setattr(
        OrchestratorParser,
        "answer_question",
        lambda self, message, state, live_context=None: (
            "Clubs are usually dance-focused, while pubs are generally more casual places for drinks and conversation."
        ),
    )
    session = client.post("/orchestrator/sessions").json()
    client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "Plan NYC from Chicago for 4 days starting 2026-09-10 for 4 people with a $3,000 budget."},
    )
    client.post(f"/orchestrator/sessions/{session['id']}/messages", json={"message": "2026-09-13"})
    response = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "What is the difference between clubs and pubs?"},
    ).json()
    assert response["parser_mode"] == "conversation"
    assert response["state"]["preferences"] == {}
    assert response["ready"] is False
    assert response["assistant_message"].startswith("Clubs are usually dance-focused")
    assert "what activities or places" in response["assistant_message"].lower()


def test_insufficient_budget_reports_minimum_and_chat_budget_retry_updates_created_trip(client):
    session = client.post("/orchestrator/sessions").json()
    brief = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={
            "message": "Plan NYC from Chicago from 2026-09-10 to 2026-09-12 for 2 people with a $200 budget including flights. We like museums and parks."
        },
    ).json()
    assert brief["ready"] is True
    too_low = client.post(f"/orchestrator/sessions/{session['id']}/generate-plans")
    assert too_low.status_code == 422
    assert "Use at least USD" in too_low.json()["detail"]
    created_trip_id = client.get(f"/orchestrator/sessions/{session['id']}").json()["state"]["trip_id"]

    changed = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={"message": "Increase the total budget to $2,400."},
    ).json()
    assert changed["state"]["total_budget"] == 2400
    retried = client.post(f"/orchestrator/sessions/{session['id']}/generate-plans")
    assert retried.status_code == 200
    assert retried.json()["trip"]["id"] == created_trip_id
    assert retried.json()["trip"]["total_budget"] == 2400


def test_local_llm_question_is_grounded_with_live_provider_records(monkeypatch):
    captured = {}

    class LocalResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"place_indices": [0], "topic": "places"}'}}

    def local_post(url, json, timeout):
        captured.update(json)
        return LocalResponse()

    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")
    monkeypatch.setattr("backend.orchestration.parser.httpx.post", local_post)
    answer = OrchestratorParser().answer_question(
        "Which museums are free?",
        TripState(destination="London"),
        live_context=[
            {
                "name": "The British Museum",
                "category": "museums",
                "cost_status": "FREE",
                "cost": 0,
                "currency": "GBP",
                "ticket_url": "https://www.britishmuseum.org/visit",
                "provider": "openstreetmap",
            }
        ],
    )
    prompt = "\n".join(item["content"] for item in captured["messages"])
    assert "The British Museum" in prompt
    assert captured["format"]["title"] == "GroundedAnswer"
    assert answer.startswith("The British Museum")


@pytest.mark.parametrize(
    ("message", "destination"),
    [
        ("I wanna go chicago", "Chicago"),
        ("I want to go Chicago", "Chicago"),
        ("We wanna visit San Diego", "San Diego"),
        ("Going to Los Angeles", "Los Angeles"),
    ],
)
def test_conversational_destination_is_a_city_not_the_whole_sentence(message, destination):
    parser = OrchestratorParser()
    command, _ = parser.parse(message, TripState())
    assert command.extracted_slots.destination == destination
    assert command.extracted_slots.origin is None
    # An explicit destination correction must not become the missing origin.
    command, _ = parser.parse(message, TripState(destination="Boston"))
    assert command.extracted_slots.destination == destination
    assert command.extracted_slots.origin is None


def test_generation_repairs_saved_destination_prefix_and_reuses_trip(client):
    from backend.db import SessionLocal
    from backend.models import Trip, TripStateRecord

    session = client.post("/orchestrator/sessions").json()
    response = client.post(
        f"/orchestrator/sessions/{session['id']}/messages",
        json={
            "message": "Plan NYC from Chicago from 2027-06-01 to 2027-06-03 for 3 people with a $5000 budget. We like museums and parks."
        },
    )
    assert response.json()["ready"]
    path = f"/orchestrator/sessions/{session['id']}/generate-plans"
    initial = client.post(path)
    assert initial.status_code == 200, initial.text
    trip_id = initial.json()["trip"]["id"]
    with SessionLocal() as db:
        record = db.get(TripStateRecord, session["id"])
        record.state = {**record.state, "destination": "I Wanna Go NYC"}
        db.get(Trip, trip_id).destination_city = "I Wanna Go NYC"
        db.commit()
    retried = client.post(path)
    assert retried.status_code == 200, retried.text
    assert retried.json()["trip"]["id"] == trip_id
    assert retried.json()["trip"]["destination_city"] == "New York City"
    assert retried.json()["trip"]["total_budget"] == 5000
    assert len(retried.json()["travelers"]) == 3
    assert client.get(f"/orchestrator/sessions/{session['id']}").json()["state"]["destination"] == "New York City"
