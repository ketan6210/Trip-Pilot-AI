from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.mock_places import PLACE_GEO, PLACES
from backend.models import Place


def seed_mock_places(db: Session) -> int:
    """Idempotently load the synthetic provider fixture into normalized storage."""
    if (db.scalar(select(func.count()).select_from(Place)) or 0) > 0:
        return 0
    records = []
    for index, raw in enumerate(PLACES):
        country, state, district, latitude, longitude = PLACE_GEO[raw["id"]]
        expected = float(raw["cost"])
        records.append(
            Place(
                external_provider="mock",
                external_place_id=raw["id"],
                name=raw["name"],
                country=country,
                state=state,
                city=raw["city"],
                borough_or_district=district,
                neighborhood=raw["neighborhood"],
                address=raw["address"],
                latitude=latitude,
                longitude=longitude,
                category=raw["category"],
                subcategory=raw["subcategory"],
                tags=[raw["category"], raw["subcategory"]],
                rating=raw["rating"],
                review_count=1000 - index * 17,
                price_level=0 if expected == 0 else min(4, max(1, round(expected / 20))),
                rating_source="TripPilot test fixture",
                cost_min=expected * 0.85,
                cost_expected=expected,
                cost_max=expected * 1.20,
                duration_minutes=raw["duration_minutes"],
                # Explicit synthetic demo evidence, never a live-hours claim.
                opening_hours={"raw": "18:00-02:00" if raw["category"] == "nightlife" else "08:00-23:00"},
                indoor_outdoor="mixed",
                reservation_required=False,
                maps_url=raw["maps_url"],
                description=f"Mock provider record for {raw['name']}.",
                cost_status="ESTIMATED",
                cost_source="mock_fixture",
                cost_currency="USD",
                source_metadata={"fixture": True, "hours_source": "Synthetic demo schedule; verify real venue hours"},
            )
        )
    db.add_all(records)
    db.commit()
    return len(records)
