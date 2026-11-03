from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.currency.converter import CurrencyService
from backend.db import SessionLocal
from backend.models import ItineraryActivity, ItineraryDay, PlanVariant, Trip
from backend.optimization.route_optimizer import validate_geography
from backend.providers.http import ProviderError, request_with_retry
from tests.test_phase1_security import create_trip, trip_payload


@pytest.mark.parametrize(
    "text,amount,currency",
    [
        ("2000 rupees", "2000", "INR"),
        ("INR 70,000", "70000", "INR"),
        ("€2500", "2500", "EUR"),
        ("budget is 2000", "2000", None),
    ],
)
def test_normalize_currency_without_guessing(client, text, amount, currency):
    response = client.post("/api/v1/trips/normalize", json={"text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["request"]["budget_amount"]) == Decimal(amount)
    assert body["request"]["currency"] == currency
    if currency is None:
        assert "currency" in body["missing_fields"]


def test_example_normalizes_friends_mobility_diet_and_only_asks_missing(client):
    response = client.post(
        "/api/v1/trips/normalize",
        json={
            "text": "I want to go to NYC with 4 friends for 3 days. Budget is 70,000 INR total. We like nightlife, museums, parks and good food. We don't want too much walking. We are vegetarian."
        },
    )
    body = response.json()
    assert body["status"] == "needs_input"
    assert body["request"]["travelers"] == 5
    assert body["request"]["duration_days"] == 3
    assert body["request"]["nightlife_preference"] is True
    assert body["request"]["dietary_preferences"] == ["vegetarian"]
    assert "minimal_walking" in body["request"]["mobility_preferences"]
    assert body["missing_fields"] == ["origin", "start_date", "end_date"]
    completed = client.post(
        "/api/v1/trips/normalize", json={"text": "From Chicago starting 2027-06-01", "known": body["request"]}
    ).json()
    assert completed["status"] == "ready"
    assert completed["request"]["end_date"] == "2027-06-03"


def test_repeated_currency_clarification_never_invents_usd(client):
    session = client.post("/orchestrator/sessions").json()["id"]
    for text in (
        "Plan NYC from Chicago with 2 people from 2027-06-01 to 2027-06-03. budget is 2000. museums",
        "not sure",
        "still not sure",
    ):
        response = client.post(f"/orchestrator/sessions/{session}/messages", json={"message": text})
        assert response.status_code == 200
    body = response.json()
    assert body["state"]["currency"] is None
    assert "budget_currency" in body["command"]["missing_fields"]


@pytest.mark.parametrize(
    "change",
    [
        {"currency": "XYZ"},
        {"budget_amount": "-1"},
        {"travelers": 0},
        {"end_date": "2027-05-01"},
        {"end_date": "2027-08-01"},
        {"budget_amount": "NaN"},
        {"origin": "  "},
    ],
)
def test_strict_trip_validation(client, change):
    response = client.post("/api/v1/trips", json={**trip_payload(), **change})
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"VALIDATION_ERROR", "INVALID_TRIP_INPUT"}


def test_missing_currency_and_invalid_partial_dates(client):
    payload = trip_payload()
    payload.pop("currency")
    response = client.post("/api/v1/trips", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["missing_fields"] == ["currency"]
    trip = create_trip(client)
    for change in ({"end_date": "2026-01-01"}, {"currency": None}, {"end_date": "2028-01-01"}):
        assert client.patch(f"/api/v1/trips/{trip['id']}", json=change).status_code == 422


def test_itinerary_persistence_budget_and_selection(client):
    trip = create_trip(client)
    response = client.post(f"/api/v1/trips/{trip['id']}/generate")
    assert response.status_code == 200, response.text
    plans = response.json()
    assert len(plans) == 4
    assert len({(p["activity_count"], p["budget"]["estimated_total"]) for p in plans}) == 4
    assert len({(p["activity_count"], p["budget"]["estimated_total"], p["strategy"]) for p in plans}) == 4
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ItineraryDay)) == 12
        assert db.scalar(select(func.count()).select_from(ItineraryActivity)) > 0
    for plan in plans:
        budget = plan["budget"]
        total = Decimal(budget["estimated_total"])
        assert sum(map(Decimal, budget["category_breakdown"].values())) == total
        assert Decimal(budget["remaining_budget"]) == Decimal("5000") - total
        assert all(
            a["duration_minutes"] > 0 and a["reason_for_selection"] for day in plan["days"] for a in day["activities"]
        )
        loaded = client.get(f"/api/v1/itineraries/{plan['id']}")
        assert loaded.json()["days"] == plan["days"]
    for plan in plans[:2]:
        assert client.post(f"/api/v1/itineraries/{plan['id']}/select").status_code == 200
    current = client.get(f"/api/v1/trips/{trip['id']}/itineraries").json()
    assert sum(p["status"] == "selected" for p in current) == 1
    updated = client.patch(f"/api/v1/itineraries/{plans[0]['id']}", json={"title": "Our city weekend"})
    assert updated.json()["title"] == "Our city weekend"
    client.patch(f"/api/v1/trips/{trip['id']}", json={"total_budget": 7000})
    assert client.post(f"/api/v1/itineraries/{plans[0]['id']}/select").status_code == 409


def test_solo_trip_and_database_constraints(client):
    payload = {**trip_payload(), "travelers": 1}
    trip = client.post("/api/v1/trips", json=payload).json()
    assert client.post(f"/api/v1/trips/{trip['id']}/generate").status_code == 200
    with SessionLocal() as db:
        row = db.get(Trip, trip["id"])
        row.traveler_count = 0
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_provider_failure_preserves_previous_plans(client, monkeypatch):
    trip = create_trip(client)
    assert client.post(f"/api/v1/trips/{trip['id']}/generate").status_code == 200

    def fail(*args):
        raise ProviderError("Places", "PROVIDER_TEMPORARY_FAILURE", True)

    monkeypatch.setattr("backend.planning.get_place_provider", fail)
    failed = client.post(f"/api/v1/trips/{trip['id']}/generate")
    assert failed.status_code == 503
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PlanVariant).where(PlanVariant.status != "stale")) == 4


@pytest.mark.parametrize(
    "status,expected_calls,code",
    [
        (503, 3, "PROVIDER_TEMPORARY_FAILURE"),
        (429, 3, "PROVIDER_QUOTA_EXCEEDED"),
        (400, 1, "PROVIDER_INVALID_INPUT"),
        (403, 1, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_retries_are_bounded(status, expected_calls, code, monkeypatch):
    monkeypatch.setattr("backend.providers.http.time.sleep", lambda _: None)
    calls = []

    def request():
        calls.append(1)
        return httpx.Response(status, request=httpx.Request("GET", "https://provider.example"))

    with pytest.raises(ProviderError) as caught:
        request_with_retry(request, "Example")
    assert len(calls) == expected_calls
    assert caught.value.code == code


def test_expired_fx_is_not_silently_reused(monkeypatch):
    service = CurrencyService(ttl=timedelta(seconds=1))
    service._cache[("USD", "INR")] = (80, datetime.now(UTC) - timedelta(days=1))

    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("backend.currency.converter.httpx.get", fail)
    monkeypatch.setattr("backend.providers.http.time.sleep", lambda _: None)
    assert service.convert(10, "USD", "INR") is None


def test_rejects_geographically_impossible_city_route():
    with pytest.raises(ValueError, match="too far apart"):
        validate_geography(
            [SimpleNamespace(latitude=40.7, longitude=-74), SimpleNamespace(latitude=51.5, longitude=-0.1)]
        )


def test_llm_cannot_fill_unstated_critical_fields(monkeypatch):
    from datetime import date

    from backend.orchestration.parser import OrchestratorParser
    from backend.orchestration.schemas import OrchestratorCommand, TripSlots, TripState
    from backend.settings import settings

    model = OrchestratorCommand(
        extracted_slots=TripSlots(
            destination="New York City",
            origin="Chicago",
            start_date=date(2027, 6, 1),
            end_date=date(2027, 6, 3),
            traveler_count=2,
            total_budget=2000,
            budget_amount=2000,
            currency="USD",
            budget_currency="USD",
        )
    )
    monkeypatch.setattr(settings, "orchestrator_provider", "ollama")
    monkeypatch.setattr(OrchestratorParser, "_parse_with_ollama", lambda *args: model)
    command, _ = OrchestratorParser().parse("I want to visit NYC", TripState())
    assert command.extracted_slots.destination == "New York City"
    assert all(
        getattr(command.extracted_slots, key) is None
        for key in (
            "origin",
            "start_date",
            "end_date",
            "traveler_count",
            "total_budget",
            "budget_amount",
            "currency",
            "budget_currency",
        )
    )
