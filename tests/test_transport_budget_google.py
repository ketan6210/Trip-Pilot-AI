from types import SimpleNamespace

from pydantic import SecretStr

from backend.providers.google_places import enrich_from_google
from backend.settings import settings
from backend.transport import choose_transport_leg
from tests.test_detail import create_selected_plan


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_budget_hint_is_currency_labeled_explainable_and_reconciled(client):
    response = client.post(
        "/budget/minimum-estimate",
        json={
            "destination": "New York City",
            "origin": "Chicago",
            "start_date": "2026-09-10",
            "end_date": "2026-09-12",
            "traveler_count": 3,
            "currency": "USD",
            "hotel_style": "budget",
            "flight_included": True,
        },
    )
    assert response.status_code == 200
    estimate = response.json()
    assert estimate["status"] == "ROUGH_PLANNING_ESTIMATE"
    assert 1800 < estimate["minimum_budget"] < 3000
    assert estimate["minimum_budget"] == round(sum(row["amount"] for row in estimate["components"]), 2)
    assert estimate["minimum_per_person"] == round(estimate["minimum_budget"] / 3, 2)
    assert estimate["flight_is_live"] is False
    assert any("not a live fare" in assumption.lower() for assumption in estimate["assumptions"])


def test_mixed_transport_chooses_cheapest_whole_group_total(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", None)
    first = SimpleNamespace(name="Museum", latitude=40.7128, longitude=-74.0060)
    second = SimpleNamespace(name="Park", latitude=40.7178, longitude=-74.0060)
    trip = SimpleNamespace(destination_city="New York City", currency="USD", traveler_count=5, transport_mode="mixed")
    result = choose_transport_leg(first, second, trip)
    totals = {option["mode"]: option["group_cost"] for option in result["options"]}
    assert result["selected_group_cost"] == min(totals.values())
    assert result["traveler_count"] == 5
    assert "whole-group" not in result["selection_reason"].lower() or "5 travelers" in result["selection_reason"]
    transit = next(option for option in result["options"] if option["mode"] == "public transit")
    assert transit["group_cost"] == round(transit["cost_per_person"] * 5, 2)
    assert all(option["source_quality"] for option in result["options"])


def test_detail_persists_every_group_transport_comparison(client):
    _, plan = create_selected_plan(client)
    detail = client.post(f"/plans/{plan['id']}/generate-detail").json()
    compared_legs = []
    for day in detail["days"]:
        assert day["timeline"][0].get("transport_details") is None
        for item in day["timeline"][1:]:
            comparison = item["transport_details"]
            compared_legs.append(comparison)
            assert comparison["selected_group_cost"] == min(option["group_cost"] for option in comparison["options"])
            assert comparison["traveler_count"] == detail["trip"]["traveler_count"]
    breakdown = client.get(f"/plans/{plan['id']}/expenses/transport").json()
    lines = [item for day in breakdown["days"] for item in day["items"]]
    assert len(lines) == len(compared_legs)
    assert all(len(line["metadata"]["options"]) >= 2 for line in lines)
    assert round(sum(line["total_expected"] for line in lines), 2) == breakdown["expected"]


def test_google_places_enrichment_uses_verified_rating_and_phone(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", SecretStr("test-key"))
    monkeypatch.setattr(
        "backend.providers.google_places.httpx.post",
        lambda *args, **kwargs: Response(
            {
                "places": [
                    {
                        "id": "ChIJ-verified",
                        "displayName": {"text": "Example Museum"},
                        "formattedAddress": "1 Main St, New York, NY",
                        "location": {"latitude": 40.71281, "longitude": -74.00601},
                        "rating": 4.7,
                        "userRatingCount": 1234,
                        "internationalPhoneNumber": "+1 212-555-0100",
                        "googleMapsUri": "https://maps.google.com/?cid=123",
                    }
                ]
            }
        ),
    )
    place = SimpleNamespace(
        name="Example Museum",
        address="1 Main St, New York, NY",
        city="New York City",
        latitude=40.7128,
        longitude=-74.0060,
        source_metadata={},
        rating=0.0,
        review_count=0,
        rating_source=None,
        phone_number=None,
        phone_source_url=None,
        google_place_id=None,
        maps_url="https://www.google.com/maps/search/?api=1",
    )
    assert enrich_from_google(place) is True
    assert place.rating == 4.7
    assert place.review_count == 1234
    assert place.rating_source == "Google Places"
    assert place.phone_number == "+1 212-555-0100"
    assert place.google_place_id == "ChIJ-verified"
    assert place.maps_url == "https://maps.google.com/?cid=123"
