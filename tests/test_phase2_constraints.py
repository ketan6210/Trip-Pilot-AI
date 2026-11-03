from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.ai.constraints import opening_windows, prepare_candidates
from backend.ai.optimizer import optimize, validate_schedule
from backend.ai.schemas import Candidate, PlanningConstraints, RankedCandidate
from backend.core.errors import AppError


@pytest.mark.parametrize(
    "raw,day,expected",
    [
        ("24/7", date(2027, 6, 1), [(0, 2880)]),
        ("Mo-Fr 09:00-18:00; Sa-Su off", date(2027, 6, 5), []),
        ("Mo-Su 09:00-18:00; Tu off", date(2027, 6, 1), []),
        ("Mo-Fr 09:00-12:00,13:00-18:00", date(2027, 6, 1), [(540, 720), (780, 1080)]),
        ("18:00-02:00", date(2027, 6, 1), [(1080, 1560)]),
        ("PH off; 09:00-17:00", date(2027, 6, 1), None),
        ("unknown", date(2027, 6, 1), None),
        ("99:00-100:00", date(2027, 6, 1), None),
        ("closed", date(2027, 6, 1), []),
    ],
)
def test_hours_are_conservative(raw, day, expected):
    assert opening_windows({"raw": raw}, day) == expected


def fixture_candidate(number, category="museums", latitude=40.7, longitude=-74.0, cost=1000, duration=60):
    return Candidate(
        place_id=number,
        external_id=str(number),
        name=f"Synthetic {number}",
        category=category,
        subcategory="fixture",
        description="Synthetic evaluation candidate",
        latitude=latitude,
        longitude=longitude,
        neighborhood="fixture",
        duration_minutes=duration,
        cost_cents=cost,
        currency="USD",
        price_source="test",
        price_type="ESTIMATED",
        rating=4.5,
        provider="synthetic",
        opening_windows={date(2027, 6, 1) + timedelta(days=i): [(540, 1320)] for i in range(2)},
        hours_known=True,
        source="test",
    )


def fixture_trip(days=2):
    return SimpleNamespace(
        start_date=date(2027, 6, 1),
        end_date=date(2027, 6, days),
        transport_mode="walking",
        max_walking_per_day=180,
        travelers=[SimpleNamespace(walking_tolerance=180)],
    )


def rankings(candidates):
    return [
        RankedCandidate(
            candidate=row, score=0.8, components={}, traveler_scores=[0.8], reason_codes=["preference_match"]
        )
        for row in candidates
    ]


def test_cp_sat_clusters_mandatory_places_and_validates_times():
    candidates = [
        fixture_candidate(1),
        fixture_candidate(2, latitude=40.8),
        fixture_candidate(3, longitude=-74.001),
        fixture_candidate(4, latitude=40.801),
        fixture_candidate(5, "food"),
        fixture_candidate(6, "food", latitude=40.8),
    ]
    policy = PlanningConstraints(must_place_ids=[1, 2, 3, 4, 5, 6])
    trip = fixture_trip()
    result = optimize(rankings(candidates), trip, policy, "balanced", 6000)
    assert result.distance_km < 1
    assert result.activity_cost_cents == 6000
    assert validate_schedule(result, candidates, trip, policy, 6000, "balanced") == []
    damaged = result.model_copy(deep=True)
    damaged.days[0].stops[1].start_minute = damaged.days[0].stops[0].start_minute
    assert "schedule_time" in validate_schedule(damaged, candidates, trip, policy, 6000, "balanced")


def test_solver_never_relaxes_required_budget_or_hours():
    dinner = fixture_candidate(1, "food", cost=1000)
    policy = PlanningConstraints(must_place_ids=[1])
    with pytest.raises(AppError):
        optimize(rankings([dinner]), fixture_trip(1), policy, "balanced", 999)
    closed = dinner.model_copy(update={"opening_windows": {date(2027, 6, 1): [(540, 600)]}})
    with pytest.raises(AppError):
        optimize(rankings([closed]), fixture_trip(1), policy, "balanced", 1000)


def test_return_anchor_and_must_return_by_are_hard():
    dinner = fixture_candidate(1, "food", latitude=40.72, duration=60)
    policy = PlanningConstraints(
        anchor_latitude=40.7, anchor_longitude=-74, return_by_minute=18 * 60 + 5, must_place_ids=[1]
    )
    with pytest.raises(AppError):
        optimize(rankings([dinner]), fixture_trip(1), policy, "balanced", 1000)


def test_hard_filters_reject_unknown_diet_and_accessibility(client):
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import Place
    from tests.phase2_fixtures import advanced_trip

    trip_data = advanced_trip(client)
    with SessionLocal() as db:
        from backend.ai.pipeline import loaded_trip

        trip = loaded_trip(db, trip_data["id"])
        place = db.scalar(select(Place).where(Place.city == "New York City", Place.category == "food"))
        place.source_metadata = {"wheelchair": "unknown", "dietary_options": []}
        candidates, rejected = prepare_candidates([place], trip, PlanningConstraints(wheelchair_required=True))
        assert not candidates and rejected[0]["reason"] == "accessibility_unverified"
        candidates, rejected = prepare_candidates([place], trip, PlanningConstraints(dietary_requirements=["vegan"]))
        assert not candidates and rejected[0]["reason"] == "dietary_incompatible_or_unknown"
        place.opening_hours = {"raw": "closed"}
        candidates, rejected = prepare_candidates([place], trip, PlanningConstraints())
        assert not candidates and rejected[0]["reason"] == "closed_during_trip"
