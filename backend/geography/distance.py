from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


def distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    lat_a, lat_b = radians(latitude_a), radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = radians(longitude_b - longitude_a)
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))
