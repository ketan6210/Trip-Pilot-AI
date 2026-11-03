from pydantic import ValidationError

from backend.pricing.models import PriceEstimate, PriceType


def _group(client):
    trip = client.post(
        "/trips",
        json={
            "destination_city": "New York City",
            "origin": "Delhi",
            "start_date": "2026-10-10",
            "end_date": "2026-10-12",
            "traveler_count": 2,
            "total_budget": 2400,
            "currency": "USD",
        },
    ).json()
    for index in range(2):
        traveler = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": f"Traveler {index + 1}"}).json()
        client.put(
            f"/travelers/{traveler['id']}/preferences",
            json={
                "preferences": [
                    {"category": "museums", "score": 9 - index},
                    {"category": "nightlife", "score": 6 + index},
                ]
            },
        )
    client.put(
        f"/trips/{trip['id']}/budget",
        json={
            "mode": "fixed",
            "categories": {
                "flight": 500,
                "hotel": 800,
                "food": 400,
                "nightlife": 200,
                "attraction": 350,
                "buffer": 100,
            },
        },
    )
    return trip, client.post(f"/trips/{trip['id']}/generate-plans").json()


def test_flight_search_preserves_route_dates_currency_and_group_size(client, monkeypatch):
    monkeypatch.setattr("backend.settings.settings.skyscanner_api_key", None)
    monkeypatch.setattr("backend.settings.settings.amadeus_client_id", None)
    monkeypatch.setattr("backend.settings.settings.amadeus_client_secret", None)
    response = client.post(
        "/flights/search",
        json={
            "origin": "Delhi",
            "destination": "Varanasi",
            "departure_date": "2026-11-01",
            "return_date": "2026-11-04",
            "adults": 3,
            "cabin": "economy",
            "currency": "INR",
        },
    )
    assert response.status_code == 200
    quote = response.json()
    assert quote["status"] == "SEARCH_REQUIRED"
    assert quote["total"] is None and quote["per_person"] is None
    assert quote["origin_code"] == "DEL" and quote["destination_code"] == "VNS"
    assert "/del/vns/261101/261104/" in quote["search_url"]
    assert "adultsv2=3" in quote["search_url"] and "currency=INR" in quote["search_url"]


def test_plan_ledgers_comparison_and_budget_action(client):
    trip, plans = _group(client)
    assert len({plan["summary"]["strategy"] for plan in plans}) == 4
    assert all(plan["estimated_total_cost"] <= trip["total_budget"] for plan in plans)
    for plan in plans:
        costs = client.get(f"/plans/{plan['id']}/costs")
        assert costs.status_code == 200
        body = costs.json()
        assert body["total"] == plan["estimated_total_cost"]
        assert {entry["price_type"] for entry in body["entries"]} <= {item.value for item in PriceType}
        assert all(not (entry["price_type"] == "UNKNOWN" and entry["expected"] == 0) for entry in body["entries"])
    comparison = client.post("/plans/compare", json={"plan_a_id": plans[0]["id"], "plan_b_id": plans[1]["id"]})
    assert comparison.status_code == 200
    compared = comparison.json()
    assert compared["total_cost_delta"] == round(plans[1]["estimated_total_cost"] - plans[0]["estimated_total_cost"], 2)
    assert isinstance(compared["shared_places"], list) and isinstance(compared["daily_differences"], list)
    assert compared["plan_a"]["display_name"] != compared["plan_b"]["display_name"]
    assert compared["plan_a"]["decision_tip"].startswith("Choose this")
    assert compared["verdict"]["savings"] == abs(compared["total_cost_delta"])
    common_category = next(
        iter(set(plans[0]["summary"]["cost_breakdown"]) & set(plans[1]["summary"]["cost_breakdown"]))
    )
    category = compared["category_costs"][common_category]
    assert category["plan_a"] == plans[0]["summary"]["cost_breakdown"][common_category]["expected"]
    assert category["plan_b"] == plans[1]["summary"]["cost_breakdown"][common_category]["expected"]
    assert category["label"] != "baseline"
    premium = next(plan for plan in plans if plan["summary"]["strategy"] == "premium_convenience")
    result = client.post(f"/plans/{premium['id']}/bring-under-budget")
    assert result.status_code == 200
    assert result.json()["target"] == trip["total_budget"]


def test_edit_marks_old_plans_stale_syncs_people_and_regenerates(client):
    trip, old_plans = _group(client)
    updated = client.put(
        f"/trips/{trip['id']}",
        json={
            "currency": "USD",
            "destination_city": "Boston",
            "traveler_count": 3,
            "total_budget": 3000,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["trip_version"] == 2 and body["traveler_count"] == 3
    assert len(client.get(f"/trips/{trip['id']}/travelers").json()) == 3
    assert client.get(f"/trips/{trip['id']}/plans").json() == []
    assert client.post(f"/plans/{old_plans[0]['id']}/select").status_code == 409
    regenerated = client.post(f"/trips/{trip['id']}/regenerate-plans")
    assert regenerated.status_code == 200
    assert all(plan["plan_set_version"] == 2 for plan in regenerated.json())


def test_unknown_price_can_never_claim_zero():
    try:
        PriceEstimate(currency="USD", price_type=PriceType.UNKNOWN, expected=0)
    except ValidationError:
        pass
    else:
        raise AssertionError("UNKNOWN zero price must be rejected")
