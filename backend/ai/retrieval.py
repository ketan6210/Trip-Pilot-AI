from backend.ai.constraints import prepare_candidates
from backend.core.errors import AppError
from backend.providers import get_place_provider
from backend.providers.base import PlaceSearch

CATEGORIES = ("museums", "nature", "nightlife", "food", "landmarks", "history", "shopping", "entertainment", "sports")


def retrieve(db, trip, constraints):
    provider = get_place_provider(db)
    # A broad base query plus category searches prevents highly rated museums from
    # crowding out restaurants, parks or minority interests in a capped result.
    records = {place.id: place for place in provider.search(PlaceSearch(city=trip.destination_city, limit=300))}
    for category in CATEGORIES:
        if sum(place.category == category for place in records.values()) < 8:
            for place in provider.search(PlaceSearch(city=trip.destination_city, category=category, limit=20)):
                records[place.id] = place
    for place_id in constraints.must_place_ids:
        place = provider.details(place_id)
        if place is None or place.city.casefold() != trip.destination_city.casefold():
            raise AppError(
                422,
                "MUST_HAVE_UNAVAILABLE",
                "A required place is unavailable in this destination.",
                {"place_ids": [place_id]},
            )
        records[place_id] = provider.enrich(place)
    # Preserve the existing spatial provider path when the user specifies a hotel/anchor.
    if constraints.anchor_latitude is not None:
        nearby = provider.search(
            PlaceSearch(
                city=trip.destination_city,
                latitude=constraints.anchor_latitude,
                longitude=constraints.anchor_longitude,
                radius_km=constraints.max_leg_km,
                limit=500,
            )
        )
        nearby_ids = {place.id for place in nearby}
        records = {
            key: value for key, value in records.items() if key in nearby_ids or key in constraints.must_place_ids
        }
    return provider, list(records.values())


def balanced_pool(ranked, limit, must_ids, days, must_subcategories=()):
    by_id = {row.candidate.place_id: row for row in ranked}
    selected = {key: by_id[key] for key in must_ids if key in by_id}
    for subcategory in must_subcategories:
        matches = [row for row in ranked if f"{row.candidate.category}/{row.candidate.subcategory}" == subcategory]
        for row in matches[:3]:
            selected[row.candidate.place_id] = row
    # Keep enough distinct restaurants to cover one priced dinner per day.
    for category in CATEGORIES:
        count = days + 3 if category == "food" else 3
        for row in [row for row in ranked if row.candidate.category == category][:count]:
            selected[row.candidate.place_id] = row
    limit = max(limit, len(selected))
    for row in ranked:
        if len(selected) >= limit:
            break
        selected[row.candidate.place_id] = row
    return sorted(selected.values(), key=lambda row: (-row.score, row.candidate.place_id))


def refresh_candidates(provider, records, schedule, trip, constraints):
    selected = {stop.place_id for day in schedule.days for stop in day.stops}
    refreshed = [provider.enrich(place) if place.id in selected else place for place in records]
    candidates, rejected = prepare_candidates(refreshed, trip, constraints)
    return candidates, rejected
