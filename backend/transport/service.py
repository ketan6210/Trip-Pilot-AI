from __future__ import annotations

from datetime import datetime
from math import ceil
from threading import Lock
from time import monotonic

import httpx

from backend.currency import currency_service
from backend.geography.distance import distance_km
from backend.geography.maps import google_maps_directions_url
from backend.providers.http import request_with_retry
from backend.settings import settings

TRANSIT_USD_PER_RIDE = {
    "new york": 2.90,
    "new york city": 2.90,
    "nyc": 2.90,
    "chicago": 2.50,
    "los angeles": 1.75,
    "san francisco": 2.75,
    "london": 3.75,
    "paris": 2.40,
    "rome": 1.65,
    "tokyo": 2.15,
    "delhi": 0.65,
    "new delhi": 0.65,
    "mumbai": 0.75,
}
CAB_PROFILE_USD = {
    "new york": (4.50, 2.50, 0.65),
    "new york city": (4.50, 2.50, 0.65),
    "nyc": (4.50, 2.50, 0.65),
    "chicago": (3.75, 2.10, 0.45),
    "los angeles": (4.00, 2.30, 0.55),
    "london": (5.00, 2.70, 0.65),
    "paris": (4.50, 2.25, 0.55),
    "delhi": (0.80, 0.42, 0.12),
    "new delhi": (0.80, 0.42, 0.12),
    "mumbai": (0.90, 0.48, 0.14),
}
# Used only if the live ECB reference-rate service is unavailable. Every result
# that uses this table is labeled as a rough planning FX assumption.
ROUGH_USD_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.78, "INR": 86.0, "CAD": 1.37, "AUD": 1.53, "JPY": 150.0}

_route_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = Lock()


def _travel_minutes(first, second, mode: str) -> int:
    kilometers = distance_km(first.latitude, first.longitude, second.latitude, second.longitude)
    speed = {"walking": 4.8, "public transit": 18, "taxi": 24}.get(mode, 16)
    transfer = 4 if mode == "walking" else 8
    return max(5, round(kilometers / speed * 60 + transfer))


def _convert_usd(amount: float, currency: str) -> tuple[float, str]:
    target = currency.upper()
    converted = currency_service.convert(amount, "USD", target)
    if converted:
        return round(converted.converted_amount, 2), "ECB_REFERENCE_FX"
    rate = ROUGH_USD_RATES.get(target)
    if rate is None:
        raise ValueError(f"A planning conversion from USD to {target} is unavailable")
    return round(amount * rate, 2), "ROUGH_PLANNING_FX"


def _seconds(value: str | None) -> int | None:
    if not value or not str(value).endswith("s"):
        return None
    try:
        return max(1, round(float(str(value)[:-1]) / 60))
    except ValueError:
        return None


def _money(value: dict | None) -> tuple[float, str] | None:
    if not value or not value.get("currencyCode"):
        return None
    try:
        amount = float(value.get("units") or 0) + float(value.get("nanos") or 0) / 1_000_000_000
        return amount, str(value["currencyCode"]).upper()
    except (TypeError, ValueError):
        return None


def _google_route(first, second, mode: str, target_currency: str) -> dict | None:
    key = settings.google_maps_api_key.get_secret_value() if settings.google_maps_api_key else ""
    if not key:
        return None
    cache_key = (
        round(first.latitude, 4),
        round(first.longitude, 4),
        round(second.latitude, 4),
        round(second.longitude, 4),
        mode,
        target_currency,
    )
    with _cache_lock:
        cached = _route_cache.get(cache_key)
        if cached and monotonic() - cached[0] < settings.route_cache_seconds:
            return cached[1]
    body = {
        "origin": {"location": {"latLng": {"latitude": first.latitude, "longitude": first.longitude}}},
        "destination": {"location": {"latLng": {"latitude": second.latitude, "longitude": second.longitude}}},
        "travelMode": mode,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    try:
        response = request_with_retry(
            lambda: httpx.post(
                settings.google_routes_url,
                json=body,
                headers={
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.travelAdvisory.transitFare",
                    "Content-Type": "application/json",
                },
                timeout=settings.live_data_timeout_seconds,
                trust_env=False,
            ),
            provider="Google Routes",
        )
        response.raise_for_status()
        route = (response.json().get("routes") or [None])[0]
        if not route:
            result = None
        else:
            result = {
                "duration_minutes": _seconds(route.get("duration")),
                "distance_km": round(float(route.get("distanceMeters") or 0) / 1000, 2),
                "route_source": "Google Routes",
            }
            fare = _money((route.get("travelAdvisory") or {}).get("transitFare"))
            if fare:
                amount, source_currency = fare
                converted = currency_service.convert(amount, source_currency, target_currency)
                if converted:
                    result["transit_fare_per_person"] = round(converted.converted_amount, 2)
                    result["transit_fare_currency"] = target_currency
                    result["fare_fx_source"] = "GOOGLE_FARE_WITH_ECB_REFERENCE_FX"
                elif source_currency == target_currency:
                    result["transit_fare_per_person"] = round(amount, 2)
                    result["transit_fare_currency"] = target_currency
                    result["fare_fx_source"] = "GOOGLE_FARE"
    except (httpx.HTTPError, ValueError, TypeError):
        result = None
    with _cache_lock:
        if result is not None:
            if len(_route_cache) >= 2048:
                _route_cache.pop(next(iter(_route_cache)))
            _route_cache[cache_key] = (monotonic(), result)
    return result


def _option(
    *,
    mode: str,
    per_person: float,
    group_cost: float,
    duration: int,
    minimum: float,
    maximum: float,
    source_quality: str,
    source_reference: str,
    calculation: str,
    route_source: str,
    vehicles: int = 0,
    fx_source: str | None = None,
) -> dict:
    return {
        "mode": mode,
        "cost_per_person": round(per_person, 2),
        "group_cost": round(group_cost, 2),
        "minimum_group_cost": round(minimum, 2),
        "maximum_group_cost": round(maximum, 2),
        "duration_minutes": int(duration),
        "source_quality": source_quality,
        "source_reference": source_reference,
        "calculation": calculation,
        "route_source": route_source,
        "vehicles_required": vehicles,
        "fx_source": fx_source,
    }


def choose_transport_leg(first, second, trip, departure: datetime | None = None) -> dict:
    """Compare group totals and choose the lowest-cost allowed city mode.

    Cab prices are planning estimates because Google Routes does not return taxi
    or rideshare prices. Transit uses Google's fare only when the response includes it.
    """
    del departure  # reserved for future provider departure-time routing
    city = str(trip.destination_city).strip().casefold()
    currency = str(trip.currency).upper()
    travelers = max(1, int(trip.traveler_count))
    straight_km = distance_km(first.latitude, first.longitude, second.latitude, second.longitude)
    transit_live = _google_route(first, second, "TRANSIT", currency)
    drive_live = _google_route(first, second, "DRIVE", currency)
    distance = (drive_live or {}).get("distance_km") or round(straight_km * 1.28, 2)
    transit_minutes = (transit_live or {}).get("duration_minutes") or _travel_minutes(first, second, "public transit")
    cab_minutes = (drive_live or {}).get("duration_minutes") or _travel_minutes(first, second, "taxi")
    transit_maps = google_maps_directions_url(
        first.latitude, first.longitude, second.latitude, second.longitude, "public transit"
    )
    cab_maps = google_maps_directions_url(first.latitude, first.longitude, second.latitude, second.longitude, "taxi")

    live_fare = (transit_live or {}).get("transit_fare_per_person")
    if live_fare is not None:
        transit_pp = float(live_fare)
        transit_quality = "GOOGLE_LIVE_FARE"
        transit_fx = (transit_live or {}).get("fare_fx_source")
        transit_range = (transit_pp * travelers, transit_pp * travelers)
    else:
        transit_pp, transit_fx = _convert_usd(TRANSIT_USD_PER_RIDE.get(city, 3.0), currency)
        transit_quality = "CITY_TRANSIT_ESTIMATE"
        transit_range = (transit_pp * travelers * 0.9, transit_pp * travelers * 1.35)
    transit_total = round(transit_pp * travelers, 2)
    transit = _option(
        mode="public transit",
        per_person=transit_pp,
        group_cost=transit_total,
        duration=transit_minutes,
        minimum=transit_range[0],
        maximum=transit_range[1],
        source_quality=transit_quality,
        source_reference=transit_maps,
        calculation=f"{travelers} travelers × {currency} {transit_pp:,.2f} = {currency} {transit_total:,.2f}",
        route_source=(transit_live or {}).get("route_source", "TripPilot distance estimate"),
        fx_source=transit_fx,
    )

    base_usd, per_km_usd, per_minute_usd = CAB_PROFILE_USD.get(city, (4.0, 2.1, 0.45))
    cab_usd = max(7.0, base_usd + per_km_usd * distance + per_minute_usd * cab_minutes)
    cab_capacity = 5
    vehicles = max(1, ceil(travelers / cab_capacity))
    one_cab, cab_fx = _convert_usd(cab_usd, currency)
    cab_total = round(one_cab * vehicles, 2)
    cab = _option(
        mode="cab",
        per_person=cab_total / travelers,
        group_cost=cab_total,
        duration=cab_minutes,
        minimum=cab_total * 0.78,
        maximum=cab_total * 1.45,
        source_quality="CAB_PLANNING_ESTIMATE",
        source_reference=cab_maps,
        calculation=f"{vehicles} cab{'s' if vehicles != 1 else ''} × {currency} {one_cab:,.2f} = {currency} {cab_total:,.2f} for {travelers}",
        route_source=(drive_live or {}).get("route_source", "TripPilot distance estimate"),
        vehicles=vehicles,
        fx_source=cab_fx,
    )

    requested = str(getattr(trip, "transport_mode", "mixed") or "mixed").strip().casefold()
    if requested == "walking":
        walking_minutes = _travel_minutes(first, second, "walking")
        selected = _option(
            mode="walking",
            per_person=0,
            group_cost=0,
            duration=walking_minutes,
            minimum=0,
            maximum=0,
            source_quality="FREE",
            source_reference=google_maps_directions_url(
                first.latitude, first.longitude, second.latitude, second.longitude, "walking"
            ),
            calculation=f"Walking = {currency} 0.00 for the group",
            route_source="TripPilot distance estimate",
        )
        options = [selected, transit, cab]
        reason = "Walking was selected because the trip explicitly requests walking."
    elif requested in {"public transit", "transit", "train"}:
        selected, options = transit, [transit, cab]
        reason = "Public transit was selected because the trip explicitly requests it."
    elif requested in {"taxi", "cab", "rental car"}:
        selected, options = cab, [transit, cab]
        reason = "Cab was selected because the trip explicitly requests door-to-door transport."
    else:
        options = [transit, cab]
        selected = min(options, key=lambda row: (row["group_cost"], row["duration_minutes"]))
        other = cab if selected is transit else transit
        saving = round(other["group_cost"] - selected["group_cost"], 2)
        reason = (
            f"{selected['mode'].title()} is cheaper for all {travelers} travelers by {currency} {saving:,.2f}; "
            "the comparison uses whole-group totals."
        )
    return {
        "from": first.name,
        "to": second.name,
        "currency": currency,
        "traveler_count": travelers,
        "distance_km": round(float(distance), 2),
        "selected_mode": selected["mode"],
        "selected_group_cost": selected["group_cost"],
        "selected_cost_per_person": selected["cost_per_person"],
        "duration_minutes": selected["duration_minutes"],
        "selection_reason": reason,
        "directions_url": selected["source_reference"],
        "source_quality": selected["source_quality"],
        "options": options,
    }
