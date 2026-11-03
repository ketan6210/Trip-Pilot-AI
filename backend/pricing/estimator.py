from __future__ import annotations

from datetime import UTC, datetime

from backend.currency import currency_service
from backend.pricing.models import PriceEstimate, PriceType

CATEGORY_USD = {
    "museums": (12, 24, 42),
    "history": (8, 18, 32),
    "landmarks": (8, 22, 40),
    "nature": (4, 12, 25),
    "nightlife": (35, 65, 110),
    "food": (18, 38, 75),
    "entertainment": (30, 62, 120),
    "sports": (28, 58, 110),
    "shopping": (20, 55, 120),
}
CITY_MULTIPLIER = {
    "new york city": 1.35,
    "new york": 1.35,
    "london": 1.25,
    "san francisco": 1.25,
    "paris": 1.15,
    "chicago": 1.05,
    "delhi": 0.42,
    "new delhi": 0.42,
    "varanasi": 0.32,
    "mumbai": 0.52,
}


def _converted_range(values: tuple[float, float, float], source: str, target: str):
    converted = [currency_service.convert(value, source, target) for value in values]
    if any(item is None for item in converted):
        return None
    return converted


def estimate_place_price(place: dict, display_currency: str, city: str | None = None) -> PriceEstimate:
    """Resolve provider evidence first, then use a labeled statistical estimate."""
    status = str(place.get("cost_status") or "UNKNOWN").upper()
    source_currency = str(place.get("cost_currency") or display_currency).upper()
    display_currency = display_currency.upper()
    raw = (
        float(place.get("cost_min") or 0),
        float(place.get("cost") or place.get("cost_expected") or 0),
        float(place.get("cost_max") or 0),
    )
    if status == "FREE":
        return PriceEstimate(
            minimum=0,
            expected=0,
            maximum=0,
            currency=display_currency,
            price_type=PriceType.FREE,
            provider=place.get("external_provider"),
            source=place.get("cost_source"),
            confidence=1,
            provider_amount=0,
            provider_currency=source_currency,
        )
    if status in {"PUBLISHED", "LIVE"} and raw[1] > 0:
        converted = _converted_range(raw, source_currency, display_currency)
        if converted:
            return PriceEstimate(
                minimum=converted[0].converted_amount,
                expected=converted[1].converted_amount,
                maximum=converted[2].converted_amount,
                currency=display_currency,
                price_type=PriceType.LIVE,
                provider=place.get("external_provider"),
                source=place.get("cost_source"),
                confidence=0.95,
                provider_amount=raw[1],
                provider_currency=source_currency,
                exchange_rate=converted[1].exchange_rate,
                rate_timestamp=converted[1].rate_timestamp,
            )
    if status == "ESTIMATED" and raw[1] > 0:
        converted = _converted_range(raw, source_currency, display_currency)
        if converted:
            return PriceEstimate(
                minimum=converted[0].converted_amount,
                expected=converted[1].converted_amount,
                maximum=converted[2].converted_amount,
                currency=display_currency,
                price_type=PriceType.ESTIMATED,
                provider=place.get("external_provider"),
                source=place.get("cost_source"),
                confidence=0.7,
                provider_amount=raw[1],
                provider_currency=source_currency,
                exchange_rate=converted[1].exchange_rate,
                rate_timestamp=converted[1].rate_timestamp,
            )

    category = str(place.get("category") or "landmarks")
    baseline = CATEGORY_USD.get(category, CATEGORY_USD["landmarks"])
    city_factor = CITY_MULTIPLIER.get((city or place.get("city") or "").casefold(), 1.0)
    level = int(place.get("price_level") or 1)
    level_factor = {0: 0.8, 1: 0.85, 2: 1.0, 3: 1.35, 4: 1.8}.get(level, 1.0)
    estimated_usd = tuple(round(value * city_factor * level_factor, 2) for value in baseline)
    converted = _converted_range(estimated_usd, "USD", display_currency)
    if not converted:
        return PriceEstimate(
            currency=display_currency,
            price_type=PriceType.UNKNOWN,
            provider="TripPilot category estimator",
            source="fx_unavailable",
        )
    return PriceEstimate(
        minimum=converted[0].converted_amount,
        expected=converted[1].converted_amount,
        maximum=converted[2].converted_amount,
        currency=display_currency,
        price_type=PriceType.ESTIMATED,
        provider="TripPilot category estimator",
        source=f"city_category:{(city or place.get('city') or 'global').casefold()}:{category}:level_{level}",
        confidence=0.55,
        provider_amount=estimated_usd[1],
        provider_currency="USD",
        exchange_rate=converted[1].exchange_rate,
        rate_timestamp=converted[1].rate_timestamp,
        fetched_at=datetime.now(UTC),
    )
