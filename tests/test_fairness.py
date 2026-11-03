from backend.preference.fairness import fairness_metrics


def test_fairness_score_penalizes_leaving_one_traveler_behind():
    equitable = fairness_metrics([0.7, 0.7, 0.7])
    uneven = fairness_metrics([1.0, 1.0, 0.1])
    assert equitable["score"] > uneven["score"]
    assert uneven == {"mean": 0.7, "minimum": 0.1, "variance": 0.18, "score": 0.418}
