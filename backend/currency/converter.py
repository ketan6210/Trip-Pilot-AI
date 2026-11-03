from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite
from threading import Lock

import httpx
from pydantic import BaseModel

from backend.providers.http import request_with_retry
from backend.settings import settings


class ConversionResult(BaseModel):
    provider_amount: float
    provider_currency: str
    converted_amount: float
    display_currency: str
    exchange_rate: float
    rate_timestamp: datetime
    provider: str = "Frankfurter / ECB reference rate"


class CurrencyService:
    """Centralized, cached FX conversion. No LLM arithmetic is involved."""

    def __init__(self, ttl: timedelta = timedelta(hours=12)) -> None:
        self.ttl = ttl
        self._cache: dict[tuple[str, str], tuple[float, datetime]] = {}
        self._lock = Lock()

    def rate(self, source: str, target: str) -> tuple[float, datetime] | None:
        source, target = source.upper(), target.upper()
        now = datetime.now(UTC)
        if source == target:
            return 1.0, now
        key = (source, target)
        with self._lock:
            cached = self._cache.get(key)
        if cached and now - cached[1] <= self.ttl:
            return cached
        try:
            response = request_with_retry(
                lambda: httpx.get(
                    f"{settings.exchange_rate_base_url.rstrip('/')}/v2/rate/{source}/{target}",
                    params={"providers": "ECB"},
                    headers={"User-Agent": settings.trippilot_user_agent},
                    timeout=8.0,
                    trust_env=False,
                ),
                provider="Frankfurter",
            )
            response.raise_for_status()
            rate = float(response.json()["rate"])
            if rate <= 0 or not isfinite(rate):
                return None
            value = (rate, now)
            with self._lock:
                self._cache[key] = value
            return value
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def convert(self, amount: float, source: str, target: str) -> ConversionResult | None:
        found = self.rate(source, target)
        if not found:
            return None
        rate, timestamp = found
        return ConversionResult(
            provider_amount=float(amount),
            provider_currency=source.upper(),
            converted_amount=round(float(amount) * rate, 2),
            display_currency=target.upper(),
            exchange_rate=rate,
            rate_timestamp=timestamp,
        )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


currency_service = CurrencyService()
