from __future__ import annotations


def schedule_objective(
    *,
    preference: float,
    quality: float,
    fairness: float,
    diversity: float,
    travel_minutes: int,
    idle_minutes: int,
    fatigue_minutes: int,
    cost_penalty: float,
) -> float:
    """Auditable weighted objective for candidate schedule comparison."""
    return round(
        0.30 * preference
        + 0.20 * quality
        + 0.20 * fairness
        + 0.10 * diversity
        - 0.0008 * travel_minutes
        - 0.0005 * idle_minutes
        - 0.0004 * fatigue_minutes
        - 0.20 * cost_penalty,
        6,
    )
