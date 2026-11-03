from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import Place


def evidence_fixture():
    """Synthetic hours/accessibility/diet; not claims about the real named venues."""
    with SessionLocal() as db:
        for place in db.scalars(select(Place)):
            place.opening_hours = {"raw": "18:00-02:00" if place.category == "nightlife" else "08:00-23:00"}
            place.source_metadata = {
                **place.source_metadata,
                "fixture": True,
                "accessibility": "yes",
                "dietary_options": ["vegetarian", "vegan", "gluten_free"],
                "evidence_source": "Synthetic evaluation fixture",
            }
        db.commit()


def advanced_trip(client, **overrides):
    response = client.post(
        "/api/v1/trips",
        json={
            "origin": "Chicago",
            "destination": "New York City",
            "start_date": "2027-06-01",
            "duration_days": 2,
            "travelers": 2,
            "budget_amount": "5000",
            "currency": "USD",
            "interests": ["museums", "nature", "food"],
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    trip = response.json()
    patch = client.patch(f"/api/v1/trips/{trip['id']}", json={"flight_included": False})
    assert patch.status_code == 200, patch.text
    evidence_fixture()
    return patch.json()
