from __future__ import annotations

from backend.geography.distance import distance_km


def dbscan_clusters(places: list, epsilon_km: float = 2.5, min_samples: int = 1) -> list[list]:
    """Small dependency-free DBSCAN implementation for geographic day clustering."""
    if not places:
        return []
    unvisited = set(range(len(places)))
    clusters: list[list] = []
    while unvisited:
        seed = min(unvisited)
        unvisited.remove(seed)
        neighbors = {
            index
            for index in range(len(places))
            if distance_km(
                places[seed].latitude, places[seed].longitude, places[index].latitude, places[index].longitude
            )
            <= epsilon_km
        }
        if len(neighbors) < min_samples:
            clusters.append([places[seed]])
            continue
        cluster_indices = {seed}
        frontier = list(neighbors - {seed})
        while frontier:
            index = frontier.pop()
            if index in cluster_indices:
                continue
            cluster_indices.add(index)
            unvisited.discard(index)
            local = {
                other
                for other in range(len(places))
                if distance_km(
                    places[index].latitude, places[index].longitude, places[other].latitude, places[other].longitude
                )
                <= epsilon_km
            }
            if len(local) >= min_samples:
                frontier.extend(local - cluster_indices)
        clusters.append([places[index] for index in sorted(cluster_indices)])
    return clusters


def assign_clusters_to_days(places: list, day_count: int) -> list[list]:
    days: list[list] = [[] for _ in range(day_count)]
    clusters = sorted(dbscan_clusters(places), key=len, reverse=True)
    for cluster in clusters:
        for place in cluster:
            target = min(range(day_count), key=lambda index: (len(days[index]), index))
            days[target].append(place)
    return days
