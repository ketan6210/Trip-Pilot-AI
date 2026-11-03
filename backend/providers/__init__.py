from sqlalchemy.orm import Session

from backend.providers.base import PlaceProvider
from backend.settings import settings


def get_place_provider(db: Session) -> PlaceProvider:
    from backend.providers.live import LivePlaceProvider
    from backend.providers.mock import DatabasePlaceProvider

    provider = settings.place_provider.strip().lower()
    if provider == "live":
        return LivePlaceProvider(db)
    if provider == "mock":
        return DatabasePlaceProvider(db)
    raise ValueError(f"Unsupported PLACE_PROVIDER: {settings.place_provider}")


__all__ = ["PlaceProvider", "get_place_provider"]
