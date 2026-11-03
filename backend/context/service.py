from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.models import Place, PlaceContext, PlaceContextSource
from backend.settings import settings

CATEGORY_VISIT_COPY = {
    "museums": "See the collections and exhibitions identified by the venue provider.",
    "history": "Explore a provider-classified historic place in its original geographic setting.",
    "landmarks": "See a recognizable destination feature and its surrounding neighborhood.",
    "nature": "Spend time outdoors and experience this part of the city at a slower pace.",
    "nightlife": "Experience an evening venue in the neighborhood where it operates.",
    "food": "Use this stop as a practical meal break near the day's route.",
    "entertainment": "Attend or explore the entertainment experience offered at this venue.",
    "shopping": "Browse the shops or market represented by this provider listing.",
    "sports": "Experience the sports venue or activity represented by this provider listing.",
}


def _wikipedia_extract(place: Place) -> tuple[str | None, str | None]:
    if place.external_provider != "openstreetmap":
        return None, None
    tags = (place.source_metadata or {}).get("osm_tags", {})
    wikipedia = str(tags.get("wikipedia") or "")
    title = wikipedia.split(":", 1)[-1] if wikipedia else None
    if not title:
        return None, None
    try:
        response = httpx.get(
            settings.wikimedia_api_url,
            params={
                "action": "query",
                "format": "json",
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "redirects": 1,
                "titles": title,
            },
            headers={"User-Agent": settings.trippilot_user_agent, "Accept-Language": "en"},
            timeout=12.0,
            trust_env=False,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        extract = " ".join(str(page.get("extract") or "").split())
        return (extract[:1400] or None), (
            page.get("fullurl") or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        )
    except Exception:
        return None, None


def get_or_create_place_context(db: Session, place: Place, refresh: bool = False) -> PlaceContext:
    existing = db.scalar(
        select(PlaceContext).where(PlaceContext.place_id == place.id).options(selectinload(PlaceContext.sources))
    )
    if existing and not refresh:
        return existing
    if existing:
        db.delete(existing)
        db.flush()

    metadata = place.source_metadata or {}
    tags = metadata.get("osm_tags", {})
    extract, wikipedia_url = _wikipedia_extract(place)
    generic_description = place.description.startswith("Live OpenStreetMap listing")
    short_description = (
        extract
        or ("" if generic_description else place.description)
        or (f"{place.name} is listed as {place.subcategory.replace('_', ' ')} in {place.neighborhood}, {place.city}.")
    )
    why_visit = [
        CATEGORY_VISIT_COPY.get(place.category, "Explore this exact provider-listed place and its neighborhood.")
    ]
    known_for = list(
        dict.fromkeys(
            item.replace("_", " ") for item in [place.subcategory, *place.tags] if item and item != place.category
        )
    )[:6]
    history = None
    historic_type = tags.get("historic")
    if historic_type:
        history = f"OpenStreetMap classifies this site as {str(historic_type).replace('_', ' ')}."
    architecture = None
    architecture_bits = [tags.get("architectural_style"), tags.get("architect"), tags.get("building:architecture")]
    architecture_bits = [str(item) for item in architecture_bits if item]
    if architecture_bits:
        architecture = "Provider-listed architecture information: " + "; ".join(architecture_bits) + "."
    facts = []
    for key, label in (
        ("start_date", "Provider-listed date"),
        ("operator", "Operator"),
        ("heritage", "Heritage designation"),
    ):
        if tags.get(key):
            facts.append(f"{label}: {tags[key]}.")
    cuisines = [item for item in str(tags.get("cuisine") or "").split(";") if item]
    cultural = f"Provider-listed cuisine: {', '.join(cuisines)}." if cuisines else None
    focus = [
        f"Allow enough time for the planned {place.duration_expected}-minute visit.",
        f"Explore the surrounding {place.neighborhood} area without adding a cross-city detour.",
    ]
    context = PlaceContext(
        place_id=place.id,
        short_description=short_description,
        why_visit=why_visit,
        known_for=known_for,
        historical_significance=history,
        cultural_significance=cultural,
        architectural_significance=architecture,
        famous_people=[],
        notable_events=[],
        movie_tv_connections=[],
        pop_culture_connections=[],
        local_tips=[],
        interesting_facts=facts,
        best_experience=CATEGORY_VISIT_COPY.get(place.category),
        recommended_visit_focus=focus,
        last_verified_at=datetime.now(UTC),
    )
    db.add(context)
    db.flush()
    osm_url = metadata.get("osm_url") or (
        f"https://www.openstreetmap.org/{place.external_place_id}"
        if place.external_provider == "openstreetmap"
        else place.maps_url
    )
    if osm_url:
        context.sources.append(
            PlaceContextSource(
                source_type="provider",
                source_name="OpenStreetMap"
                if place.external_provider == "openstreetmap"
                else place.external_provider.title(),
                source_url=osm_url,
                claim_types=["identity", "category", "location", "provider_tags"],
            )
        )
    if wikipedia_url and extract:
        context.sources.append(
            PlaceContextSource(
                source_type="structured_knowledge",
                source_name="Wikipedia",
                source_url=wikipedia_url,
                claim_types=["short_description"],
            )
        )
    if place.official_url:
        context.sources.append(
            PlaceContextSource(
                source_type="official",
                source_name=f"{place.name} official site",
                source_url=place.official_url,
                claim_types=["official_identity", "visitor_information"],
            )
        )
    db.commit()
    db.refresh(context)
    return db.scalar(
        select(PlaceContext).where(PlaceContext.id == context.id).options(selectinload(PlaceContext.sources))
    )


def serialize_place_context(context: PlaceContext) -> dict:
    return {
        "place_id": context.place_id,
        "short_description": context.short_description,
        "why_visit": context.why_visit,
        "known_for": context.known_for,
        "historical_significance": context.historical_significance,
        "cultural_significance": context.cultural_significance,
        "architectural_significance": context.architectural_significance,
        "famous_people": context.famous_people,
        "notable_events": context.notable_events,
        "movie_tv_connections": context.movie_tv_connections,
        "pop_culture_connections": context.pop_culture_connections,
        "local_tips": context.local_tips,
        "interesting_facts": context.interesting_facts,
        "best_experience": context.best_experience,
        "recommended_visit_focus": context.recommended_visit_focus,
        "source_records": [
            {
                "source_type": source.source_type,
                "source_name": source.source_name,
                "source_url": source.source_url,
                "claim_types": source.claim_types,
                "retrieved_at": source.retrieved_at.isoformat(),
            }
            for source in context.sources
        ],
        "last_verified_at": context.last_verified_at.isoformat(),
    }
