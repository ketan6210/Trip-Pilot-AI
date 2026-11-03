from __future__ import annotations

from datetime import date
from math import ceil

from backend.currency import currency_service
from backend.geography.cities import search_cities
from backend.geography.distance import distance_km
from backend.pricing.ledger import CITY_DAY_USD, CITY_HOTEL_USD

ROUGH_USD_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.78, "INR": 86.0, "CAD": 1.37, "AUD": 1.53, "JPY": 150.0}


KNOWN_CITIES = {
    "new york": (40.7128, -74.0060),
    "new york city": (40.7128, -74.0060),
    "nyc": (40.7128, -74.0060),
    "chicago": (41.8781, -87.6298),
    "los angeles": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194),
    "london": (51.5072, -0.1276),
    "paris": (48.8566, 2.3522),
    "rome": (41.9028, 12.4964),
    "tokyo": (35.6762, 139.6503),
    "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090),
    "mumbai": (19.0760, 72.8777),
}
STYLE_MULTIPLIER = {"budget": 0.68, "midrange": 0.86, "premium": 1.18, "luxury": 1.65}


def _coordinate(city: str | None) -> tuple[float, float] | None:
    if not city:
        return None
    known = KNOWN_CITIES.get(city.strip().casefold())
    if known:
        return known
    try:
        found = search_cities(city, 1)
        if found:
            return float(found[0]["latitude"]), float(found[0]["longitude"])
    except Exception:
        return None
    return None


def _flight_reserve_usd(origin: str | None, destination: str) -> tuple[float, str]:
    first, second = _coordinate(origin), _coordinate(destination)
    if not first or not second:
        return 350.0, "Route distance unavailable; a conservative generic round-trip airfare reserve was used."
    kilometers = distance_km(first[0], first[1], second[0], second[1])
    if kilometers <= 550:
        reserve = 180.0
    elif kilometers <= 1600:
        reserve = 260.0
    elif kilometers <= 4000:
        reserve = 420.0
    elif kilometers <= 8000:
        reserve = 700.0
    else:
        reserve = 950.0
    return reserve, f"Airfare reserve is distance-based for roughly {kilometers:,.0f} km; it is not a live fare."


def _convert(amount: float, currency: str) -> tuple[float, str]:
    currency = currency.upper()
    result = currency_service.convert(amount, "USD", currency)
    if result:
        return round(result.converted_amount, 2), "ECB_REFERENCE_FX"
    rate = ROUGH_USD_RATES.get(currency)
    if rate is None:
        raise ValueError(f"A planning conversion from USD to {currency} is unavailable")
    return round(amount * rate, 2), "ROUGH_PLANNING_FX"


def minimum_budget_estimate(
    *,
    destination: str,
    origin: str | None,
    start_date: date | None,
    end_date: date | None,
    traveler_count: int,
    currency: str,
    hotel_style: str = "budget",
    flight_included: bool = True,
) -> dict:
    """Return an explainable lower planning floor, never a quote or guarantee."""
    people = max(1, int(traveler_count))
    if start_date and end_date:
        days = max(1, (end_date - start_date).days + 1)
        dates_assumed = False
    else:
        days = 3
        dates_assumed = True
    nights = max(1, days - 1)
    rooms = ceil(people / 2)
    city = destination.strip().casefold()
    factor = CITY_DAY_USD.get(city, 1.0)
    nightly = CITY_HOTEL_USD.get(city, 150.0)
    style = STYLE_MULTIPLIER.get(hotel_style.strip().casefold(), STYLE_MULTIPLIER["budget"])

    components_usd: list[tuple[str, float, str]] = [
        ("hotel", nightly * style * rooms * nights, f"{rooms} budget room(s) × {nights} night(s)"),
        ("food", 52 * factor * 0.70 * people * days, f"Simple meals for {people} travelers × {days} days"),
        ("local transport", 10 * factor * people * days, "Low-cost city transport planning allowance"),
        ("places & tickets", 10 * factor * people * days, "Mostly free places plus a small paid-attraction allowance"),
    ]
    flight_note = "Flights are excluded."
    if flight_included:
        flight_per_person, flight_note = _flight_reserve_usd(origin, destination)
        components_usd.insert(
            0,
            (
                "round-trip flights",
                flight_per_person * people,
                f"{people} travelers × USD {flight_per_person:,.0f} rough airfare reserve",
            ),
        )
    subtotal_usd = sum(amount for _, amount, _ in components_usd)
    contingency_usd = subtotal_usd * 0.06
    components_usd.append(
        ("small contingency", contingency_usd, "6% for taxes, price movement, and minor unplanned costs")
    )

    converted_components = []
    fx_sources = set()
    for category, amount, explanation in components_usd:
        converted, fx_source = _convert(amount, currency)
        fx_sources.add(fx_source)
        converted_components.append({"category": category, "amount": converted, "explanation": explanation})
    total = round(sum(row["amount"] for row in converted_components), 2)
    assumptions = [
        "This is a rough bare-minimum planning indication, not a live quote or a spending target.",
        flight_note,
        "It assumes shared rooms, economical meals and transport, and mostly free or low-cost places.",
        "Live hotel, flight, cab, restaurant, and ticket prices can move above this floor.",
    ]
    if dates_assumed:
        assumptions.append("Dates are not complete yet, so the hint temporarily assumes a 3-day / 2-night trip.")
    if "ROUGH_PLANNING_FX" in fx_sources:
        assumptions.append("Live reference FX was unavailable, so a labeled rough planning conversion was used.")
    return {
        "status": "ROUGH_PLANNING_ESTIMATE",
        "destination": destination,
        "origin": origin,
        "currency": currency.upper(),
        "traveler_count": people,
        "days": days,
        "nights": nights,
        "rooms": rooms,
        "minimum_budget": total,
        "minimum_per_person": round(total / people, 2),
        "components": converted_components,
        "assumptions": assumptions,
        "flight_included": flight_included,
        "flight_is_live": False,
        "fx_source": ", ".join(sorted(fx_sources)),
        "message": f"A practical bare-minimum starting point for {people} travelers is about {currency.upper()} {total:,.0f}.",
    }
