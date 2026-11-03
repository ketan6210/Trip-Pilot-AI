from __future__ import annotations

from urllib.parse import urlencode

GOOGLE_MAPS_BASE_URL = "https://www.google.com/maps"


def google_maps_place_url(
    name: str,
    address: str,
    latitude: float,
    longitude: float,
) -> str:
    """Build a keyless, cross-platform Google Maps search URL for an exact venue."""
    query = ", ".join(part.strip() for part in (name, address) if part and part.strip())
    if not query:
        query = f"{latitude:.6f},{longitude:.6f}"
    return f"{GOOGLE_MAPS_BASE_URL}/search/?{urlencode({'api': 1, 'query': query})}"


def google_maps_directions_url(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    transport_mode: str,
) -> str:
    """Build a keyless Google Maps directions URL between two exact coordinates."""
    travel_mode = {
        "walking": "walking",
        "public transit": "transit",
        "mixed": "transit",
        "taxi": "driving",
        "rental car": "driving",
    }.get(transport_mode.strip().lower(), "driving")
    parameters = {
        "api": 1,
        "origin": f"{origin_latitude:.6f},{origin_longitude:.6f}",
        "destination": f"{destination_latitude:.6f},{destination_longitude:.6f}",
        "travelmode": travel_mode,
    }
    return f"{GOOGLE_MAPS_BASE_URL}/dir/?{urlencode(parameters)}"
