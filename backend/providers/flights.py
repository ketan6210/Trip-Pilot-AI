from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from threading import Lock
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx

from backend.flights.base import FlightSearchRequest
from backend.models import Trip
from backend.providers.http import request_with_retry
from backend.settings import settings

_QUOTE_CACHE: dict[tuple, tuple[datetime, dict]] = {}
_QUOTE_LOCK = Lock()


CITY_CODES = {
    "new york": "NYC",
    "new york city": "NYC",
    "nyc": "NYC",
    "newark": "EWR",
    "newark nj": "EWR",
    "newark, nj": "EWR",
    "chicago": "CHI",
    "london": "LON",
    "boston": "BOS",
    "miami": "MIA",
    "los angeles": "LAX",
    "san francisco": "SFO",
    "washington dc": "WAS",
    "paris": "PAR",
    "rome": "ROM",
    "madrid": "MAD",
    "barcelona": "BCN",
    "berlin": "BER",
    "amsterdam": "AMS",
    "dublin": "DUB",
    "tokyo": "TYO",
    "singapore": "SIN",
    "dubai": "DXB",
    "toronto": "YTO",
    "vancouver": "YVR",
    "delhi": "DEL",
    "new delhi": "DEL",
    "varanasi": "VNS",
    "banaras": "VNS",
    "mumbai": "BOM",
    "sydney": "SYD",
}


@lru_cache(maxsize=512)
def _resolve_city_code(city: str) -> str | None:
    normalized = city.strip().lower()
    if normalized in CITY_CODES:
        return CITY_CODES[normalized]
    try:
        response = request_with_retry(
            lambda: httpx.get(
                settings.flight_location_url,
                params=[("term", city), ("locale", "en"), ("types[]", "city")],
                headers={"User-Agent": settings.trippilot_user_agent},
                timeout=10.0,
                trust_env=False,
            ),
            provider="Flights",
        )
        response.raise_for_status()
        rows = response.json()
        exact = next((row for row in rows if str(row.get("name", "")).casefold() == city.casefold()), None)
        selected = exact or (rows[0] if rows else None)
        code = str((selected or {}).get("code", "")).upper()
        return code if re.fullmatch(r"[A-Z]{3}", code) else None
    except Exception:
        return None


def _search_url(trip: Trip, origin: str | None = None, destination: str | None = None) -> str:
    origin = origin or _resolve_city_code(trip.origin)
    destination = destination or _resolve_city_code(trip.destination_city)
    if not origin or not destination:
        return "https://www.skyscanner.com/transport/flights"
    params = {
        "adultsv2": trip.traveler_count,
        "cabinclass": "economy",
        "preferDirects": "false",
        "rtn": "1",
        "currency": trip.currency,
    }
    outbound = trip.start_date.strftime("%y%m%d")
    inbound = trip.end_date.strftime("%y%m%d")
    return (
        "https://www.skyscanner.com/transport/flights/"
        f"{origin.lower()}/{destination.lower()}/{outbound}/{inbound}/?{urlencode(params)}"
    )


def _parse_amount(value) -> float | None:
    unit = "PRICE_UNIT_WHOLE"
    if isinstance(value, dict):
        unit = str(value.get("unit") or unit)
        value = value.get("amount") or value.get("value")
    try:
        amount = float(value)
        divisor = {
            "PRICE_UNIT_WHOLE": 1,
            "PRICE_UNIT_CENTI": 100,
            "PRICE_UNIT_MILLI": 1_000,
            "PRICE_UNIT_MICRO": 1_000_000,
        }.get(unit, 1)
        amount /= divisor
        return amount if amount > 0 else None
    except (TypeError, ValueError):
        return None


def _skyscanner_quote(request: FlightSearchRequest, base: dict) -> dict | None:
    """Use Skyscanner's official partner Live Prices endpoint when configured."""
    if not settings.skyscanner_api_key:
        return None
    origin = base.get("origin_code")
    destination = base.get("destination_code")
    if not origin or not destination:
        return None
    cabin = request.cabin.upper().replace(" ", "_")
    if not cabin.startswith("CABIN_CLASS_"):
        cabin = f"CABIN_CLASS_{cabin}"
    market, locale = {
        "INR": ("IN", "en-IN"),
        "GBP": ("UK", "en-GB"),
        "EUR": ("DE", "en-GB"),
        "CAD": ("CA", "en-CA"),
        "AUD": ("AU", "en-AU"),
        "JPY": ("JP", "en-GB"),
    }.get(request.currency.upper(), ("US", "en-US"))
    body = {
        "query": {
            "market": market,
            "locale": locale,
            "currency": request.currency.upper(),
            "queryLegs": [
                {
                    "originPlaceId": {"iata": origin},
                    "destinationPlaceId": {"iata": destination},
                    "date": {
                        "year": request.departure_date.year,
                        "month": request.departure_date.month,
                        "day": request.departure_date.day,
                    },
                },
                {
                    "originPlaceId": {"iata": destination},
                    "destinationPlaceId": {"iata": origin},
                    "date": {
                        "year": request.return_date.year,
                        "month": request.return_date.month,
                        "day": request.return_date.day,
                    },
                },
            ],
            "adults": request.adults,
            "cabinClass": cabin,
        }
    }
    headers = {"x-api-key": settings.skyscanner_api_key.get_secret_value(), "Content-Type": "application/json"}
    response = request_with_retry(
        lambda: httpx.post(
            f"{settings.skyscanner_base_url.rstrip('/')}/flights/live/search/create",
            headers=headers,
            json=body,
            timeout=settings.live_data_timeout_seconds,
        ),
        provider="Flights",
    )
    response.raise_for_status()
    payload = response.json()
    session_token = payload.get("sessionToken")
    poll_count = 0
    # The create response is intentionally only a partial cached subset. Poll
    # for completed inventory so the displayed minimum is based on the finished
    # route/date/adult search instead of the first provisional response.
    while session_token and payload.get("status") == "RESULT_STATUS_INCOMPLETE" and poll_count < 4:
        poll = request_with_retry(
            lambda: httpx.post(
                f"{settings.skyscanner_base_url.rstrip('/')}/flights/live/search/poll/{session_token}",
                headers=headers,
                timeout=settings.live_data_timeout_seconds,
            ),
            provider="Flights",
        )
        poll.raise_for_status()
        polled_payload = poll.json()
        if ((polled_payload.get("content") or {}).get("results") or {}).get("itineraries"):
            payload = polled_payload
        elif polled_payload.get("status"):
            payload["status"] = polled_payload["status"]
        poll_count += 1
    itineraries = ((payload.get("content") or {}).get("results") or {}).get("itineraries") or {}
    if isinstance(itineraries, dict):
        itineraries = list(itineraries.values())
    offers = []
    for itinerary in itineraries:
        for option in itinerary.get("pricingOptions") or []:
            amount = _parse_amount(option.get("price"))
            if amount is None:
                continue
            item = (option.get("items") or [{}])[0]
            link = item.get("deepLink") or option.get("deepLink") or base["search_url"]
            agent = item.get("agentId") or option.get("agentIds") or []
            offers.append(
                {
                    "total": round(amount, 2),
                    "per_person": round(amount / request.adults, 2),
                    "currency": request.currency.upper(),
                    "deep_link": link,
                    "agent": agent,
                    "itinerary_id": itinerary.get("id"),
                    "price_type": "LIVE",
                }
            )
    if not offers:
        return None
    offers.sort(key=lambda row: row["total"])
    cheapest = offers[0]
    return {
        **base,
        "status": "LIVE_FARE",
        "price_type": "LIVE",
        "provider": "Skyscanner Live Prices API",
        "total": cheapest["total"],
        "per_person": cheapest["per_person"],
        "offers": offers[:5],
        "source_url": cheapest["deep_link"],
        "booking_url": cheapest["deep_link"],
        "price_confirmed": True,
        "booking_is_provider_offer": True,
        "search_complete": payload.get("status") == "RESULT_STATUS_COMPLETE",
        "poll_count": poll_count,
        "message": "Live group fare returned for the exact route, dates, cabin, currency, and adult count. Fares can change before purchase.",
    }


def get_flight_quote(trip: Trip, refresh: bool = False) -> dict:
    """Return an actual Amadeus offer when credentials exist; otherwise never invent a fare."""
    allocation = float((trip.budget.categories if trip.budget else {}).get("flight", 0))
    origin = _resolve_city_code(trip.origin)
    destination = _resolve_city_code(trip.destination_city)
    base = {
        "status": "NOT_INCLUDED" if allocation <= 0 else "SEARCH_REQUIRED",
        "provider": None,
        "currency": trip.currency,
        "total": None,
        "per_person": None,
        "allocated_budget": round(allocation, 2),
        "search_url": _search_url(trip, origin, destination),
        "search_provider": "Skyscanner",
        "origin_code": origin,
        "destination_code": destination,
        "departure_date": trip.start_date.isoformat(),
        "return_date": trip.end_date.isoformat(),
        "traveler_count": trip.traveler_count,
        "source_url": None,
        "checked_at": datetime.now(UTC).isoformat(),
        "fetched_at": datetime.now(UTC).isoformat(),
        "price_type": "UNKNOWN",
        "offers": [],
        "cache_age_seconds": 0,
        "message": (
            "Flights are outside this trip budget."
            if allocation <= 0
            else f"Open the structured live search for {trip.traveler_count} adults to see current bookable prices."
        ),
    }
    if allocation <= 0:
        return base
    request = FlightSearchRequest(
        origin=trip.origin,
        destination=trip.destination_city,
        departure_date=trip.start_date,
        return_date=trip.end_date,
        adults=trip.traveler_count,
        currency=trip.currency,
    )
    now = datetime.now(UTC)
    if not refresh:
        with _QUOTE_LOCK:
            cached = _QUOTE_CACHE.get(request.cache_key)
        if cached and now - cached[0] <= timedelta(minutes=settings.flight_cache_minutes):
            return {**cached[1], "cache_age_seconds": round((now - cached[0]).total_seconds()), "cached": True}
    if settings.skyscanner_api_key:
        try:
            quote = _skyscanner_quote(request, base)
            if quote:
                with _QUOTE_LOCK:
                    _QUOTE_CACHE[request.cache_key] = (now, quote)
                return quote
        except Exception as exc:
            base["message"] = (
                f"Skyscanner did not return a usable live fare ({type(exc).__name__}); checking the fallback provider."
            )
    if not settings.amadeus_client_id or not settings.amadeus_client_secret or not origin or not destination:
        return base
    try:
        auth = request_with_retry(
            lambda: httpx.post(
                f"{settings.amadeus_base_url.rstrip('/')}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.amadeus_client_id,
                    "client_secret": settings.amadeus_client_secret.get_secret_value(),
                },
                timeout=settings.live_data_timeout_seconds,
            ),
            provider="Flights",
        )
        auth.raise_for_status()
        offers = request_with_retry(
            lambda: httpx.get(
                f"{settings.amadeus_base_url.rstrip('/')}/v2/shopping/flight-offers",
                headers={"Authorization": f"Bearer {auth.json()['access_token']}"},
                params={
                    "originLocationCode": origin,
                    "destinationLocationCode": destination,
                    "departureDate": trip.start_date.isoformat(),
                    "returnDate": trip.end_date.isoformat(),
                    "adults": trip.traveler_count,
                    "currencyCode": trip.currency,
                    "max": 10,
                },
                timeout=settings.live_data_timeout_seconds,
            ),
            provider="Flights",
        )
        offers.raise_for_status()
        rows = offers.json().get("data", [])
        if not rows:
            return base
        cheapest = min(
            rows,
            key=lambda row: float(row.get("price", {}).get("grandTotal") or row.get("price", {}).get("total") or "inf"),
        )
        pricing = request_with_retry(
            lambda: httpx.post(
                f"{settings.amadeus_base_url.rstrip('/')}/v1/shopping/flight-offers/pricing",
                headers={"Authorization": f"Bearer {auth.json()['access_token']}", "Content-Type": "application/json"},
                json={"data": {"type": "flight-offers-pricing", "flightOffers": [cheapest]}},
                timeout=settings.live_data_timeout_seconds,
            ),
            provider="Flights",
        )
        pricing.raise_for_status()
        confirmed_rows = pricing.json().get("data", {}).get("flightOffers", [])
        if not confirmed_rows:
            return {
                **base,
                "message": "The provider found a flight but could not confirm its current price; use the exact-route search link.",
            }
        confirmed = confirmed_rows[0]
        total = float(confirmed["price"].get("grandTotal") or confirmed["price"]["total"])
        carriers = sorted(
            {
                segment.get("carrierCode")
                for itinerary in confirmed.get("itineraries", [])
                for segment in itinerary.get("segments", [])
                if segment.get("carrierCode")
            }
        )
        test_environment = "test.api.amadeus.com" in settings.amadeus_base_url
        quote = {
            **base,
            "status": "TEST_FARE" if test_environment else "LIVE_FARE",
            "provider": "Amadeus Flight Offers Price",
            "currency": confirmed["price"].get("currency", trip.currency),
            "total": round(total, 2),
            "per_person": round(total / trip.traveler_count, 2),
            "source_url": _search_url(trip, origin, destination),
            "booking_url": _search_url(trip, origin, destination),
            "booking_is_provider_offer": False,
            "evidence_url": "https://developers.amadeus.com/self-service/category/flights/api-doc/flight-offers-price",
            "offer_id": confirmed.get("id") or cheapest.get("id"),
            "carriers": carriers,
            "price_confirmed": True,
            "price_type": "ESTIMATED" if test_environment else "LIVE",
            "offers": [
                {
                    "total": round(total, 2),
                    "per_person": round(total / trip.traveler_count, 2),
                    "currency": confirmed["price"].get("currency", trip.currency),
                    "deep_link": _search_url(trip, origin, destination),
                    "offer_id": confirmed.get("id"),
                    "carriers": carriers,
                    "price_type": "ESTIMATED" if test_environment else "LIVE",
                }
            ],
            "message": (
                "Amadeus test-environment example fare; this is not a live bookable quote."
                if test_environment
                else "Current group fare confirmed by Amadeus for this route, dates, and traveler count. The search link opens the same trip inputs; retail prices can still change before purchase."
            ),
        }
        if not test_environment:
            with _QUOTE_LOCK:
                _QUOTE_CACHE[request.cache_key] = (now, quote)
        return quote
    except Exception as exc:
        return {
            **base,
            "message": f"The live flight provider did not return a usable fare ({type(exc).__name__}); use the search link to recheck.",
        }


def search_flights(request: FlightSearchRequest, refresh: bool = False) -> dict:
    """Public provider-neutral search used by the API and trip planning."""
    trip = SimpleNamespace(
        origin=request.origin,
        destination_city=request.destination,
        start_date=request.departure_date,
        end_date=request.return_date,
        traveler_count=request.adults,
        currency=request.currency.upper(),
        budget=SimpleNamespace(categories={"flight": 1}),
    )
    return get_flight_quote(trip, refresh=refresh)
