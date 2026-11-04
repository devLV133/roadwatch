"""Great-circle distance estimates; no map or routing service is needed."""

from math import asin, cos, radians, sin, sqrt


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(min(1.0, max(0.0, h))))
