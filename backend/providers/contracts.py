"""Small provider contracts around existing integrations, with explicit estimates."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from backend.flights.base import FlightProvider, FlightSearchRequest
from backend.providers.base import PlaceProvider

PlacesProvider = PlaceProvider


class HotelOption(BaseModel):
    name: str
    total: Decimal | None
    currency: str
    source: str
    price_type: str


class HotelProvider(Protocol):
    def search(self, destination: str, start: date, end: date, travelers: int, currency: str) -> list[HotelOption]: ...


class CurrencyProvider(Protocol):
    def convert(self, amount: float, source: str, target: str): ...


class MapsProvider(Protocol):
    def route(self, first, second, trip) -> dict: ...


class ExistingFlightProvider(FlightProvider):
    name = "Configured flight providers"

    @property
    def configured(self) -> bool:
        from backend.settings import settings

        return bool(settings.skyscanner_api_key or (settings.amadeus_client_id and settings.amadeus_client_secret))

    def search(self, request: FlightSearchRequest) -> dict:
        from backend.providers.flights import search_flights

        return search_flights(request)


class ExistingMapsProvider:
    def route(self, first, second, trip) -> dict:
        from backend.transport.service import choose_transport_leg

        return choose_transport_leg(first, second, trip)


class UnavailableHotelProvider:
    """No booking integration is configured; never manufacture hotel inventory."""

    def search(self, destination: str, start: date, end: date, travelers: int, currency: str) -> list[HotelOption]:
        return []
