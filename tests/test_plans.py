def create_group(client):
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
            "pace": "balanced",
        },
    ).json()
    travelers = []
    profiles = [
        [
            {"category": "nightlife", "subcategory": "jazz", "score": 10},
            {"category": "museums", "subcategory": "general", "score": 2},
        ],
        [
            {"category": "nightlife", "subcategory": "jazz", "score": 2},
            {"category": "museums", "subcategory": "general", "score": 10},
        ],
    ]
    for index, preferences in enumerate(profiles):
        traveler = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": f"Traveler {index + 1}"}).json()
        client.put(f"/travelers/{traveler['id']}/preferences", json={"preferences": preferences})
        travelers.append(traveler)
    client.put(
        f"/trips/{trip['id']}/budget",
        json={
            "mode": "fixed",
            "categories": {"flight": 600, "hotel": 900, "food": 300, "attraction": 350, "buffer": 100},
        },
    )
    return trip, travelers


def test_generates_four_distinct_fairness_aware_plans(client):
    trip, _ = create_group(client)
    response = client.post(f"/trips/{trip['id']}/generate-plans")
    assert response.status_code == 200
    plans = response.json()
    assert {plan["plan_type"] for plan in plans} == {"balanced", "experience_heavy", "budget", "relaxed"}
    assert all(0 <= plan["minimum_traveler_satisfaction"] <= plan["mean_group_satisfaction"] <= 1 for plan in plans)
    # Per-person display rounding must not change the authoritative group total.
    assert all(plan["estimated_per_person_cost"] == round(plan["estimated_total_cost"] / 2, 2) for plan in plans)
    assert len({tuple(plan["summary"]["place_ids"]) for plan in plans}) >= 3
    by_type = {plan["plan_type"]: plan for plan in plans}
    assert by_type["relaxed"]["activity_count"] < by_type["experience_heavy"]["activity_count"]
    assert by_type["budget"]["summary"]["activity_cost"] <= by_type["experience_heavy"]["summary"]["activity_cost"]
    assert all(
        place["group_cost"] == place["cost"] * trip["traveler_count"]
        for plan in plans
        for place in plan["summary"]["featured_places"]
    )
    assert all(plan["summary"]["currency"] == trip["currency"] for plan in plans)


def test_vote_is_single_choice_and_plan_can_be_selected(client):
    trip, travelers = create_group(client)
    plans = client.post(f"/trips/{trip['id']}/generate-plans").json()
    first, second = plans[0], plans[1]
    assert (
        client.post(
            f"/plans/{first['id']}/vote", json={"traveler_id": travelers[0]["id"], "vote_type": "vote"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/plans/{second['id']}/vote", json={"traveler_id": travelers[0]["id"], "vote_type": "vote"}
        ).status_code
        == 200
    )
    votes = client.get(f"/trips/{trip['id']}/votes").json()
    assert len(votes) == 1
    assert votes[0]["plan_variant_id"] == second["id"]
    selected = client.post(f"/plans/{second['id']}/select")
    assert selected.status_code == 200
    assert selected.json()["status"] == "selected"
    refreshed = client.get(f"/trips/{trip['id']}/plans").json()
    assert sum(plan["status"] == "selected" for plan in refreshed) == 1


def test_group_plans_require_two_travelers(client):
    trip = client.post(
        "/trips",
        json={
            "currency": "USD",
            "destination_city": "Boston",
            "origin": "NYC",
            "start_date": "2026-10-01",
            "end_date": "2026-10-02",
            "traveler_count": 2,
            "total_budget": 1200,
        },
    ).json()
    response = client.post(f"/trips/{trip['id']}/generate-plans")
    assert response.status_code == 422
    assert "two travelers" in response.json()["detail"]
