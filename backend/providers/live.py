from __future__ import annotations

import html
import ipaddress
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.errors import AppError
from backend.geography.maps import google_maps_place_url
from backend.models import Place
from backend.providers.base import PlaceProvider, PlaceSearch
from backend.providers.currency import convert_amount
from backend.providers.google_places import enrich_from_google
from backend.providers.http import ProviderError, request_with_retry
from backend.providers.mock import DatabasePlaceProvider
from backend.settings import settings

CATEGORY_DURATION = {
    "museums": 120,
    "nightlife": 120,
    "food": 90,
    "nature": 90,
    "landmarks": 75,
    "history": 90,
    "entertainment": 120,
    "shopping": 90,
    "sports": 120,
}
CURRENCY_BY_COUNTRY = {
    "gb": "GBP",
    "uk": "GBP",
    "us": "USD",
    "ca": "CAD",
    "au": "AUD",
    "jp": "JPY",
    "in": "INR",
    "ch": "CHF",
    "se": "SEK",
    "no": "NOK",
    "dk": "DKK",
    "pl": "PLN",
    "cz": "CZK",
}
SYMBOL_CURRENCY = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY", "₹": "INR"}
COUNTRY_NAME_CURRENCY = {
    "india": "INR",
    "united states": "USD",
    "united kingdom": "GBP",
    "canada": "CAD",
    "australia": "AUD",
    "japan": "JPY",
    "switzerland": "CHF",
}
CITY_CURRENCY = {"delhi": "INR", "new delhi": "INR", "varanasi": "INR", "mumbai": "INR", "london": "GBP"}


def _local_currency(place: Place) -> str:
    return COUNTRY_NAME_CURRENCY.get(place.country.casefold()) or CITY_CURRENCY.get(place.city.casefold()) or "USD"


class _TicketLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.current_href: str | None = None
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self.current_href and data.strip():
            self.links.append((data.strip(), urljoin(self.base_url, self.current_href)))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self.current_href = None


def _category(tags: dict[str, str]) -> tuple[str, str] | None:
    tourism, amenity, leisure, historic, shop = (
        tags.get("tourism", ""),
        tags.get("amenity", ""),
        tags.get("leisure", ""),
        tags.get("historic", ""),
        tags.get("shop", ""),
    )
    if tourism in {"museum", "gallery"}:
        return "museums", tourism
    if amenity in {"nightclub", "bar", "pub", "biergarten"}:
        return "nightlife", amenity
    if amenity in {"restaurant", "cafe", "food_court", "ice_cream"}:
        return "food", amenity
    if amenity in {"theatre", "cinema", "arts_centre", "music_venue"}:
        return "entertainment", amenity
    if leisure in {"park", "garden", "nature_reserve"}:
        return "nature", leisure
    if leisure in {"stadium", "sports_centre"}:
        return "sports", leisure
    if shop in {"mall", "department_store"}:
        return "shopping", shop
    if historic:
        return "history", historic
    if tourism in {"attraction", "viewpoint", "artwork", "zoo", "aquarium", "theme_park"}:
        return "landmarks", tourism
    return None


def _public_web_url(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.casefold() in {"yes", "no", "true", "false", "1", "0", "none", "unknown"}:
        return None
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.hostname:
        return None
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return None
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            return None
        if "." not in parsed.hostname:
            return None
    return candidate


def _walk_prices(value) -> list[tuple[float, str | None]]:
    found: list[tuple[float, str | None]] = []
    if isinstance(value, dict):
        currency = value.get("priceCurrency") or value.get("pricecurrency")
        for key in ("price", "lowPrice", "highPrice"):
            raw = value.get(key)
            if (
                isinstance(raw, (int, float))
                or isinstance(raw, str)
                and re.fullmatch(r"\s*\d+(?:[.,]\d{1,2})?\s*", raw)
            ):
                found.append((float(str(raw).strip().replace(",", ".")), str(currency).upper() if currency else None))
        for child in value.values():
            found.extend(_walk_prices(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_prices(child))
    return found


class LivePlaceProvider(PlaceProvider):
    """Cached worldwide place data from OpenStreetMap plus detail enrichment from the public web."""

    _rates: dict[tuple[str, str], float] = {}

    def __init__(self, db: Session):
        self.db = db
        self.database = DatabasePlaceProvider(db)
        self.headers = {"User-Agent": settings.trippilot_user_agent, "Accept-Language": "en"}

    def search(self, query: PlaceSearch) -> list[Place]:
        failure = None
        if self._needs_refresh(query.city):
            try:
                self._refresh_city(query.city)
            except (AppError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                failure = exc
                self.db.rollback()
        # The places table is also used by deterministic test fixtures. Live mode must
        # never silently mix those records into a real search, even when both sources
        # have cached rows for the same city.
        places = self.database.search(replace(query, limit=max(300, query.limit), external_provider="openstreetmap"))
        if not places and failure:
            if isinstance(failure, (AppError, ProviderError)):
                raise failure
            raise ProviderError("OpenStreetMap", "PROVIDER_INVALID_RESPONSE", False, 502) from failure
        places.sort(
            key=lambda place: float((place.source_metadata or {}).get("ranking_score", place.rating or 0)),
            reverse=True,
        )
        maps_changed = False
        for place in places:
            maps_url = google_maps_place_url(place.name, place.address, place.latitude, place.longitude)
            if place.maps_url != maps_url:
                place.maps_url = maps_url
                maps_changed = True
            metadata = dict(place.source_metadata or {})
            local_currency = _local_currency(place)
            if (
                place.cost_status == "PUBLISHED"
                and metadata.get("price_page_url")
                and "price_currency_explicit" not in metadata
                and metadata.get("published_currency") == "USD"
                and local_currency != "USD"
                and metadata.get("published_price") is not None
            ):
                amount = float(metadata["published_price"])
                converted = self._convert(amount, local_currency, "USD")
                place.cost_min = place.cost_expected = place.cost_max = converted if converted is not None else amount
                place.cost_currency = "USD" if converted is not None else local_currency
                metadata["published_currency"] = local_currency
                metadata["price_currency_explicit"] = False
                metadata["legacy_currency_repaired_at"] = datetime.now(UTC).isoformat()
                place.source_metadata = metadata
                maps_changed = True
        if maps_changed:
            self.db.commit()
        return places[: query.limit]

    def details(self, place_id: int) -> Place | None:
        place = self.database.details(place_id)
        if place and place.external_provider == "openstreetmap":
            maps_url = google_maps_place_url(place.name, place.address, place.latitude, place.longitude)
            if place.maps_url != maps_url:
                place.maps_url = maps_url
                self.db.commit()
        return place

    def enrich(self, place: Place) -> Place:
        if place.external_provider != "openstreetmap":
            return place
        changed = False
        changed = enrich_from_google(place) or changed
        if not place.image_url:
            changed = self._enrich_image(place) or changed
        metadata = place.source_metadata or {}
        # Re-check legacy cached web prices that did not record whether their
        # currency was explicit. Bare local prices must not silently become USD.
        if (
            place.cost_status == "PUBLISHED"
            and metadata.get("price_page_url")
            and "price_currency_explicit" not in metadata
        ):
            place.cost_status = "UNKNOWN"
        if place.cost_status == "UNKNOWN" or not place.ticket_url:
            changed = self._enrich_official_page(place) or changed
        if changed:
            place.last_updated_at = datetime.now(UTC)
            self.db.add(place)
            self.db.commit()
            self.db.refresh(place)
        return place

    def _needs_refresh(self, city: str) -> bool:
        latest = self.db.scalar(
            select(func.max(Place.last_updated_at)).where(
                Place.external_provider == "openstreetmap", func.lower(Place.city) == city.lower()
            )
        )
        if not latest:
            return True
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return latest < datetime.now(UTC) - timedelta(hours=settings.place_cache_hours)

    def _refresh_city(self, city: str) -> None:
        with httpx.Client(headers=self.headers, timeout=settings.live_data_timeout_seconds) as client:
            geocode = request_with_retry(
                lambda: client.get(
                    f"{settings.nominatim_base_url.rstrip('/')}/search",
                    params={"q": city, "format": "jsonv2", "addressdetails": 1, "limit": 1},
                ),
                provider="OpenStreetMap / venue data",
            )
            geocode.raise_for_status()
            matches = geocode.json()
            if not isinstance(matches, list):
                raise ProviderError("OpenStreetMap", "PROVIDER_INVALID_RESPONSE", False, 502)
            if not matches:
                raise AppError(
                    422,
                    "DESTINATION_NOT_FOUND",
                    "That destination could not be found. Use a city name, and add the state or country if needed.",
                    {"destination": city},
                )
            location = matches[0]
            latitude, longitude = float(location["lat"]), float(location["lon"])
            address = location.get("address", {})
            query = f"""[out:json][timeout:30];
(
relation[\"name\"][\"wikidata\"][\"tourism\"~\"museum|gallery|attraction|viewpoint|artwork|zoo|aquarium|theme_park\"](around:7000,{latitude},{longitude});
relation[\"name\"][\"wikidata\"][\"historic\"](around:7000,{latitude},{longitude});
relation[\"name\"][\"wikidata\"][\"leisure\"~\"park|garden|nature_reserve|stadium|sports_centre\"](around:7000,{latitude},{longitude});
);out center 40;
(
way[\"name\"][\"wikidata\"][\"tourism\"~\"museum|gallery|attraction|viewpoint|artwork|zoo|aquarium|theme_park\"](around:7000,{latitude},{longitude});
way[\"name\"][\"wikidata\"][\"historic\"](around:7000,{latitude},{longitude});
way[\"name\"][\"wikidata\"][\"leisure\"~\"park|garden|nature_reserve|stadium|sports_centre\"](around:7000,{latitude},{longitude});
);out center 70;
(
node[\"name\"][\"wikidata\"][\"tourism\"~\"museum|gallery|attraction|viewpoint|artwork|zoo|aquarium|theme_park\"](around:7000,{latitude},{longitude});
node[\"name\"][\"wikidata\"][\"historic\"](around:7000,{latitude},{longitude});
node[\"name\"][\"wikidata\"][\"leisure\"~\"park|garden|nature_reserve|stadium|sports_centre\"](around:7000,{latitude},{longitude});
);out center 70;
(
nwr[\"name\"][\"tourism\"~\"museum|gallery|attraction|viewpoint|artwork|zoo|aquarium|theme_park\"][\"website\"](around:7000,{latitude},{longitude});
nwr[\"name\"][\"historic\"][\"website\"](around:7000,{latitude},{longitude});
nwr[\"name\"][\"leisure\"~\"park|garden|nature_reserve|stadium|sports_centre\"][\"website\"](around:7000,{latitude},{longitude});
nwr[\"name\"][\"amenity\"~\"nightclub|bar|pub|biergarten|restaurant|cafe|food_court|theatre|cinema|arts_centre|music_venue\"][\"website\"](around:7000,{latitude},{longitude});
nwr[\"name\"][\"shop\"~\"mall|department_store\"](around:7000,{latitude},{longitude});
);out center 120;"""
            last_error: Exception | None = None
            elements = []
            for endpoint in (settings.overpass_url, settings.overpass_fallback_url):
                try:
                    overpass = request_with_retry(
                        lambda: client.post(endpoint, data={"data": query}),
                        provider="OpenStreetMap / venue data",
                        attempts=1,  # Fail over before spending the full request window on one overloaded server.
                    )
                    overpass.raise_for_status()
                    elements = overpass.json().get("elements", [])
                    if elements:
                        break
                except Exception as exc:
                    last_error = exc
            if not elements:
                if last_error:
                    raise last_error
                raise ValueError("The public OpenStreetMap place service returned no records") from last_error

        country_code = str(address.get("country_code", "")).lower()
        default_currency = CURRENCY_BY_COUNTRY.get(
            country_code,
            "EUR"
            if country_code in {"at", "be", "de", "es", "fi", "fr", "gr", "ie", "it", "lu", "nl", "pt"}
            else "USD",
        )
        seen: set[str] = set()
        for element in elements:
            tags = element.get("tags", {})
            if tags.get("zoo") == "enclosure":
                continue
            mapped = _category(tags)
            center = element.get("center", element)
            if not mapped or "lat" not in center or "lon" not in center:
                continue
            external_id = f"{element.get('type', 'node')}/{element['id']}"
            if external_id in seen:
                continue
            seen.add(external_id)
            category, subcategory = mapped
            source_url = f"https://www.openstreetmap.org/{external_id}"
            amount, status, published_currency = self._charge(tags, default_currency)
            converted = self._convert(amount, published_currency, "USD") if amount is not None else None
            expected = converted if converted is not None else float(amount or 0)
            stored_currency = "USD" if amount is None or converted is not None else published_currency
            street = " ".join(part for part in (tags.get("addr:housenumber"), tags.get("addr:street")) if part)
            neighborhood = (
                tags.get("addr:suburb") or tags.get("addr:district") or tags.get("is_in:suburb") or "City centre"
            )
            district = tags.get("addr:borough") or tags.get("addr:district") or neighborhood
            full_address = ", ".join(part for part in (street, neighborhood, city) if part)
            official_url = _public_web_url(tags.get("website") or tags.get("contact:website") or tags.get("url"))
            ticket_url = _public_web_url(tags.get("tickets:website") or tags.get("booking") or tags.get("reservation"))
            metadata = {
                "osm_url": source_url,
                "osm_tags": tags,
                "published_charge": tags.get("charge"),
                "published_price": amount,
                "published_currency": published_currency,
                "attribution": "© OpenStreetMap contributors, ODbL",
            }
            phone = tags.get("contact:phone") or tags.get("phone")
            place = self.db.scalar(
                select(Place).where(Place.external_provider == "openstreetmap", Place.external_place_id == external_id)
            )
            if place:
                for key, value in (place.source_metadata or {}).items():
                    if key.startswith("google_places_"):
                        metadata[key] = value
            category_relevance = {
                "museums": 4.6,
                "landmarks": 4.5,
                "nature": 4.4,
                "entertainment": 4.2,
                "nightlife": 4.1,
                "sports": 4.1,
                "history": 3.9,
                "shopping": 3.9,
                "food": 3.8,
            }.get(category, 3.8)
            relevance = min(
                4.9, category_relevance + (0.2 if tags.get("wikipedia") else 0.1 if tags.get("wikidata") else 0.0)
            )
            metadata["ranking_score"] = relevance
            values = {
                "name": tags["name"],
                "country": address.get("country", ""),
                "state": address.get("state", ""),
                "city": city,
                "borough_or_district": district,
                "neighborhood": neighborhood,
                "address": full_address or f"{tags['name']}, {city}",
                "latitude": float(center["lat"]),
                "longitude": float(center["lon"]),
                "category": category,
                "subcategory": subcategory,
                "tags": list(
                    dict.fromkeys(
                        [category, subcategory, *(tags.get("cuisine", "").split(";") if tags.get("cuisine") else [])]
                    )
                ),
                "rating": 0.0,
                "review_count": 0,
                "price_level": 0 if status == "FREE" else 1,
                "rating_source": None,
                "google_place_id": None,
                "phone_number": phone,
                "phone_source_url": source_url if phone else None,
                "cost_min": expected,
                "cost_expected": expected,
                "cost_max": expected,
                "duration_minutes": CATEGORY_DURATION.get(category, 90),
                "opening_hours": {"raw": tags["opening_hours"]} if tags.get("opening_hours") else {},
                "indoor_outdoor": "outdoor" if category == "nature" else "mixed",
                "reservation_required": False,
                "official_url": official_url,
                "maps_url": google_maps_place_url(
                    tags["name"],
                    full_address or f"{tags['name']}, {city}",
                    float(center["lat"]),
                    float(center["lon"]),
                ),
                "ticket_url": ticket_url,
                "reservation_url": ticket_url,
                "cost_status": status,
                "cost_source": source_url,
                "cost_currency": stored_currency,
                "source_metadata": metadata,
                "description": f"Live OpenStreetMap listing for {tags['name']}.",
                "last_updated_at": datetime.now(UTC),
            }
            if place and place.rating_source == "Google Places":
                values.update(
                    {
                        "rating": place.rating,
                        "review_count": place.review_count,
                        "rating_source": place.rating_source,
                        "google_place_id": place.google_place_id,
                        "phone_number": place.phone_number or phone,
                        "phone_source_url": place.phone_source_url or (source_url if phone else None),
                        "maps_url": place.maps_url,
                    }
                )
            if place:
                for key, value in values.items():
                    setattr(place, key, value)
            else:
                self.db.add(Place(external_provider="openstreetmap", external_place_id=external_id, **values))
        self.db.commit()

    @staticmethod
    def _charge(tags: dict[str, str], default_currency: str) -> tuple[float | None, str, str]:
        fee = tags.get("fee", "").lower()
        if fee in {"no", "free"}:
            return 0.0, "FREE", default_currency
        raw = tags.get("charge", "")
        match = re.search(
            r"(?:(GBP|USD|EUR|JPY|CAD|AUD|CHF|INR|SEK|NOK|DKK|PLN|CZK)\s*)?([£$€¥₹])?\s*(\d+(?:[.,]\d{1,2})?)",
            raw,
            re.IGNORECASE,
        )
        if not match:
            return None, "UNKNOWN", default_currency
        currency = (match.group(1) or SYMBOL_CURRENCY.get(match.group(2) or "") or default_currency).upper()
        return float(match.group(3).replace(",", ".")), "PUBLISHED", currency

    def _convert(self, amount: float | None, source: str, target: str) -> float | None:
        if amount is None or source == target:
            return float(amount or 0)
        return convert_amount(amount, source, target)

    def _enrich_image(self, place: Place) -> bool:
        tags = (place.source_metadata or {}).get("osm_tags", {})
        wikipedia = tags.get("wikipedia", "")
        title = wikipedia.split(":", 1)[-1] if wikipedia else None
        params = {
            "action": "query",
            "format": "json",
            "prop": "pageimages|info",
            "piprop": "thumbnail|name",
            "pithumbsize": 1000,
            "inprop": "url",
            "redirects": 1,
        }
        if title:
            params["titles"] = title
        else:
            params.update(
                {
                    "generator": "search",
                    "gsrsearch": f'intitle:"{place.name}" {place.city}',
                    "gsrnamespace": 0,
                    "gsrlimit": 1,
                }
            )
        try:
            response = request_with_retry(
                lambda: httpx.get(
                    settings.wikimedia_api_url, params=params, headers=self.headers, timeout=12.0, trust_env=False
                ),
                provider="OpenStreetMap / venue data",
            )
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {})
            page = next((value for value in pages.values() if value.get("thumbnail")), None)
            if not page:
                return False
            place.image_url = page["thumbnail"]["source"]
            place.image_source = (
                page.get("fullurl")
                or f"https://en.wikipedia.org/wiki/{quote(page.get('title', place.name).replace(' ', '_'))}"
            )
            place.image_attribution = f"Wikimedia contributors · {page.get('pageimage', page.get('title', place.name))}"
            return True
        except Exception:
            return False

    def _enrich_official_page(self, place: Place) -> bool:
        official_url = _public_web_url(place.official_url)
        if not official_url:
            return False
        changed = False
        pages: list[tuple[str, str]] = []
        try:
            with httpx.Client(headers=self.headers, timeout=10.0, follow_redirects=True) as client:
                response = request_with_retry(lambda: client.get(official_url), provider="OpenStreetMap / venue data")
                response.raise_for_status()
                resolved_official = _public_web_url(str(response.url))
                if not resolved_official or "text/html" not in response.headers.get("content-type", ""):
                    return False
                official_body = response.text[:1_500_000]
                pages.append((resolved_official, official_body))

                parser = _TicketLinkParser(resolved_official)
                try:
                    parser.feed(official_body)
                except Exception:
                    pass
                ticket_url = _public_web_url(place.ticket_url)
                if not ticket_url:
                    ticket_url = next(
                        (
                            valid
                            for label, url in parser.links
                            if re.search(r"\b(tickets?|admission|book|booking|visit)\b", label, re.IGNORECASE)
                            and (valid := _public_web_url(url))
                        ),
                        None,
                    )
                if ticket_url and ticket_url != place.ticket_url:
                    place.ticket_url = ticket_url
                    place.reservation_url = place.reservation_url or ticket_url
                    changed = True
                if ticket_url and ticket_url != resolved_official:
                    ticket_response = request_with_retry(
                        lambda: client.get(ticket_url), provider="OpenStreetMap / venue data"
                    )
                    ticket_response.raise_for_status()
                    resolved_ticket = _public_web_url(str(ticket_response.url))
                    if resolved_ticket and "text/html" in ticket_response.headers.get("content-type", ""):
                        pages.insert(0, (resolved_ticket, ticket_response.text[:1_500_000]))
                        if resolved_ticket != place.ticket_url:
                            place.ticket_url = resolved_ticket
                            place.reservation_url = resolved_ticket
                            changed = True
        except Exception:
            if not pages:
                return changed

        priced_page: str | None = None
        prices: list[tuple[float, str | None]] = []
        for page_url, body in pages:
            page_prices: list[tuple[float, str | None]] = []
            for raw in re.findall(
                r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
                body,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                try:
                    page_prices.extend(_walk_prices(json.loads(html.unescape(raw))))
                except (json.JSONDecodeError, TypeError):
                    continue
            if not page_prices:
                plain = re.sub(r"<[^>]+>", " ", body)
                match = re.search(
                    r"\b(?:admission|tickets?|entry|from)\b.{0,100}?([£$€¥])\s*(\d+(?:[.,]\d{1,2})?)",
                    plain,
                    re.IGNORECASE,
                )
                if match:
                    page_prices.append((float(match.group(2).replace(",", ".")), SYMBOL_CURRENCY[match.group(1)]))
            positive = [item for item in page_prices if item[0] > 0]
            if positive:
                prices = positive
                priced_page = page_url
                break
        if prices and place.cost_status == "UNKNOWN" and priced_page:
            amount, currency = min(prices, key=lambda item: item[0])
            source_currency = currency or _local_currency(place)
            converted = self._convert(amount, source_currency, "USD")
            place.cost_min = place.cost_expected = place.cost_max = converted if converted is not None else amount
            place.cost_status = "PUBLISHED"
            place.cost_source = priced_page
            place.cost_currency = "USD" if converted is not None else source_currency
            metadata = dict(place.source_metadata or {})
            metadata.update(
                {
                    "published_price": amount,
                    "published_currency": source_currency,
                    "price_currency_explicit": currency is not None,
                    "price_page_url": priced_page,
                    "price_checked_at": datetime.now(UTC).isoformat(),
                }
            )
            place.source_metadata = metadata
            changed = True
        return changed
