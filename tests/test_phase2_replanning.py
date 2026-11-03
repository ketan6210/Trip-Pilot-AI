import pytest

from tests.phase2_fixtures import advanced_trip


def generated(client):
    trip = advanced_trip(client)
    response = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert response.status_code == 200, response.text
    return trip, response.json()["plans"][0]


def test_partial_edit_preserves_other_day_and_rollback_appends_version(client):
    trip, plan = generated(client)
    response = client.post(
        f"/api/v1/itineraries/{plan['id']}/replan", json={"expected_version": 1, "text": "Move dinner later on day 2"}
    )
    assert response.status_code == 200, response.text
    edited = response.json()["itinerary"]
    assert edited["days"][0] == plan["days"][0]  # IDs and all persisted values remain unchanged.
    before = next(item for item in plan["days"][1]["activities"] if item["category"] == "food")
    after = next(item for item in edited["days"][1]["activities"] if item["category"] == "food")
    assert after["place_id"] == before["place_id"]
    assert after["start_time"] > before["start_time"]
    assert edited["summary"]["itinerary_version"] == 2
    conflict = client.post(
        f"/api/v1/itineraries/{plan['id']}/replan", json={"expected_version": 1, "text": "Make day 2 cheaper"}
    )
    assert conflict.status_code == 409
    restored = client.post(
        f"/api/v1/itineraries/{plan['id']}/rollback", json={"expected_version": 2, "target_version": 1}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["summary"]["ai"]["schedule"] == plan["summary"]["ai"]["schedule"]
    assert restored.json()["summary"]["itinerary_version"] == 3
    assert (
        client.get(f"/api/v1/itineraries/{plan['id']}/versions").json()[0]["reason"]
        == "Initial constraint-optimized itinerary"
    )
    assert len(client.get(f"/api/v1/itineraries/{plan['id']}/versions").json()) == 3


def test_unknown_edit_is_non_destructive_and_legacy_mutation_cannot_bypass_validation(client):
    _, plan = generated(client)
    response = client.post(
        f"/api/v1/itineraries/{plan['id']}/replan",
        json={"expected_version": 1, "text": "Add the imaginary moon restaurant"},
    )
    assert response.status_code == 422
    assert client.get(f"/api/v1/itineraries/{plan['id']}").json()["days"] == plan["days"]
    assert client.post(f"/plans/{plan['id']}/generate-detail").status_code == 409


def test_budget_replan_is_partial_and_actually_cheaper(client):
    _, plan = generated(client)
    before = sum(float(item["estimated_cost"]) for item in plan["days"][1]["activities"])
    response = client.post(
        f"/api/v1/itineraries/{plan['id']}/replan", json={"expected_version": 1, "text": "Make day 2 cheaper"}
    )
    assert response.status_code == 200, response.text
    edited = response.json()["itinerary"]
    after = sum(float(item["estimated_cost"]) for item in edited["days"][1]["activities"])
    assert after <= before * 0.9
    assert edited["days"][0] == plan["days"][0]


@pytest.mark.parametrize("action", ["remove", "add", "reduce_walking"])
def test_remaining_edit_paths_preserve_other_day_and_enforce_request(client, action):
    _, plan = generated(client)
    day = plan["days"][0]
    category = next(row["category"] for row in day["activities"] if row["category"] != "food")
    command = {"action": action, "day_number": 1}
    if action == "remove":
        command["category"] = category
    elif action == "add":
        command["category"] = "nightlife"
    response = client.post(f"/api/v1/itineraries/{plan['id']}/replan", json={"expected_version": 1, "command": command})
    assert response.status_code == 200, response.text
    edited = response.json()["itinerary"]
    assert edited["days"][1] == plan["days"][1]
    categories = {row["category"] for row in edited["days"][0]["activities"]}
    if action == "remove":
        assert category not in categories
    elif action == "add":
        assert "nightlife" in categories
    else:
        before = plan["summary"]["ai"]["schedule"]["days"][0]
        after = edited["summary"]["ai"]["schedule"]["days"][0]
        old_travel = sum(row["travel_minutes"] for row in before["stops"]) + before["return_travel_minutes"]
        new_travel = sum(row["travel_minutes"] for row in after["stops"]) + after["return_travel_minutes"]
        assert new_travel <= int(old_travel * 0.75)
