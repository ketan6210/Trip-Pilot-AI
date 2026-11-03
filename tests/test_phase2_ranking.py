from types import SimpleNamespace

from backend.ai.ranking import rank_candidates
from backend.ai.schemas import PlanningConstraints
from backend.embeddings.semantic_mapper import semantic_similarity
from tests.test_phase2_constraints import fixture_candidate


def traveler(category, text=""):
    return SimpleNamespace(
        preferences=[SimpleNamespace(category=category, subcategory="general", score=10, is_avoid=False)],
        preference_text=text,
        budget_preference=None,
    )


def test_group_ranking_is_symmetric_and_vote_components_are_exposed():
    candidates = [fixture_candidate(1, "museums"), fixture_candidate(2, "nature")]
    people = [traveler("museums"), traveler("nature")]
    first = rank_candidates(candidates, people, PlanningConstraints(), "balanced")
    second = rank_candidates(candidates, list(reversed(people)), PlanningConstraints(), "balanced")
    assert [row.score for row in first] == [row.score for row in second]
    voted = rank_candidates(candidates, people, PlanningConstraints(), "balanced", votes={2: 1})
    assert voted[0].candidate.place_id == 2
    assert voted[0].components["votes"] == 1
    assert set(voted[0].components) == {
        "preference",
        "group_fit",
        "rating",
        "price_fit",
        "semantic",
        "novelty",
        "votes",
        "time_fit",
        "distance",
    }


def test_semantic_concepts_and_configurable_weights_affect_ranking():
    assert semantic_similarity("quiet greenery", "peaceful parks") > semantic_similarity(
        "quiet greenery", "loud nightlife shopping"
    )
    candidates = [fixture_candidate(1, "museums", cost=5000), fixture_candidate(2, "nature", cost=0)]
    weights = PlanningConstraints().ranking_weights.model_copy(update={"price_fit": 5})
    ranked = rank_candidates(
        candidates, [traveler("museums")], PlanningConstraints(ranking_weights=weights), "balanced"
    )
    assert ranked[0].candidate.place_id == 2
