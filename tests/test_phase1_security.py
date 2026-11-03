from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.core.auth import token_digest, verify_password
from backend.db import SessionLocal
from backend.main import app
from backend.models import AuthSession, User


def register(client, email="guest@example.com"):
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "display_name": "Guest", "password": "a-long-password-123"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def trip_payload():
    return {
        "destination": "New York City",
        "origin": "Chicago",
        "start_date": "2027-06-01",
        "end_date": "2027-06-03",
        "travelers": 2,
        "budget_amount": "5000.00",
        "currency": "USD",
        "interests": ["museums", "food", "nature", "nightlife"],
    }


def create_trip(client):
    response = client.post("/api/v1/trips", json=trip_payload())
    assert response.status_code == 201, response.text
    return response.json()


def test_register_login_logout_and_password_storage(client):
    with TestClient(app) as guest:
        result = register(guest)
        token = result["access_token"]
        with SessionLocal() as db:
            user = db.get(User, result["user"]["id"])
            assert user.password_hash != "a-long-password-123"
            assert verify_password("a-long-password-123", user.password_hash)
            assert db.get(AuthSession, token_digest(token)) is not None
        assert guest.get("/api/v1/auth/me").status_code == 200
        assert guest.post("/api/v1/auth/logout").status_code == 403  # CSRF header required for cookie writes
        assert guest.post("/api/v1/auth/logout", headers={"X-Requested-With": "TripPilot"}).status_code == 204
        assert guest.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert (
            guest.post(
                "/api/v1/auth/login", json={"email": "guest@example.com", "password": "wrong-password-123"}
            ).status_code
            == 401
        )
        login = guest.post("/api/v1/auth/login", json={"email": "GUEST@example.com", "password": "a-long-password-123"})
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "password_hash" not in login.text


def test_expiry_duplicate_registration_and_rate_limit(client, monkeypatch):
    with TestClient(app) as guest:
        result = register(guest)
        duplicate = guest.post(
            "/api/v1/auth/register",
            json={"email": "GUEST@example.com", "display_name": "Other", "password": "a-long-password-123"},
        )
        assert duplicate.status_code == 409
        with SessionLocal() as db:
            session = db.get(AuthSession, token_digest(result["access_token"]))
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
        assert guest.get("/api/v1/auth/me").status_code == 401
        from backend.settings import settings

        monkeypatch.setattr(settings, "auth_rate_limit", 2)
        limited = guest.post(
            "/api/v1/auth/login", json={"email": "guest@example.com", "password": "a-long-password-123"}
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_no_unauthenticated_legacy_bypass_or_secret_echo(client):
    trip = create_trip(client)
    with TestClient(app) as anonymous:
        for path in (
            f"/trips/{trip['id']}",
            f"/api/v1/trips/{trip['id']}",
            "/places/search?city=Boston",
            "/orchestrator/status",
        ):
            assert anonymous.get(path).status_code == 401
        invalid = anonymous.post(
            "/api/v1/auth/register", json={"email": "bad", "display_name": "Name", "password": "secret"}
        )
        assert invalid.status_code == 422
        assert "secret" not in invalid.text
        blocked = anonymous.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"email": "guest@example.com", "password": "a-long-password-123"},
        )
        assert blocked.status_code == 403


def test_owner_editor_viewer_permissions_and_revocation(client):
    trip = create_trip(client)
    group = client.get(f"/api/v1/trips/{trip['id']}/group").json()
    conversation = client.post("/orchestrator/sessions").json()
    with TestClient(app) as guest:
        account = register(guest)
        guest.headers["Authorization"] = f"Bearer {account['access_token']}"
        assert guest.get(f"/trips/{trip['id']}").status_code == 403
        assert guest.get(f"/orchestrator/sessions/{conversation['id']}").status_code == 403
        assert guest.post(f"/orchestrator/sessions?trip_id={trip['id']}").status_code == 403
        assert guest.get("/api/v1/trips").json() == []
        assert (
            client.put(
                f"/api/v1/groups/{group['id']}/members", json={"email": "guest@example.com", "role": "VIEWER"}
            ).status_code
            == 200
        )
        assert guest.get(f"/api/v1/trips/{trip['id']}").status_code == 200
        assert guest.patch(f"/api/v1/trips/{trip['id']}", json={"total_budget": 6000}).status_code == 403
        assert guest.put(f"/trips/{trip['id']}", json={"total_budget": 6000}).status_code == 403
        assert (
            guest.put(
                f"/api/v1/groups/{group['id']}/members", json={"email": "owner@example.com", "role": "VIEWER"}
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/v1/groups/{group['id']}/members", json={"email": "guest@example.com", "role": "EDITOR"}
            ).status_code
            == 200
        )
        assert guest.patch(f"/api/v1/trips/{trip['id']}", json={"total_budget": 6000}).status_code == 200
        assert client.delete(f"/api/v1/groups/{group['id']}/members/{account['user']['id']}").status_code == 204
        assert guest.get(f"/trips/{trip['id']}").status_code == 403


def test_group_vote_identity_and_plan_access(client):
    trip = create_trip(client)
    plans = client.post(f"/api/v1/trips/{trip['id']}/generate").json()
    group = client.get(f"/api/v1/trips/{trip['id']}/group").json()
    with TestClient(app) as guest:
        account = register(guest)
        guest.headers["Authorization"] = f"Bearer {account['access_token']}"
        for suffix in ("", "/costs", "/detail", "/expenses", "/days/1/meals"):
            assert guest.get(f"/plans/{plans[0]['id']}{suffix}").status_code == 403
        assert (
            guest.post("/plans/compare", json={"plan_a_id": plans[0]["id"], "plan_b_id": plans[1]["id"]}).status_code
            == 403
        )
        client.put(f"/api/v1/groups/{group['id']}/members", json={"email": "guest@example.com", "role": "VIEWER"})
        for plan in plans[:2]:
            vote = guest.post(f"/api/v1/groups/{group['id']}/votes", json={"itinerary_id": plan["id"]})
            assert vote.status_code == 200, vote.text
            assert vote.json()["user_id"] == account["user"]["id"]
        assert len(client.get(f"/api/v1/groups/{group['id']}/votes").json()) == 1
        assert guest.post(f"/api/v1/itineraries/{plans[0]['id']}/select").status_code == 403
        assert (
            guest.post(
                f"/api/v1/groups/{group['id']}/votes", json={"itinerary_id": plans[0]["id"], "user_id": "someone-else"}
            ).status_code
            == 422
        )
        client.patch(f"/api/v1/trips/{trip['id']}", json={"total_budget": 6000})
        assert (
            guest.post(f"/api/v1/groups/{group['id']}/votes", json={"itinerary_id": plans[0]["id"]}).status_code == 409
        )
