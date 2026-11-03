def _selected_detail(client):
    trip = client.post(
        "/trips",
        json={
            "destination_city": "New York City",
            "origin": "Chicago",
            "start_date": "2026-10-10",
            "end_date": "2026-10-12",
            "traveler_count": 4,
            "total_budget": 3600,
            "currency": "USD",
        },
    ).json()
    for index in range(4):
        traveler = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": f"Traveler {index + 1}"}).json()
        client.put(
            f"/travelers/{traveler['id']}/preferences",
            json={
                "preferences": [
                    {"category": "museums", "score": 9},
                    {"category": "food", "score": 8},
                    {"category": "nightlife", "score": 7},
                ]
            },
        )
    client.put(
        f"/trips/{trip['id']}/budget",
        json={
            "mode": "fixed",
            "categories": {
                "flight": 700,
                "hotel": 1200,
                "food": 800,
                "nightlife": 250,
                "attraction": 350,
                "buffer": 300,
            },
        },
    )
    plans = client.post(f"/trips/{trip['id']}/generate-plans").json()
    selected = next(plan for plan in plans if plan["plan_type"] == "balanced")
    assert client.post(f"/plans/{selected['id']}/select").status_code == 200
    response = client.post(f"/plans/{selected['id']}/generate-detail")
    assert response.status_code == 200
    return trip, selected, response.json()


def test_meals_context_and_expense_drilldown_reconcile(client):
    trip, plan, detail = _selected_detail(client)
    checks = detail.get("validation", {}).get("checks", {})
    assert detail["stage"] == "READY", {
        "failed": [key for key, value in checks.items() if not value],
        "meals": [
            [(meal["meal_type"], meal["start_minute"]) for meal in day.get("meals", [])]
            for day in detail.get("days", [])
        ],
    }
    assert detail["expenses"]["reconciliation"]["valid"] is True
    assert detail["budget"]["expected"] == detail["expenses"]["expected_total"]
    assert (
        sum(category["expected"] for category in detail["expenses"]["categories"])
        == detail["expenses"]["expected_total"]
    )
    for day in detail["days"]:
        assert {meal["meal_type"] for meal in day["meals"]} == {"breakfast", "lunch", "dinner"}
        assert len(day["timeline"]) == len(day["items"]) + 3
        assert all(
            item["start_minute"] >= previous["end_minute"]
            for previous, item in zip(day["timeline"], day["timeline"][1:])
        )
        assert all(
            meal["restaurant_id"] and meal["maps_url"].startswith("https://www.google.com/maps/")
            for meal in day["meals"]
        )
        meals = client.get(f"/plans/{plan['id']}/days/{day['day_number']}/meals")
        assert meals.status_code == 200 and len(meals.json()) == 3
        meal_expense = client.get(f"/plans/{plan['id']}/meals/{day['meals'][0]['id']}/expense")
        assert meal_expense.status_code == 200
        assert meal_expense.json()["itinerary_item_id"] == day["meals"][0]["item_id"]

    dashboard = client.get(f"/plans/{plan['id']}/expenses").json()
    food = client.get(f"/plans/{plan['id']}/expenses/food").json()
    assert dashboard["reconciliation"]["valid"] is True
    assert len(food["days"]) == 3
    assert all(
        {line["subcategory"] for line in day["items"]} == {"breakfast", "lunch", "dinner"} for day in food["days"]
    )
    assert sum(day["expected"] for day in food["days"]) == food["expected"]
    assert all(line["calculation_expression"] and line["assumptions"] for day in food["days"] for line in day["items"])

    attraction = next(item for day in detail["days"] for item in day["items"])
    context = client.get(f"/places/{attraction['place_id']}/context")
    assert context.status_code == 200
    assert context.json()["short_description"] and context.json()["why_visit"]
    assert context.json()["source_records"]
    assert context.json()["movie_tv_connections"] == []


def test_nearby_restaurant_replacement_updates_only_meal_and_budget(client):
    _, plan, detail = _selected_detail(client)
    target = detail["days"][1]["meals"][2]
    before_other_days = [day["meals"] for day in detail["days"] if day["day_number"] != 2]
    response = client.post(
        f"/plans/{plan['id']}/days/2/meals/dinner/replace",
        json={},
    )
    assert response.status_code == 200
    alternatives = response.json()["alternatives"]
    assert 1 <= len(alternatives) <= 5
    assert all(
        option["restaurant_id"] != target["restaurant_id"] and option["distance_km"] >= 0 for option in alternatives
    )
    chosen = min(alternatives, key=lambda option: option["group_total"])
    replaced = client.post(
        f"/plans/{plan['id']}/days/2/meals/dinner/replace",
        json={"restaurant_id": chosen["restaurant_id"]},
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["meal"]["restaurant_id"] == chosen["restaurant_id"]
    assert body["detail"]["days"][1]["meals"][2]["restaurant_id"] == chosen["restaurant_id"]
    assert [day["meals"] for day in body["detail"]["days"] if day["day_number"] != 2] == before_other_days
    assert body["detail"]["expenses"]["reconciliation"]["valid"] is True
    assert body["detail"]["budget"]["expected"] == body["detail"]["expenses"]["expected_total"]
    persisted = client.get(f"/api/v1/itineraries/{plan['id']}").json()
    assert float(persisted["budget"]["estimated_total"]) == body["detail"]["budget"]["expected"]
    assert any(activity["place_id"] == chosen["restaurant_id"] for activity in persisted["days"][1]["activities"])
