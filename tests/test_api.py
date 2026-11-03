def make_trip(client):
    response = client.post(
        "/trips",
        json={
            "currency": "USD",
            "destination_city": "New York City",
            "origin": "Chicago",
            "start_date": "2026-09-10",
            "end_date": "2026-09-11",
            "traveler_count": 2,
            "total_budget": 3500,
            "pace": "balanced",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_phase_one_happy_path(client):
    trip = make_trip(client)
    traveler_ids = []
    for name in ["Ari", "Sam"]:
        response = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": name})
        assert response.status_code == 201
        traveler_ids.append(response.json()["id"])
    for traveler_id in traveler_ids:
        response = client.put(
            f"/travelers/{traveler_id}/preferences",
            json={
                "preferences": [
                    {"category": "nightlife", "subcategory": "jazz", "score": 10},
                    {"category": "museums", "subcategory": "classical art", "score": 3},
                ]
            },
        )
        assert response.status_code == 200
    response = client.put(
        f"/trips/{trip['id']}/budget",
        json={"mode": "fixed", "categories": {"hotel": 1200, "flight": 600, "food": 600, "attraction": 400}},
    )
    assert response.status_code == 200
    plan = client.post(f"/trips/{trip['id']}/generate-itinerary")
    assert plan.status_code == 200
    data = plan.json()
    items = [item for day in data["days"] for item in day["items"]]
    assert any(item["name"] == "Village Vanguard" for item in items)
    assert all(int(item["start_time"].split(":")[0]) >= 18 for item in items if item["category"] == "nightlife")
    assert data["itinerary_id"] > 0
    assert data["cost_summary"]["total_estimated"] <= data["cost_summary"]["trip_budget"]
    assert all(item["address"] and item["neighborhood"] for day in data["days"] for item in day["items"])


def test_rejects_bad_dates_and_too_many_travelers(client):
    bad = client.post(
        "/trips",
        json={
            "currency": "USD",
            "destination_city": "Boston",
            "origin": "NYC",
            "start_date": "2026-09-12",
            "end_date": "2026-09-10",
            "traveler_count": 2,
            "total_budget": 1000,
        },
    )
    assert bad.status_code == 422
    trip = make_trip(client)
    assert client.post(f"/trips/{trip['id']}/travelers", json={"display_name": "One"}).status_code == 201
    assert client.post(f"/trips/{trip['id']}/travelers", json={"display_name": "Two"}).status_code == 201
    assert client.post(f"/trips/{trip['id']}/travelers", json={"display_name": "Three"}).status_code == 409


def test_budget_categories_cannot_exceed_total(client):
    trip = make_trip(client)
    response = client.put(f"/trips/{trip['id']}/budget", json={"mode": "fixed", "categories": {"hotel": 4000}})
    assert response.status_code == 422
