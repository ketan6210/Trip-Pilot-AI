from abc import ABC, abstractmethod
from dataclasses import dataclass

from backend.models import Place


@dataclass(frozen=True)
class PlaceSearch:
    city: str
    category: str | None = None
    subcategory: str | None = None
    neighborhood: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    limit: int = 100
    external_provider: str | None = None


class PlaceProvider(ABC):
    """Provider-neutral place retrieval contract used by planning services."""

    @abstractmethod
    def search(self, query: PlaceSearch) -> list[Place]:
        raise NotImplementedError

    @abstractmethod
    def details(self, place_id: int) -> Place | None:
        raise NotImplementedError

    def enrich(self, place: Place) -> Place:
        """Optionally refresh expensive detail-only fields such as photos and ticket prices."""
        return place
