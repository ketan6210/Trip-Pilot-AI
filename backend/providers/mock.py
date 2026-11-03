from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.geography.maps import google_maps_place_url
from backend.itinerary.best_time import best_time
from backend.itinerary.duration_model import estimate_duration
from backend.models import Place
from backend.providers.base import PlaceProvider, PlaceSearch


def distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Calculate great-circle distance for the non-PostGIS development path."""
    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))


class DatabasePlaceProvider(PlaceProvider):
    """Read normalized mock-provider records from the shared place database."""

    def __init__(self, db: Session):
        self.db = db

    def search(self, query: PlaceSearch) -> list[Place]:
        statement = select(Place).where(func.lower(Place.city) == query.city.lower())
        if query.category:
            statement = statement.where(func.lower(Place.category) == query.category.lower())
        if query.subcategory:
            statement = statement.where(func.lower(Place.subcategory) == query.subcategory.lower())
        if query.neighborhood:
            statement = statement.where(func.lower(Place.neighborhood) == query.neighborhood.lower())
        if query.external_provider:
            statement = statement.where(func.lower(Place.external_provider) == query.external_provider.lower())
        has_radius = query.latitude is not None and query.longitude is not None and query.radius_km is not None
        if has_radius and self.db.bind and self.db.bind.dialect.name == "postgresql":
            statement = statement.where(
                text(
                    "ST_DWithin(location, ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography, :radius_meters)"
                )
            ).params(longitude=query.longitude, latitude=query.latitude, radius_meters=query.radius_km * 1000)
        places = list(self.db.scalars(statement.order_by(Place.rating.desc(), Place.review_count.desc())))
        if has_radius and (not self.db.bind or self.db.bind.dialect.name != "postgresql"):
            places = [
                place
                for place in places
                if distance_km(query.latitude, query.longitude, place.latitude, place.longitude) <= query.radius_km
            ]
        return places[: query.limit]

    def details(self, place_id: int) -> Place | None:
        return self.db.get(Place, place_id)


def place_to_dict(place: Place) -> dict:
    metadata = place.source_metadata or {}
    duration = estimate_duration(
        place.category,
        place.subcategory,
        name=place.name,
        tags=place.tags,
    )
    time_window = best_time(place.category, place.subcategory, place.name)
    return {
        "database_id": place.id,
        "id": place.external_place_id,
        "name": place.name,
        "country": place.country,
        "state": place.state,
        "city": place.city,
        "borough_or_district": place.borough_or_district,
        "neighborhood": place.neighborhood,
        "address": place.address,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "category": place.category,
        "subcategory": place.subcategory,
        "tags": place.tags,
        "rating": place.rating,
        "quality_score": float((place.source_metadata or {}).get("ranking_score", place.rating)),
        "review_count": place.review_count,
        "rating_source": place.rating_source,
        "google_place_id": place.google_place_id,
        "phone_number": place.phone_number,
        "phone_source_url": place.phone_source_url,
        "price_level": place.price_level,
        "cost": float(place.cost_expected),
        "cost_min": float(place.cost_min),
        "cost_max": float(place.cost_max),
        "duration_minutes": duration.expected,
        "duration_min": duration.minimum,
        "duration_expected": duration.expected,
        "duration_max": duration.maximum,
        "duration_source": duration.source,
        "best_time_of_day": time_window.label,
        "opening_hours": (place.opening_hours or {}).get("raw"),
        "indoor_outdoor": place.indoor_outdoor,
        "reservation_required": place.reservation_required,
        "age_restriction": place.age_restriction,
        "accessibility": metadata.get("accessibility") or metadata.get("wheelchair") or "unknown",
        "popularity": {"rating": place.rating, "review_count": place.review_count},
        "maps_url": google_maps_place_url(place.name, place.address, place.latitude, place.longitude),
        "official_url": place.official_url,
        "ticket_url": place.ticket_url,
        "reservation_url": place.reservation_url,
        "image_url": place.image_url,
        "image_source": place.image_source,
        "image_attribution": place.image_attribution,
        "cost_status": place.cost_status,
        "cost_source": place.cost_source,
        "cost_currency": place.cost_currency,
        "published_cost": metadata.get("published_price"),
        "published_currency": metadata.get("published_currency"),
        "external_provider": place.external_provider,
        "description": place.description,
    }
