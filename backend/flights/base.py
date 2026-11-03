from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FlightSearchRequest:
    origin: str
    destination: str
    departure_date: date
    return_date: date
    adults: int
    cabin: str = "economy"
    currency: str = "USD"

    @property
    def cache_key(self) -> tuple:
        return (
            self.origin.casefold(),
            self.destination.casefold(),
            self.departure_date,
            self.return_date,
            self.adults,
            self.cabin.casefold(),
            self.currency.upper(),
        )


class FlightProvider(ABC):
    name: str

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def search(self, request: FlightSearchRequest) -> dict | None: ...
