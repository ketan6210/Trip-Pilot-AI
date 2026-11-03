from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from backend.settings import settings


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    concepts = {
        "quiet": "peaceful",
        "calm": "peaceful",
        "tranquil": "peaceful",
        "relaxing": "peaceful",
        "greenery": "nature",
        "gardens": "nature",
        "garden": "nature",
        "parks": "nature",
        "park": "nature",
        "galleries": "museum",
        "gallery": "museum",
        "museums": "museum",
        "exhibits": "museum",
        "jazz": "music",
        "concert": "music",
        "concerts": "music",
        "bands": "music",
        "dining": "food",
        "cuisine": "food",
        "restaurants": "food",
        "restaurant": "food",
        "children": "family",
        "kids": "family",
        "kid": "family",
    }
    return [concepts.get(word, word) for word in words]


def _hashed_vector(text: str, dimensions: int = 384) -> list[float]:
    """Normalized concept/token vectors; not a trained language embedding."""
    vector = [0.0] * dimensions
    tokens = _tokens(text)
    features = [*tokens, *(f"{a}_{b}" for a, b in zip(tokens, tokens[1:]))]
    for feature in features:
        digest = hashlib.blake2b(feature.encode(), digest_size=4).digest()
        index = int.from_bytes(digest, "big") % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class PreferenceEncoder:
    """Lazy MiniLM encoder with a deterministic local fallback.

    The heavyweight model is optional so TripPilot remains runnable on Python
    versions without a compatible PyTorch wheel.
    """

    def __init__(self) -> None:
        self.model = None
        self.provider = "hashed_semantic_fallback"
        self._initialized = False

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        if settings.embedding_backend != "sentence_transformers":
            return
        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(settings.preference_embedding_model)
            self.provider = settings.preference_embedding_model
        except Exception:
            self.model = None

    @lru_cache(maxsize=1024)
    def encode(self, text: str) -> tuple[float, ...]:
        self._initialize()
        if self.model is not None:
            values = self.model.encode(text, normalize_embeddings=True).tolist()
            return tuple(float(value) for value in values)
        return tuple(_hashed_vector(text))


encoder = PreferenceEncoder()
