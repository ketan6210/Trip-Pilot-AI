from datetime import date
from urllib.parse import parse_qs, urlparse

from pydantic import SecretStr

from backend.db import SessionLocal
from backend.models import Place, Trip, TripBudget
from backend.providers.base import PlaceSearch
from backend.providers.flights import get_flight_quote
from backend.providers.live import LivePlaceProvider, _public_web_url
from backend.settings import settings


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_live_place_provider_normalizes_worldwide_records(monkeypatch):
    geocode = [
        {
            "lat": "51.5074",
            "lon": "-0.1278",
            "boundingbox": ["51.2", "51.7", "-0.5", "0.3"],
            "address": {"country": "United Kingdom", "country_code": "gb", "state": "England"},
        }
    ]
    elements = {
        "elements": [
            {
                "type": "node",
                "id": 10,
                "lat": 51.5,
                "lon": -0.12,
                "tags": {
                    "name": "Free London Museum",
                    "tourism": "museum",
                    "fee": "no",
                    "wikipedia": "en:Free London Museum",
                },
            },
            {
                "type": "way",
                "id": 20,
                "center": {"lat": 51.51, "lon": -0.13},
                "tags": {
                    "name": "London Night Hall",
                    "amenity": "nightclub",
                    "website": "https://example.com",
                    "charge": "GBP 20",
                },
            },
        ]
    }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            return Response(geocode)

        def post(self, url, data):
            return Response(elements)

    monkeypatch.setattr("backend.providers.live.httpx.Client", Client)
    monkeypatch.setattr(LivePlaceProvider, "_convert", lambda self, amount, source, target: amount * 1.25)
    with SessionLocal() as db:
        db.add(
            Place(
                external_provider="mock",
                external_place_id="mock-london",
                name="Fake London Attraction",
                country="United Kingdom",
                state="England",
                city="London",
                borough_or_district="Central",
                neighborhood="Central",
                address="Fake address",
                latitude=51.5,
                longitude=-0.12,
                category="museums",
                subcategory="museum",
                tags=["museums"],
                rating=5,
                review_count=9999,
                price_level=1,
                cost_min=1,
                cost_expected=1,
                cost_max=1,
                duration_minutes=60,
                maps_url="https://example.com/fake",
                source_metadata={},
                description="Test-only fixture that must never enter a live result.",
            )
        )
        db.commit()
        places = LivePlaceProvider(db).search(PlaceSearch(city="London", limit=10))
        by_name = {place.name: place for place in places}
        assert "Fake London Attraction" not in by_name
        assert {place.external_provider for place in places} == {"openstreetmap"}
        assert by_name["Free London Museum"].cost_status == "FREE"
        assert float(by_name["Free London Museum"].cost_expected) == 0
        assert by_name["Free London Museum"].rating == 0
        assert by_name["Free London Museum"].source_metadata["ranking_score"] == 4.8
        assert by_name["London Night Hall"].cost_status == "PUBLISHED"
        assert float(by_name["London Night Hall"].cost_expected) == 25
        assert by_name["London Night Hall"].maps_url.startswith("https://www.google.com/maps/search/")
        assert by_name["London Night Hall"].source_metadata["attribution"].startswith("© OpenStreetMap")


def test_amadeus_quote_is_live_and_separate_from_budget_allocation(monkeypatch):
    trip = Trip(
        destination_city="London",
        origin="Chicago",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 13),
        traveler_count=3,
        total_budget=3000,
        currency="USD",
        pace="balanced",
        transport_mode="mixed",
        max_walking_per_day=120,
        hotel_style="midrange",
    )
    trip.budget = TripBudget(categories={"flight": 900})
    monkeypatch.setattr(settings, "amadeus_client_id", "client")
    monkeypatch.setattr(settings, "amadeus_client_secret", SecretStr("secret"))

    def post(url, *args, **kwargs):
        if url.endswith("/token"):
            return Response({"access_token": "token"})
        assert url.endswith("/flight-offers/pricing")
        selected = kwargs["json"]["data"]["flightOffers"]
        assert [offer["id"] for offer in selected] == ["1"]
        return Response(
            {
                "data": {
                    "flightOffers": [
                        {
                            **selected[0],
                            "price": {"grandTotal": "1185.00", "currency": "USD"},
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("backend.providers.flights.httpx.post", post)
    monkeypatch.setattr(
        "backend.providers.flights.httpx.get",
        lambda *args, **kwargs: Response(
            {
                "data": [
                    {"id": "2", "price": {"grandTotal": "1500.00", "currency": "USD"}, "itineraries": []},
                    {
                        "id": "1",
                        "price": {"grandTotal": "1200.00", "currency": "USD"},
                        "itineraries": [{"segments": [{"carrierCode": "BA"}]}],
                    },
                ]
            }
        ),
    )
    quote = get_flight_quote(trip)
    assert quote["status"] == "LIVE_FARE"
    assert quote["total"] == 1185
    assert quote["per_person"] == 395
    assert quote["allocated_budget"] == 900
    assert quote["carriers"] == ["BA"]
    assert quote["price_confirmed"] is True


def test_skyscanner_uses_valid_cabin_polls_complete_results_and_honors_price_units(monkeypatch):
    trip = Trip(
        destination_city="Paris",
        origin="Chicago",
        start_date=date(2026, 10, 10),
        end_date=date(2026, 10, 14),
        traveler_count=3,
        total_budget=5000,
        currency="USD",
        pace="balanced",
        transport_mode="mixed",
        max_walking_per_day=120,
        hotel_style="midrange",
    )
    trip.budget = TripBudget(categories={"flight": 1500})
    monkeypatch.setattr(settings, "skyscanner_api_key", SecretStr("partner-key"))
    monkeypatch.setattr(settings, "amadeus_client_id", None)
    monkeypatch.setattr(settings, "amadeus_client_secret", None)
    calls = []

    def post(url, *args, **kwargs):
        calls.append(url)
        if url.endswith("/search/create"):
            assert kwargs["json"]["query"]["cabinClass"] == "CABIN_CLASS_ECONOMY"
            assert kwargs["json"]["query"]["adults"] == 3
            return Response(
                {"sessionToken": "session-1", "status": "RESULT_STATUS_INCOMPLETE", "content": {"results": {}}}
            )
        assert url.endswith("/search/poll/session-1")
        return Response(
            {
                "sessionToken": "session-1",
                "status": "RESULT_STATUS_COMPLETE",
                "content": {
                    "results": {
                        "itineraries": {
                            "it-1": {
                                "id": "it-1",
                                "pricingOptions": [
                                    {
                                        "price": {"amount": "123450", "unit": "PRICE_UNIT_CENTI"},
                                        "items": [
                                            {"agentId": "agent-1", "deepLink": "https://booking.example/offer-1"}
                                        ],
                                    }
                                ],
                            }
                        }
                    }
                },
            }
        )

    monkeypatch.setattr("backend.providers.flights.httpx.post", post)
    quote = get_flight_quote(trip, refresh=True)
    assert len(calls) == 2
    assert quote["status"] == "LIVE_FARE" and quote["search_complete"] is True
    assert quote["total"] == 1234.50 and quote["per_person"] == 411.50
    assert quote["booking_url"] == "https://booking.example/offer-1"
    assert quote["booking_is_provider_offer"] is True


def test_flight_search_link_encodes_route_dates_and_all_travelers(monkeypatch):
    trip = Trip(
        destination_city="Los Angeles",
        origin="New York City",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        traveler_count=3,
        total_budget=3000,
        currency="USD",
        pace="balanced",
        transport_mode="mixed",
        max_walking_per_day=120,
        hotel_style="midrange",
    )
    trip.budget = TripBudget(categories={"flight": 600})
    monkeypatch.setattr(settings, "amadeus_client_id", None)
    monkeypatch.setattr(settings, "amadeus_client_secret", None)
    quote = get_flight_quote(trip)
    params = parse_qs(urlparse(quote["search_url"]).query)
    path = urlparse(quote["search_url"]).path
    assert quote["status"] == "SEARCH_REQUIRED"
    assert quote["origin_code"] == "NYC"
    assert quote["destination_code"] == "LAX"
    assert quote["traveler_count"] == 3
    assert path.endswith("/nyc/lax/260901/260903/")
    assert params["adultsv2"] == ["3"]


def test_newark_uses_ewr_in_exact_route_search():
    trip = Trip(
        destination_city="Delhi",
        origin="Newark",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
        traveler_count=4,
        total_budget=20000,
        currency="INR",
    )
    trip.budget = TripBudget(categories={"flight": 4000})
    quote = get_flight_quote(trip)
    assert quote["origin_code"] == "EWR"
    assert "/ewr/del/260910/260912/" in quote["search_url"]
    assert "adultsv2=4" in quote["search_url"]


def test_booking_tags_are_not_mistaken_for_web_links():
    assert _public_web_url("yes") is None
    assert _public_web_url("true") is None
    assert _public_web_url("booking") is None
    assert _public_web_url("tickets.example.org/buy") == "https://tickets.example.org/buy"


def test_official_ticket_page_is_the_exact_published_price_source(monkeypatch):
    class WebResponse(Response):
        def __init__(self, url, text):
            super().__init__({})
            self.url = url
            self.text = text
            self.headers = {"content-type": "text/html; charset=utf-8"}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            if url == "https://venue.example.org":
                return WebResponse(url, '<a href="/tickets">Buy tickets</a>')
            assert url == "https://venue.example.org/tickets"
            return WebResponse(
                url,
                '<script type="application/ld+json">{"@type":"Offer","price":"25.00","priceCurrency":"GBP"}</script>',
            )

    monkeypatch.setattr("backend.providers.live.httpx.Client", Client)
    monkeypatch.setattr(LivePlaceProvider, "_convert", lambda self, amount, source, target: amount * 1.25)
    with SessionLocal() as db:
        place = Place(
            external_provider="openstreetmap",
            external_place_id="node/exact-ticket",
            name="Exact Ticket Venue",
            country="United Kingdom",
            state="England",
            city="London",
            borough_or_district="Central",
            neighborhood="Central",
            address="London",
            latitude=51.5,
            longitude=-0.12,
            category="landmarks",
            subcategory="attraction",
            tags=["landmarks"],
            rating=0,
            review_count=0,
            price_level=1,
            cost_min=0,
            cost_expected=0,
            cost_max=0,
            duration_minutes=60,
            official_url="https://venue.example.org",
            maps_url="https://www.google.com/maps/search/?api=1",
            source_metadata={"published_currency": "GBP"},
            cost_status="UNKNOWN",
            cost_currency="USD",
            description="",
        )
        assert LivePlaceProvider(db)._enrich_official_page(place) is True
        assert place.ticket_url == "https://venue.example.org/tickets"
        assert place.cost_source == "https://venue.example.org/tickets"
        assert float(place.cost_expected) == 31.25
        assert place.source_metadata["published_price"] == 25
        assert place.source_metadata["published_currency"] == "GBP"
