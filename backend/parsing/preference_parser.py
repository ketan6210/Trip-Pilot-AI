from __future__ import annotations

import re

from pydantic import BaseModel, Field

from backend.embeddings.semantic_mapper import semantic_category
from backend.settings import settings

CANONICAL_CATEGORIES = (
    "museum",
    "nightlife",
    "food",
    "nature",
    "shopping",
    "landmarks",
    "history",
    "adventure",
    "relaxation",
    "photography",
    "entertainment",
    "sports",
    "architecture",
    "culture",
    "religious",
    "waterfront",
    "family",
    "local_experience",
    "outdoor",
    "music",
    "theater",
)
PLANNABLE_CATEGORIES = (
    "museum",
    "nightlife",
    "food",
    "nature",
    "shopping",
    "landmarks",
    "history",
    "entertainment",
    "sports",
)
CATEGORY_PATTERNS = {
    "museum": (r"museums?", r"musems?", r"mesuems?", r"galler(?:y|ies)", r"exhibitions?"),
    "nightlife": (r"night\s*life", r"nightclubs?", r"clubs?", r"pubs?", r"bars?", r"cocktails?", r"part(?:y|ies|ying)"),
    "food": (r"food", r"restaurants?", r"dining", r"cuisine", r"caf(?:e|es|é|és)", r"street food"),
    "nature": (
        r"nature",
        r"greenery",
        r"scenic",
        r"parks?",
        r"gardens?",
        r"hikes?",
        r"hiking",
        r"trails?",
        r"forests?",
        r"beaches?",
    ),
    "shopping": (r"shopping", r"shops?", r"markets?", r"boutiques?"),
    "landmarks": (r"landmarks?", r"monuments?", r"sightseeing", r"must[- ]see"),
    "history": (r"history", r"historic(?:al)?", r"histroic(?:al)?", r"heritage"),
    "adventure": (r"adventure", r"thrills?", r"rafting", r"climbing"),
    "relaxation": (r"relax(?:ation|ing)?", r"wellness", r"spas?", r"quiet"),
    "photography": (r"photograph(?:y|ic)", r"photo spots?", r"pictures?"),
    "entertainment": (r"entertainment", r"shows?", r"cinema", r"comedy"),
    "sports": (r"sports?", r"games?", r"stadiums?"),
    "architecture": (r"architecture", r"buildings?", r"design"),
    "culture": (r"culture", r"cultural", r"traditions?"),
    "religious": (r"religious", r"temples?", r"churches?", r"mosques?", r"sacred"),
    "waterfront": (r"waterfront", r"harbou?r", r"riverside", r"lakeside"),
    "family": (r"family", r"kids?", r"children"),
    "local_experience": (
        r"local life",
        r"local experience",
        r"local markets?",
        r"neighbou?rhood walks?",
        r"street culture",
        r"community events?",
        r"not touristy",
        r"non[- ]touristy",
    ),
    "outdoor": (r"outdoors?", r"open air"),
    "music": (r"music", r"concerts?", r"jazz", r"bands?"),
    "theater": (r"theat(?:er|re)", r"plays?", r"musicals?"),
}
SUBCATEGORIES = {
    "nature": ("parks", "gardens", "hiking", "waterfront", "viewpoints", "beaches", "trails", "forests", "lakes"),
    "nightlife": (
        "nightclub",
        "rooftop",
        "cocktail_bar",
        "live_music",
        "jazz",
        "comedy",
        "pub",
        "sports_bar",
        "late_night_food",
    ),
    "museum": ("modern_art", "classical_art", "history", "science", "natural_history", "specialty"),
    "local_experience": (
        "markets",
        "neighborhood_walks",
        "local_food",
        "street_culture",
        "community_events",
        "local_shopping",
    ),
}
PLANNER_CATEGORY = {
    "museum": "museums",
    "outdoor": "nature",
    "waterfront": "nature",
    "music": "entertainment",
    "theater": "entertainment",
    "architecture": "landmarks",
    "culture": "history",
    "religious": "history",
    "local_experience": "food",
}


class Preference(BaseModel):
    raw_text: str
    canonical_category: str | None = None
    subcategories: list[str] = Field(default_factory=list)
    weight: float = Field(default=0.8, ge=0, le=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    is_custom: bool = False

    @property
    def planner_category(self) -> str | None:
        if not self.canonical_category:
            return None
        if self.canonical_category == "nature" and "parks" in self.subcategories:
            return "parks"
        return PLANNER_CATEGORY.get(self.canonical_category, self.canonical_category)


def _subcategories(category: str, text: str) -> list[str]:
    lowered = text.casefold().replace("-", "_").replace(" ", "_")
    return [
        item for item in SUBCATEGORIES.get(category, ()) if item in lowered or item.replace("_", " ") in text.casefold()
    ]


def _weight_context(text: str, start: int) -> float:
    before = text.casefold()[max(0, start - 45) : start]
    if re.search(r"(?:mostly|especially|more|love|must|priority|really like)\s*$", before):
        return 1.0
    if re.search(r"(?:some|maybe|open to|a little)\s*$", before):
        return 0.6
    return 0.8


def parse_preferences(text: str, offered_categories: tuple[str, ...] = PLANNABLE_CATEGORIES) -> list[Preference]:
    normalized = " ".join(text.strip().split())
    lowered = normalized.casefold()
    results: dict[str, Preference] = {}
    means_all = bool(
        re.search(
            r"\b(?:all of them|all of those|all activities|all categories|everything|open to everything|bit of everything)\b",
            lowered,
        )
    )
    if means_all:
        for category in offered_categories:
            results[category] = Preference(
                raw_text=normalized, canonical_category=category, weight=0.7, confidence=0.95
            )

    for category, patterns in CATEGORY_PATTERNS.items():
        matches = [match for pattern in patterns if (match := re.search(rf"\b(?:{pattern})\b", lowered))]
        if not matches:
            continue
        first = min(matches, key=lambda match: match.start())
        before = lowered[max(0, first.start() - 35) : first.start()]
        remove = bool(re.search(r"(?:\bno\b|avoid|not into|don't like|do not like)\s*$", before))
        weight = 0 if remove else _weight_context(lowered, first.start())
        results[category] = Preference(
            raw_text=normalized,
            canonical_category=category,
            subcategories=_subcategories(category, lowered),
            weight=weight,
            confidence=0.96,
        )

    if results:
        return list(results.values())

    category, similarity = semantic_category(normalized)
    if category and similarity >= settings.preference_medium_similarity:
        return [
            Preference(
                raw_text=normalized,
                canonical_category=category,
                subcategories=_subcategories(category, lowered),
                weight=0.8 if similarity >= settings.preference_high_similarity else 0.7,
                confidence=min(0.95, 0.55 + similarity),
            )
        ]
    if normalized:
        return [Preference(raw_text=normalized, weight=0.7, confidence=max(0.25, similarity), is_custom=True)]
    return []
