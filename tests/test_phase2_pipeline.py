from tests.phase2_fixtures import advanced_trip


def test_advanced_planning_persists_four_valid_distinct_versions(client):
    trip = advanced_trip(client)
    response = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert response.status_code == 200, response.text
    data = response.json()
    plans = data["plans"]
    assert len(plans) == 4
    assert len({tuple(sorted(plan["summary"]["ai_place_ids"])) for plan in plans}) == 4
    for plan in plans:
        assert float(plan["budget"]["estimated_total"]) <= trip["total_budget"]
        assert plan["summary"]["ai"]["constraint_violations"] == []
        assert plan["summary"]["ai"]["schedule"]["status"] in {"OPTIMAL", "FEASIBLE"}
        assert plan["summary"]["itinerary_version"] == 1
        for day in plan["days"]:
            assert sum(item["category"] == "food" for item in day["activities"]) == 1
        history = client.get(f"/api/v1/itineraries/{plan['id']}/versions").json()
        assert [item["version"] for item in history] == [1]
    metrics = client.get(f"/api/v1/trips/{trip['id']}/ai/runs/{data['run_id']}").json()
    assert metrics["status"] == "succeeded"
    assert metrics["latency_ms"] > 0
    assert metrics["metrics"]["llm_calls"] == 0
    assert metrics["metrics"]["structured_output_validity"] is None
    assert metrics["metrics"]["constraint_violation_rate"] == 0
