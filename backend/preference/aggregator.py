from collections import defaultdict

from backend.models import Traveler


def traveler_preference_maps(travelers: list[Traveler]) -> dict[int, dict[tuple[str, str], float]]:
    """Normalize each traveler's 0–10 preference scores to 0–1 lookup maps."""
    result: dict[int, dict[tuple[str, str], float]] = {}
    for traveler in travelers:
        preferences: dict[tuple[str, str], list[float]] = defaultdict(list)
        for item in traveler.preferences:
            score = 0.0 if item.is_avoid else item.score / 10
            preferences[(item.category.lower(), item.subcategory.lower())].append(score)
            preferences[(item.category.lower(), "general")].append(score)
        result[traveler.id] = {key: sum(values) / len(values) for key, values in preferences.items()}
    return result


def preference_for_place(preferences: dict[tuple[str, str], float], place: dict) -> float:
    key = (place["category"].lower(), place["subcategory"].lower())
    return preferences.get(key, preferences.get((key[0], "general"), 0.5))
