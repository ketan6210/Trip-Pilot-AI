from __future__ import annotations

from math import sqrt

from backend.embeddings.preference_encoder import encoder
from backend.settings import settings

CATEGORY_DESCRIPTIONS = {
    "museum": "museums galleries exhibits art science natural history specialty collections",
    "nightlife": "nightclubs bars rooftops cocktails live music jazz comedy pubs late night entertainment",
    "food": "restaurants cuisine local food street food markets cafes dining lunch dinner",
    "nature": "nature greenery quiet parks hiking forests gardens scenic views waterfront trails beaches lakes",
    "shopping": "shopping boutiques malls local markets crafts stores",
    "landmarks": "landmarks monuments famous sights viewpoints sightseeing",
    "history": "history heritage historic sites archaeology old city",
    "adventure": "adventure hiking climbing rafting active outdoors thrills",
    "relaxation": "relaxation wellness spa quiet slow peaceful rest",
    "photography": "photography scenic photo spots viewpoints architecture street scenes",
    "entertainment": "shows cinema comedy concerts performances attractions",
    "sports": "sports games stadiums arenas active recreation",
    "architecture": "architecture buildings design historic structures modern structures",
    "culture": "culture traditions arts heritage neighborhoods customs",
    "religious": "religious temples churches mosques synagogues sacred spiritual",
    "waterfront": "waterfront river lake harbor beach coastal promenade",
    "family": "family children kid friendly zoo aquarium play",
    "local_experience": "local life markets neighborhood walks local food street culture community events local shopping",
    "outdoor": "outdoors parks trails hiking open air nature active",
    "music": "music concerts jazz live bands performances clubs",
    "theater": "theater theatre plays musicals stage performances",
}


def semantic_similarity(left: str, right: str) -> float:
    first, second = encoder.encode(left), encoder.encode(right)
    denominator = sqrt(sum(value * value for value in first)) * sqrt(sum(value * value for value in second))
    return sum(a * b for a, b in zip(first, second)) / denominator if denominator else 0.0


def semantic_category(text: str) -> tuple[str | None, float]:
    scores = [
        (category, semantic_similarity(text, description)) for category, description in CATEGORY_DESCRIPTIONS.items()
    ]
    category, score = max(scores, key=lambda item: item[1])
    threshold = settings.preference_medium_similarity
    if encoder.provider == "hashed_semantic_fallback":
        # Hash collisions are noisier than transformer embeddings. Direct
        # keyword matches run before this path, so a conservative threshold
        # prevents ordinary trip prose from becoming a false preference.
        threshold = max(threshold, 0.34)
    return (category, round(score, 4)) if score >= threshold else (None, round(score, 4))
