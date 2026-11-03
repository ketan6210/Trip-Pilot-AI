import asyncio

import httpx

from backend.providers.locations import resolve_route_locations


def test_independent_location_requests_overlap(monkeypatch):
    original_client = httpx.AsyncClient
    calls = []
    both_started = asyncio.Event()

    async def respond(request):
        calls.append(request.url.params["name"])
        if len(calls) == 2:
            both_started.set()
        # A sequential implementation cannot complete this bounded rendezvous.
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": request.url.params["name"], "latitude": 40.7, "longitude": -74, "country": "United States"}
                ]
            },
        )

    monkeypatch.setattr(
        "backend.providers.locations.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = asyncio.run(resolve_route_locations("Chicago", "New York City"))
    assert set(calls) == {"Chicago", "New York City"}
    assert result["origin"]["name"] == "Chicago"
    assert result["destination"]["name"] == "New York City"


def test_place_outage_is_not_reported_as_invalid_user_input(client, monkeypatch):
    from backend.providers.http import ProviderError
    from backend.providers.live import LivePlaceProvider
    from backend.settings import settings

    monkeypatch.setattr(settings, "place_provider", "live")

    def fail(*args):
        raise ProviderError("OpenStreetMap", "PROVIDER_QUOTA_EXCEEDED", True, 429)

    monkeypatch.setattr(LivePlaceProvider, "_refresh_city", fail)
    response = client.get("/api/v1/places/search?city=UnknownCity")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "PROVIDER_QUOTA_EXCEEDED"


def test_unresolved_city_has_actionable_input_error_not_generic_provider_failure(client, monkeypatch):
    from backend.settings import settings

    original_client = httpx.Client
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(settings, "place_provider", "live")
    monkeypatch.setattr(
        "backend.providers.live.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    response = client.get("/api/v1/places/search?city=UnresolvableCity")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DESTINATION_NOT_FOUND"
    assert "city name" in response.json()["detail"]
    assert len(requests) == 1  # No venue query or pointless retry for an empty match.


def test_retired_overpass_endpoint_is_updated_and_custom_endpoint_is_preserved():
    from backend.settings import Settings

    config = Settings(
        overpass_fallback_url="https://overpass.kumi.systems/api/interpreter",
        overpass_url="https://maps.example.test/api/interpreter",
        _env_file=None,
    )
    assert config.overpass_fallback_url == "https://overpass.private.coffee/api/interpreter"
    assert config.overpass_url == "https://maps.example.test/api/interpreter"


def test_overpass_fails_over_once_before_repeating_slow_primary(client, monkeypatch):
    from backend.db import SessionLocal
    from backend.providers.live import LivePlaceProvider
    from backend.settings import settings

    original_client = httpx.Client
    calls = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"lat": "41.88", "lon": "-87.63", "address": {"country": "United States", "country_code": "us"}}],
            )
        calls.append(str(request.url))
        if str(request.url) == settings.overpass_url:
            raise httpx.ReadTimeout("overloaded", request=request)
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 987654,
                        "lat": 41.88,
                        "lon": -87.63,
                        "tags": {"name": "Test Chicago Museum", "tourism": "museum", "fee": "no"},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "backend.providers.live.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with SessionLocal() as db:
        LivePlaceProvider(db)._refresh_city("Chicago")
    assert calls == [settings.overpass_url, settings.overpass_fallback_url]


def test_route_cache_expires_and_does_not_keep_failures(monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from backend.settings import settings
    from backend.transport import service

    service._route_cache.clear()
    clock = [0.0]
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            json={"routes": [{"duration": "600s", "distanceMeters": 1000}]},
            request=httpx.Request("POST", "https://routes.example"),
        )

    monkeypatch.setattr(settings, "google_maps_api_key", SecretStr("test"))
    monkeypatch.setattr(settings, "route_cache_seconds", 10)
    monkeypatch.setattr(service, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service.httpx, "post", post)
    a = SimpleNamespace(latitude=40.7, longitude=-74)
    b = SimpleNamespace(latitude=40.71, longitude=-74.01)
    service._google_route(a, b, "DRIVE", "USD")
    service._google_route(a, b, "DRIVE", "USD")
    assert len(calls) == 1
    clock[0] = 11
    service._google_route(a, b, "DRIVE", "USD")
    assert len(calls) == 2
