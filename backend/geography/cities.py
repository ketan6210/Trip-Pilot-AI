from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from backend.providers.http import request_with_retry
from backend.settings import settings

_CITY_CACHE: dict[str, tuple[datetime, list[dict]]] = {}


def search_cities(query: str, limit: int = 6) -> list[dict]:
    """Return ranked worldwide populated places from the live geocoding service."""
    normalized = " ".join(query.strip().split())
    if len(normalized) < 2:
        return []
    cache_key = normalized.casefold()
    cached = _CITY_CACHE.get(cache_key)
    if cached and cached[0] > datetime.now(UTC) - timedelta(hours=24):
        return cached[1][:limit]

    response = request_with_retry(
        lambda: httpx.get(
            settings.city_search_url,
            params={"name": normalized, "count": min(max(limit * 2, 6), 20), "language": "en", "format": "json"},
            headers={"User-Agent": settings.trippilot_user_agent},
            timeout=10.0,
            trust_env=False,
        ),
        provider="Open-Meteo",
    )
    response.raise_for_status()
    raw_results = response.json().get("results", [])
    populated = [item for item in raw_results if str(item.get("feature_code", "")).startswith("P")]
    candidates = populated or raw_results

    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in candidates:
        name = str(item.get("name", "")).strip()
        country = str(item.get("country", "")).strip()
        region = str(item.get("admin1", "")).strip()
        if not name or item.get("latitude") is None or item.get("longitude") is None:
            continue
        identity = (name.casefold(), region.casefold(), country.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        display_name = ", ".join(dict.fromkeys(part for part in (name, region, country) if part))
        results.append(
            {
                "id": int(item.get("id", 0)),
                "name": name,
                "display_name": display_name,
                "country": country,
                "country_code": str(item.get("country_code", "")),
                "region": region,
                "latitude": float(item["latitude"]),
                "longitude": float(item["longitude"]),
                "timezone": item.get("timezone"),
                "population": item.get("population"),
                "source": "Open-Meteo Geocoding",
            }
        )
        if len(results) >= limit:
            break
    _CITY_CACHE[cache_key] = (datetime.now(UTC), results)
    return results
