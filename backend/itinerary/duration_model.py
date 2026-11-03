from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DurationEstimate:
    minimum: int
    expected: int
    maximum: int
    source: str = "deterministic_category_prior"


PRIORS: dict[str, tuple[int, int, int]] = {
    "small_museum": (60, 90, 120),
    "museum": (90, 150, 210),
    "large_museum": (150, 210, 300),
    "park": (75, 120, 180),
    "large_park": (120, 180, 240),
    "garden": (60, 90, 150),
    "hiking": (210, 300, 390),
    "major_hike": (300, 420, 540),
    "trail": (150, 240, 360),
    "nightlife": (120, 180, 270),
    "nightclub": (150, 210, 300),
    "pub": (90, 150, 240),
    "live_music": (120, 180, 240),
    "jazz": (120, 180, 240),
    "comedy": (90, 120, 165),
    "restaurant": (60, 90, 120),
    "cafe": (45, 60, 90),
    "food": (60, 90, 120),
    "landmark": (45, 60, 90),
    "viewpoint": (45, 60, 90),
    "shopping": (75, 120, 180),
    "show": (120, 150, 210),
    "theatre": (120, 150, 210),
    "cinema": (120, 150, 210),
    "concert": (150, 180, 270),
    "market": (75, 120, 180),
    "neighborhood_walk": (90, 150, 210),
    "sports": (120, 180, 240),
    "history": (60, 90, 150),
    "entertainment": (90, 150, 210),
}


def _kind(category: str, subcategory: str, name: str = "", tags: list[str] | None = None) -> str:
    text = " ".join((category, subcategory, name, *(tags or []))).casefold().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("major_hike", "full_day_hike", "mountain_trek")):
        return "major_hike"
    if any(token in text for token in ("hiking", "hike")):
        return "hiking"
    if "trail" in text:
        return "trail"
    if category in {"museums", "museum"}:
        if any(token in text for token in ("metropolitan", "national_museum", "natural_history", "large")):
            return "large_museum"
        if "small" in text or "specialty" in text:
            return "small_museum"
        return "museum"
    if category == "nature":
        if "large_park" in text or "national_park" in text:
            return "large_park"
        if "garden" in text:
            return "garden"
        return "park"
    if category == "nightlife":
        for item in ("nightclub", "pub", "jazz", "live_music", "comedy"):
            if item in text:
                return item
        return "nightlife"
    if category == "food":
        if "market" in text:
            return "market"
        if "cafe" in text:
            return "cafe"
        return "restaurant"
    if category == "landmarks":
        return "viewpoint" if "viewpoint" in text else "landmark"
    if category == "shopping":
        return "market" if "market" in text else "shopping"
    if category == "entertainment":
        for item in ("concert", "theatre", "cinema", "comedy"):
            if item in text:
                return item
        return "show"
    return category if category in PRIORS else "landmark"


def estimate_duration(
    category: str,
    subcategory: str,
    *,
    name: str = "",
    tags: list[str] | None = None,
    interest: float = 0.5,
    pace: str = "balanced",
) -> DurationEstimate:
    minimum, expected, maximum = PRIORS[_kind(category, subcategory, name, tags)]
    if interest >= 0.8:
        expected += 45
    elif interest <= 0.3:
        expected -= 20
    if pace in {"packed", "experience_heavy"}:
        expected -= 20
    elif pace == "relaxed":
        expected += 20
    expected = max(minimum, min(maximum, int(round(expected / 5) * 5)))
    return DurationEstimate(minimum, expected, maximum)
