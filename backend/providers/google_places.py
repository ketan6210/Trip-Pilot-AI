from __future__ import annotations

from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

import httpx

from backend.geography.distance import distance_km
from backend.models import Place
from backend.providers.http import request_with_retry
from backend.settings import settings

GOOGLE_PLACE_FIELDS = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.googleMapsUri",
    )
)


def _normalized(value: str) -> str:
    return " ".join("".join(character.casefold() if character.isalnum() else " " for character in value).split())


def _fresh(metadata: dict) -> bool:
    raw = metadata.get("google_places_checked_at")
    if not raw:
        return False
    try:
        checked = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        return checked >= datetime.now(UTC) - timedelta(days=settings.google_place_cache_days)
    except (TypeError, ValueError):
        return False


def _candidate_score(place: Place, candidate: dict) -> float:
    display = str((candidate.get("displayName") or {}).get("text") or "")
    name_score = SequenceMatcher(None, _normalized(place.name), _normalized(display)).ratio()
    location = candidate.get("location") or {}
    try:
        kilometers = distance_km(
            place.latitude,
            place.longitude,
            float(location["latitude"]),
            float(location["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return 0
    if kilometers > 1.5:
        return 0
    distance_score = max(0.0, 1 - kilometers / 1.5)
    return 0.76 * name_score + 0.24 * distance_score


def enrich_from_google(place: Place) -> bool:
    """Attach only fields returned by a conservatively matched Google Place.

    The function is intentionally a no-op without an official Maps Platform key.
    It never derives a rating or phone number from search snippets.
    """
    key = settings.google_maps_api_key.get_secret_value() if settings.google_maps_api_key else ""
    metadata = dict(place.source_metadata or {})
    if not key or _fresh(metadata):
        return False
    checked_at = datetime.now(UTC).isoformat()
    body = {
        "textQuery": f"{place.name}, {place.address or place.city}",
        "pageSize": 3,
        "locationBias": {
            "circle": {
                "center": {"latitude": place.latitude, "longitude": place.longitude},
                "radius": 500.0,
            }
        },
    }
    try:
        response = request_with_retry(
            lambda: httpx.post(
                f"{settings.google_places_base_url.rstrip('/')}/places:searchText",
                json=body,
                headers={
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": GOOGLE_PLACE_FIELDS,
                    "Content-Type": "application/json",
                },
                timeout=settings.live_data_timeout_seconds,
                trust_env=False,
            ),
            provider="Google Places",
        )
        response.raise_for_status()
        candidates = response.json().get("places") or []
    except (httpx.HTTPError, ValueError, TypeError):
        return False
    ranked = sorted(((_candidate_score(place, row), row) for row in candidates), key=lambda row: row[0], reverse=True)
    if not ranked or ranked[0][0] < 0.72:
        metadata["google_places_checked_at"] = checked_at
        metadata["google_places_match_status"] = "NO_CONFIDENT_MATCH"
        place.source_metadata = metadata
        return True

    score, candidate = ranked[0]
    maps_url = candidate.get("googleMapsUri")
    rating = candidate.get("rating")
    reviews = candidate.get("userRatingCount")
    phone = candidate.get("internationalPhoneNumber") or candidate.get("nationalPhoneNumber")
    changed = False
    if isinstance(rating, (int, float)) and 0 < float(rating) <= 5:
        place.rating = float(rating)
        place.review_count = max(0, int(reviews or 0))
        place.rating_source = "Google Places"
        changed = True
    if phone:
        place.phone_number = str(phone)
        place.phone_source_url = str(maps_url) if maps_url else place.maps_url
        changed = True
    if candidate.get("id"):
        place.google_place_id = str(candidate["id"])
        changed = True
    if maps_url:
        place.maps_url = str(maps_url)
        changed = True
    metadata.update(
        {
            "google_places_checked_at": checked_at,
            "google_places_match_status": "MATCHED",
            "google_places_match_score": round(score, 4),
            "google_places_uri": maps_url,
            "google_places_fields_returned": [
                field
                for field, present in {
                    "rating": rating is not None,
                    "review_count": reviews is not None,
                    "phone": bool(phone),
                    "maps_uri": bool(maps_url),
                }.items()
                if present
            ],
        }
    )
    place.source_metadata = metadata
    return changed or True
