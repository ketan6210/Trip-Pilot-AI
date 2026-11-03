import asyncio

import httpx

from backend.providers.http import ProviderError, request_async
from backend.settings import settings


async def resolve_route_locations(origin: str, destination: str) -> dict:
    """Independent lookups run concurrently; no ORM session crosses tasks."""
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:

        async def lookup(name: str):
            response = await request_async(
                lambda: client.get(
                    settings.city_search_url, params={"name": name, "count": 1, "language": "en", "format": "json"}
                ),
                "Open-Meteo",
            )
            try:
                rows = response.json().get("results", [])
                if not rows:
                    return None
                row = rows[0]
                return {
                    "name": row["name"],
                    "country": row.get("country"),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderError("Open-Meteo", "PROVIDER_INVALID_RESPONSE", False, 502) from exc

        first, second = await asyncio.gather(lookup(origin), lookup(destination))
        return {"origin": first, "destination": second}
