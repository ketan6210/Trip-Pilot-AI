from __future__ import annotations

from backend.geography.distance import distance_km


def validate_geography(places: list, max_leg_km: float = 75) -> None:
    for first, second in zip(places, places[1:]):
        if distance_km(first.latitude, first.longitude, second.latitude, second.longitude) > max_leg_km:
            raise ValueError(
                "Consecutive activities are too far apart for a city itinerary; choose a closer destination area"
            )


def travel_minutes_between(first, second, mode: str) -> int:
    kilometers = distance_km(first.latitude, first.longitude, second.latitude, second.longitude)
    speed = {"walking": 4.8, "public transit": 18, "mixed": 16, "taxi": 24, "rental car": 22}.get(mode, 16)
    transfer = 4 if mode == "walking" else 8
    return max(5, round(kilometers / speed * 60 + transfer))


def route_matrix(places: list, mode: str) -> dict[tuple[int, int], int]:
    return {
        (first.id, second.id): 0 if first.id == second.id else travel_minutes_between(first, second, mode)
        for first in places
        for second in places
    }


def nearest_neighbor_order(places: list, start=None) -> list:
    if not places:
        return []
    remaining = list(places)
    ordered = (
        [remaining.pop(0)]
        if start is None
        else [
            min(
                remaining,
                key=lambda place: distance_km(start.latitude, start.longitude, place.latitude, place.longitude),
            )
        ]
    )
    if start is not None:
        remaining.remove(ordered[0])
    while remaining:
        previous = ordered[-1]
        selected = min(
            remaining,
            key=lambda place: distance_km(previous.latitude, previous.longitude, place.latitude, place.longitude),
        )
        ordered.append(selected)
        remaining.remove(selected)
    return ordered
