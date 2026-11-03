from statistics import mean, pvariance


def fairness_metrics(satisfactions: list[float]) -> dict[str, float]:
    """Return transparent group statistics and the roadmap's fairness-aware score."""
    if not satisfactions:
        satisfactions = [0.5]
    average = mean(satisfactions)
    minimum = min(satisfactions)
    variance = pvariance(satisfactions)
    score = 0.60 * average + 0.25 * minimum - 0.15 * variance
    return {
        "mean": round(average, 4),
        "minimum": round(minimum, 4),
        "variance": round(variance, 4),
        "score": round(max(0.0, min(1.0, score)), 4),
    }
