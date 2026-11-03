from __future__ import annotations

from dataclasses import dataclass
from datetime import time


def to_minutes(value: time | str | None, fallback: int) -> int:
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, str):
        try:
            hour, minute = value.split(":", 1)
            return int(hour) * 60 + int(minute[:2])
        except (ValueError, TypeError):
            pass
    return fallback


@dataclass(frozen=True)
class TimeWindow:
    label: str
    preferred_start: int
    earliest: int
    latest_end: int


def best_time(category: str, subcategory: str = "", name: str = "") -> TimeWindow:
    text = f"{subcategory} {name}".casefold()
    if any(token in text for token in ("hike", "trail", "trek")):
        return TimeWindow("early morning", 7 * 60, 6 * 60, 18 * 60)
    if category in {"museums", "history"}:
        return TimeWindow("morning or afternoon", 10 * 60, 9 * 60, 18 * 60)
    if category == "nature":
        if "sunset" in text or "viewpoint" in text:
            return TimeWindow("sunset", 17 * 60, 15 * 60, 21 * 60)
        return TimeWindow("morning or afternoon", 9 * 60 + 30, 6 * 60 + 30, 20 * 60)
    if category == "food":
        return TimeWindow("meal time", 12 * 60 + 30, 8 * 60, 22 * 60 + 30)
    if category == "nightlife":
        return TimeWindow("late evening", 20 * 60 + 30, 18 * 60, 26 * 60)
    if category == "entertainment":
        return TimeWindow("evening", 19 * 60, 17 * 60, 24 * 60)
    if category == "shopping":
        return TimeWindow("late morning or afternoon", 11 * 60, 10 * 60, 20 * 60)
    return TimeWindow("daytime", 10 * 60, 8 * 60, 20 * 60)


def day_bounds(
    profile_key: str,
    first_category: str,
    first_subcategory: str = "",
    preferred_start: time | str | None = None,
    preferred_end: time | str | None = None,
    previous_end: int | None = None,
) -> tuple[int, int]:
    starts = {"balanced": 9 * 60 + 15, "experience_heavy": 7 * 60 + 45, "budget": 9 * 60, "relaxed": 10 * 60 + 30}
    ends = {"balanced": 23 * 60, "experience_heavy": 26 * 60, "budget": 22 * 60 + 30, "relaxed": 22 * 60}
    start = to_minutes(preferred_start, starts.get(profile_key, 9 * 60 + 15))
    end = to_minutes(preferred_end, ends.get(profile_key, 23 * 60))
    if end < start:
        end += 24 * 60
    text = f"{first_category} {first_subcategory}".casefold()
    if any(token in text for token in ("hike", "trail", "trek")):
        start = min(start, 6 * 60 + 30)
    elif first_category == "nightlife":
        start = max(start, 11 * 60)
    if previous_end is not None and previous_end >= 25 * 60 + 30 and profile_key != "experience_heavy":
        start = max(start, 8 * 60 + 30)
    return start, end
