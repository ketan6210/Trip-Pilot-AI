from statistics import mean

from backend.ai.schemas import RankedCandidate
from backend.embeddings.semantic_mapper import semantic_similarity
from backend.geography.distance import distance_km

STRATEGIES = {
    "balanced": {
        "max_stops": 3,
        "travel_penalty": 5,
        "cost_penalty": 1,
        "pace": "balanced",
        "description": "Balances each traveler's interests with cost and travel time.",
    },
    "budget": {
        "max_stops": 3,
        "travel_penalty": 7,
        "cost_penalty": 8,
        "pace": "balanced",
        "description": "Favors free and lower-cost activities with short transfers.",
    },
    "experience_heavy": {
        "max_stops": 4,
        "travel_penalty": 3,
        "cost_penalty": 0,
        "pace": "packed",
        "description": "Uses fuller days to cover more interests and highly ranked activities.",
    },
    "relaxed": {
        "max_stops": 2,
        "travel_penalty": 14,
        "cost_penalty": 1,
        "pace": "relaxed",
        "description": "Schedules fewer stops and places a stronger penalty on transfers.",
    },
}


def rank_candidates(candidates, travelers, constraints, strategy, *, votes=None, advice=None, seen=None):
    votes, advice, seen = votes or {}, advice or {}, seen or set()
    weights = constraints.ranking_weights.model_dump()
    if strategy == "budget":
        weights["price_fit"] *= 3
    elif strategy == "experience_heavy":
        weights["preference"] *= 1.5
        weights["novelty"] *= 2
    elif strategy == "relaxed":
        weights["distance"] *= 3
    max_price = max((item.cost_cents for item in candidates), default=1) or 1
    output = []
    for place in candidates:
        scores, semantic = [], []
        for traveler in travelers:
            mapping = {(p.category, p.subcategory): p.score / 10 for p in traveler.preferences if not p.is_avoid}
            score = mapping.get((place.category, place.subcategory), mapping.get((place.category, "general"), 0.35))
            if (
                traveler.budget_preference is not None
                and place.cost_cents / max(len(travelers), 1) > float(traveler.budget_preference) * 100
            ):
                score *= 0.5
            scores.append(score)
            if traveler.preference_text:
                semantic.append(
                    max(
                        0,
                        semantic_similarity(
                            traveler.preference_text, f"{place.category} {place.subcategory} {place.description}"
                        ),
                    )
                )
        components = {
            "preference": mean(scores) if scores else 0.5,
            "group_fit": min(scores) if scores else 0.5,
            "rating": min(1, max(0, (place.rating or 0) / 5)),
            "price_fit": 1 - place.cost_cents / max_price,
            "semantic": mean(semantic) if semantic else 0,
            "novelty": float(place.place_id not in seen),
            "votes": votes.get(place.place_id, 0),
            "time_fit": sum(bool(windows) for windows in place.opening_windows.values())
            / max(1, len(place.opening_windows)),
            "distance": min(
                1,
                distance_km(constraints.anchor_latitude, constraints.anchor_longitude, place.latitude, place.longitude)
                / constraints.max_leg_km,
            )
            if constraints.anchor_latitude is not None
            else 0,
        }
        score = sum(value * weights[key] * (-1 if key == "distance" else 1) for key, value in components.items())
        # LLM advice is a small, bounded soft signal; it cannot change provider facts.
        score += 0.05 * advice.get(place.place_id, 0)
        reasons = [key for key, value in components.items() if key != "distance" and value >= 0.65]
        output.append(
            RankedCandidate(
                candidate=place,
                score=round(score, 6),
                components=components,
                traveler_scores=scores,
                reason_codes=reasons or ["variety"],
            )
        )
    return sorted(output, key=lambda row: (-row.score, row.candidate.place_id))
