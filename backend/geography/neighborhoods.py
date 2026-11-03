from collections import defaultdict
from statistics import mean

from backend.models import Place, Traveler
from backend.preference.aggregator import preference_for_place, traveler_preference_maps
from backend.providers.mock import distance_km, place_to_dict


def rank_neighborhoods(places: list[Place], travelers: list[Traveler], limit: int = 10) -> list[dict]:
    """Rank neighborhoods using transparent preference, density, quality, price, and compactness features."""
    grouped: dict[str, list[Place]] = defaultdict(list)
    for place in places:
        grouped[place.neighborhood].append(place)
    if not grouped:
        return []
    maps = traveler_preference_maps(travelers)
    maximum_density = max(len(items) for items in grouped.values())
    maximum_price = max((float(place.cost_expected) for place in places), default=1.0) or 1.0
    results = []
    for neighborhood, items in grouped.items():
        item_dicts = [place_to_dict(place) for place in items]
        preference_values = [
            preference_for_place(preferences, item) for preferences in maps.values() for item in item_dicts
        ] or [0.5]
        center_latitude = mean(place.latitude for place in items)
        center_longitude = mean(place.longitude for place in items)
        spread = mean(
            distance_km(center_latitude, center_longitude, place.latitude, place.longitude) for place in items
        )
        district = items[0].borough_or_district.lower()
        transit = 0.95 if district in {"manhattan", "boston"} else 0.85 if district == "brooklyn" else 0.80
        features = {
            "category_density": len(items) / maximum_density,
            "preference_match": mean(preference_values),
            "rating_quality": mean(place.rating for place in items) / 5,
            "price_fit": 1 - mean(float(place.cost_expected) for place in items) / maximum_price,
            "transit_connectivity": transit,
            "geographic_compactness": max(0.0, 1 - spread / 3),
            "hotel_proximity": 0.5,
        }
        score = (
            0.28 * features["preference_match"]
            + 0.20 * features["category_density"]
            + 0.18 * features["rating_quality"]
            + 0.12 * features["price_fit"]
            + 0.10 * features["transit_connectivity"]
            + 0.08 * features["geographic_compactness"]
            + 0.04 * features["hotel_proximity"]
        )
        ranked_places = sorted(items, key=lambda place: (-place.rating, float(place.cost_expected), place.name))
        results.append(
            {
                "neighborhood": neighborhood,
                "borough_or_district": items[0].borough_or_district,
                "score": round(score, 4),
                "features": {key: round(value, 4) for key, value in features.items()},
                "place_count": len(items),
                "center": {"latitude": round(center_latitude, 6), "longitude": round(center_longitude, 6)},
                "top_places": [place_to_dict(place) for place in ranked_places[:5]],
            }
        )
    return sorted(results, key=lambda item: (-item["score"], item["neighborhood"]))[:limit]


def build_location_hierarchy(places: list[Place]) -> dict:
    """Return country → state → city → district → neighborhood → exact venue hierarchy."""
    hierarchy: dict = {}
    for place in places:
        venues = (
            hierarchy.setdefault(place.country, {})
            .setdefault(place.state, {})
            .setdefault(place.city, {})
            .setdefault(place.borough_or_district, {})
            .setdefault(place.neighborhood, [])
        )
        venues.append(
            {"id": place.id, "name": place.name, "category": place.category, "subcategory": place.subcategory}
        )
    return hierarchy
