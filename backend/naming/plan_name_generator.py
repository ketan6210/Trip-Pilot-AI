from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.settings import settings


class DisplayName(BaseModel):
    plan_type: str
    title: str = Field(min_length=3, max_length=60)
    subtitle: str = Field(min_length=3, max_length=140)


class NamingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_type: Literal["balanced", "experience_heavy", "budget", "relaxed"]
    emphasis: Literal["interests", "destination"]


class DisplayNameBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[NamingChoice] = Field(min_length=4, max_length=4)


PROFILE_WORDS = {
    "balanced": ("Balanced Explorer", "A well-rounded route with variety and breathing room."),
    "experience_heavy": ("Experience Route", "A fuller route built around standout experiences."),
    "budget": ("Smart Spend", "High-value stops that protect the overall trip budget."),
    "relaxed": ("Premium & Convenient", "Premium comfort, fewer transfers, and more breathing room."),
}


def _destination_label(destination: str) -> str:
    aliases = {"new york city": "New York", "nyc": "New York", "new delhi": "Delhi"}
    return aliases.get(destination.casefold(), destination.title())


def _deterministic_names(destination: str, preferences: list[str]) -> dict[str, DisplayName]:
    label = _destination_label(destination)
    focus = [item.replace("_", " ").title() for item in preferences[:2]]
    focus_text = " & ".join(focus)
    result = {}
    for plan_type, (suffix, subtitle) in PROFILE_WORDS.items():
        title = f"{label} {suffix}"
        if focus_text:
            subtitle = f"{focus_text} shape this {subtitle[0].lower()}{subtitle[1:]}"
        result[plan_type] = DisplayName(plan_type=plan_type, title=title, subtitle=subtitle)
    return result


def _llm_names(destination: str, preferences: list[str], neighborhoods: dict[str, list[str]]) -> dict[str, DisplayName]:
    from backend.llm.service import structured

    batch = structured(
        DisplayNameBatch,
        [
            {
                "role": "system",
                "content": "Choose whether each of the four strategy labels should emphasize the supplied interests or destination. Return one choice for each strategy. Display text is rendered from verified templates; do not supply prose or factual claims.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"destination": destination, "preferences": preferences, "neighborhoods": neighborhoods}
                ),
            },
        ],
        purpose="display_names",
    )
    focused = _deterministic_names(destination, preferences)
    general = _deterministic_names(destination, [])
    result = {
        item.plan_type: (focused if item.emphasis == "interests" else general)[item.plan_type] for item in batch.names
    }
    if set(result) != set(PROFILE_WORDS):
        raise ValueError("Naming response omitted a strategy")
    return result


def generate_plan_names(
    destination: str, preferences: list[str], neighborhoods: dict[str, list[str]]
) -> dict[str, DisplayName]:
    """Use the configured main LLM once, with destination-aware safe fallbacks."""
    if settings.orchestrator_provider.strip().lower() in {"ollama", "openai", "auto"}:
        try:
            return _llm_names(destination, preferences, neighborhoods)
        except Exception:
            from backend.llm.telemetry import active_trace

            if trace := active_trace.get():
                trace.fallback = True
    return _deterministic_names(destination, preferences)
