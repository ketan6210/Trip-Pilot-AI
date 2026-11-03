from dataclasses import dataclass


@dataclass(frozen=True)
class PlanProfile:
    key: str
    title: str
    tagline: str
    preference_weight: float
    fairness_weight: float
    quality_weight: float
    free_value_weight: float
    cost_penalty_weight: float
    activities_per_day: int
    pace: str
    walking_intensity: str
    minutes_per_transfer: int
    hotel_tier: str
    hotel_multiplier: float
    food_multiplier: float
    transport_multiplier: float
    nightlife_multiplier: float
    shopping_multiplier: float
    buffer_rate: float


PLAN_PROFILES = (
    PlanProfile(
        "balanced",
        "The Common Ground",
        "A balanced mix of highlights, local flavor, comfort, and value.",
        0.35,
        0.30,
        0.20,
        0.05,
        0.10,
        3,
        "balanced",
        "moderate",
        24,
        "midrange",
        1.0,
        1.0,
        1.0,
        1.0,
        0.7,
        0.08,
    ),
    PlanProfile(
        "experience_heavy",
        "The Big Experience",
        "More standout paid experiences and a fuller daily schedule.",
        0.50,
        0.15,
        0.34,
        0,
        0.01,
        4,
        "packed",
        "high",
        27,
        "upscale",
        1.25,
        1.25,
        1.15,
        1.35,
        1.0,
        0.10,
    ),
    PlanProfile(
        "budget",
        "The Smart Spend",
        "Free and high-value experiences with efficient local transport.",
        0.25,
        0.20,
        0.10,
        0.35,
        0.25,
        3,
        "balanced",
        "moderate",
        25,
        "value",
        0.72,
        0.70,
        0.68,
        0.65,
        0.45,
        0.06,
    ),
    PlanProfile(
        "relaxed",
        "Premium & Convenient",
        "Fewer transfers, premium lodging, dining, and door-to-door convenience.",
        0.30,
        0.20,
        0.34,
        0,
        0.02,
        2,
        "relaxed",
        "low",
        16,
        "premium",
        1.75,
        1.65,
        1.85,
        1.60,
        1.35,
        0.12,
    ),
)
