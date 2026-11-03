def create_selected_plan(client):
    trip = client.post(
        "/trips",
        json={
            "currency": "USD",
            "destination_city": "New York City",
            "origin": "Chicago",
            "start_date": "2026-09-10",
            "end_date": "2026-09-12",
            "traveler_count": 2,
            "total_budget": 2400,
        },
    ).json()
    for name, nightlife, museums in [("Ari", 10, 2), ("Sam", 2, 10)]:
        traveler = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": name}).json()
        client.put(
            f"/travelers/{traveler['id']}/preferences",
            json={
                "preferences": [
                    {"category": "nightlife", "score": nightlife},
                    {"category": "museums", "score": museums},
                ]
            },
        )
    client.put(
        f"/trips/{trip['id']}/budget",
        json={
            "mode": "fixed",
            "categories": {
                "flight": 480,
                "hotel": 840,
                "food": 480,
                "nightlife": 192,
                "attraction": 288,
                "buffer": 120,
            },
        },
    )
    plans = client.post(f"/trips/{trip['id']}/generate-plans").json()
    experience = next(plan for plan in plans if plan["plan_type"] == "experience_heavy")
    client.post(f"/plans/{experience['id']}/select")
    return trip, experience


def test_selected_plan_generates_grounded_detailed_timeline(client):
    trip, plan = create_selected_plan(client)
    response = client.post(f"/plans/{plan['id']}/generate-detail")
    assert response.status_code == 200
    detail = response.json()
    assert detail["stage"] == "READY"
    assert detail["stage_history"][0] == "SELECTED"
    assert detail["stage_history"][-1] == "READY"
    assert len(detail["days"]) == 3
    items = [item for day in detail["days"] for item in day["items"]]
    assert len(items) == plan["activity_count"]
    assert len({item["place_id"] for item in items}) == len(items)
    assert all(item["name"] and item["neighborhood"] and item["address"] for item in items)
    assert all(item["maps_url"].startswith("https://www.google.com/maps/search/") for item in items)
    assert all(
        not item["directions_url"]
        if index == 0
        else item["directions_url"].startswith("https://www.google.com/maps/dir/")
        for day in detail["days"]
        for index, item in enumerate(day["items"])
    )
    assert all(
        item["photo_source"] == "category_placeholder" and item["photo_url_or_reference"].startswith("/placeholders/")
        for item in items
    )
    assert all(item["cost_min"] <= item["cost_expected"] <= item["cost_max"] for item in items)
    assert all(item["group_cost_expected"] == item["cost_expected"] * trip["traveler_count"] for item in items)
    assert sum(day["cost_expected"] for day in detail["days"]) == sum(item["group_cost_expected"] for item in items)
    assert all(len(item["individual_matches"]) == trip["traveler_count"] for item in items)
    assert detail["budget"]["minimum"] <= detail["budget"]["expected"] <= detail["budget"]["safe"]
    assert detail["budget"]["status"] == "WITHIN_BUDGET"
    assert detail["validation"]["valid"] is True
    assert detail["data_provenance"] == {
        "places": "mock",
        "routes": "deterministic_haversine_estimate",
        "photos": "category_placeholders",
    }
    persisted = client.get(f"/plans/{plan['id']}/detail")
    assert persisted.status_code == 200
    assert persisted.json() == detail
    budget = client.get(f"/plans/{plan['id']}/budget").json()
    assert budget == detail["budget"]


def test_detail_requires_plan_selection(client):
    _, selected = create_selected_plan(client)
    plans = client.get(f"/trips/{selected['trip_id']}/plans").json()
    candidate = next(plan for plan in plans if plan["status"] == "candidate")
    response = client.post(f"/plans/{candidate['id']}/generate-detail")
    assert response.status_code == 422
    assert "select the plan" in response.json()["detail"]
