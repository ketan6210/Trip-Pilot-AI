from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import Place
from tests.phase2_fixtures import advanced_trip


def register(client, email):
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "display_name": "Member", "password": "member-password-123"}
    )
    assert response.status_code == 201, response.text
    return response.json()["user"], {"Authorization": "Bearer " + response.json()["access_token"]}


def test_member_preferences_votes_and_cross_trip_isolation(client):
    trip = advanced_trip(client)
    group = client.get(f"/api/v1/trips/{trip['id']}/group").json()
    member, headers = register(client, "phase2-member@example.com")
    outsider, outsider_headers = register(client, "phase2-outsider@example.com")
    assert (
        client.put(
            f"/api/v1/groups/{group['id']}/members", json={"email": member["email"], "role": "VIEWER"}
        ).status_code
        == 200
    )
    preference = {"interests": {"nightlife": 10, "food": 8}, "description": "Live jazz and small music clubs"}
    response = client.put(f"/api/v1/trips/{trip['id']}/ai/preferences/me", json=preference, headers=headers)
    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/trips/{trip['id']}/ai/preferences/me").json()["interests"].get("museums") == 8
    assert client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={}, headers=headers).status_code == 403
    assert client.get(f"/api/v1/trips/{trip['id']}/ai/runs", headers=headers).status_code == 403
    with SessionLocal() as db:
        place_id = db.scalar(select(Place.id).where(Place.city == "New York City", Place.category == "nightlife"))
    ballot = client.put(
        f"/api/v1/trips/{trip['id']}/ai/votes", json={"place_id": place_id, "value": 1, "rank": 1}, headers=headers
    )
    assert ballot.status_code == 200, ballot.text
    assert ballot.json()["user_id"] == member["id"]
    assert (
        client.put(
            f"/api/v1/trips/{trip['id']}/ai/votes", json={"place_id": place_id, "value": -1}, headers=outsider_headers
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/trips/{trip['id']}/ai/preferences/me", headers=outsider_headers).status_code == 403
    generated = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert generated.status_code == 200, generated.text
    plans = generated.json()["plans"]
    scores = [row for plan in plans for row in plan["summary"]["ai"]["ranking"] if row["place_id"] == place_id]
    assert scores and all(row["components"]["votes"] > 0 for row in scores)
    assert client.get(f"/api/v1/itineraries/{plans[0]['id']}/versions", headers=outsider_headers).status_code == 403
    assert (
        client.post(
            f"/api/v1/itineraries/{plans[0]['id']}/replan",
            json={"expected_version": 1, "text": "Remove museum"},
            headers=headers,
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/groups/{group['id']}/members/{member['id']}").status_code == 204
    assert client.get(f"/api/v1/trips/{trip['id']}/ai/votes", headers=headers).status_code == 403
    with SessionLocal() as db:
        from backend.ai.group import vote_scores
        from backend.ai.pipeline import loaded_trip

        assert vote_scores(db, loaded_trip(db, trip["id"])).get(place_id, 0) == 0


def test_must_and_veto_conflict_blocks_generation(client):
    trip = advanced_trip(client)
    own = client.put(f"/api/v1/trips/{trip['id']}/ai/preferences/me", json={"must_categories": ["museums"]})
    assert own.status_code == 200
    saved = client.put(f"/api/v1/trips/{trip['id']}/ai/constraints", json={"veto_categories": ["museums"]})
    assert saved.status_code == 200
    response = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GROUP_CONSTRAINT_CONFLICT"


def test_provider_failure_preserves_selected_plan_and_llm_failure_falls_back(client, monkeypatch):
    trip = advanced_trip(client)
    from backend.llm.providers import LLMUnavailable

    def unavailable(*args, **kwargs):
        raise LLMUnavailable("offline")

    monkeypatch.setattr("backend.ai.pipeline.structured", unavailable)
    response = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": True})
    assert response.status_code == 200, response.text
    assert response.json()["metrics"]["fallback_used"] is True
    plan = response.json()["plans"][0]
    assert client.post(f"/api/v1/itineraries/{plan['id']}/select").status_code == 200
    from backend.providers.http import ProviderError

    def outage(*args, **kwargs):
        raise ProviderError("test_provider", "PROVIDER_UNAVAILABLE", True, 503)

    monkeypatch.setattr("backend.ai.pipeline.retrieve", outage)
    failed = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert failed.status_code == 503
    persisted = client.get(f"/api/v1/itineraries/{plan['id']}").json()
    assert persisted["status"] == "selected"
    assert persisted["days"] == plan["days"]
    assert client.get(f"/api/v1/trips/{trip['id']}/ai/runs").json()[0]["status"] == "failed"


def test_refreshed_price_triggers_bounded_budget_repair(client, monkeypatch):
    trip = advanced_trip(client, budget_amount="1800")
    from backend.providers.mock import DatabasePlaceProvider

    changed = []

    def enrich(self, place):
        if not changed and place.category not in {"food", "nature"}:
            place.cost_expected = Decimal("10000")
            changed.append(place.id)
        return place

    from decimal import Decimal

    monkeypatch.setattr(DatabasePlaceProvider, "enrich", enrich)
    response = client.post(f"/api/v1/trips/{trip['id']}/ai/generate", json={"use_llm": False})
    assert response.status_code == 200, response.text
    assert changed
    assert response.json()["metrics"]["budget_repairs"] >= 1
    assert all(float(plan["budget"]["estimated_total"]) <= 1800 for plan in response.json()["plans"])
    assert all(changed[0] not in plan["summary"]["ai_place_ids"] for plan in response.json()["plans"])
