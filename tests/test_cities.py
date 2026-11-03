from backend.geography import cities


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "results": [
                {
                    "id": 2643743,
                    "name": "London",
                    "latitude": 51.50853,
                    "longitude": -0.12574,
                    "feature_code": "PPLC",
                    "country_code": "GB",
                    "country": "United Kingdom",
                    "admin1": "England",
                    "timezone": "Europe/London",
                    "population": 8961989,
                },
                {
                    "id": 123,
                    "name": "London",
                    "latitude": 42.98339,
                    "longitude": -81.23304,
                    "feature_code": "PPLA2",
                    "country_code": "CA",
                    "country": "Canada",
                    "admin1": "Ontario",
                    "timezone": "America/Toronto",
                    "population": 346765,
                },
            ]
        }


def test_live_city_search_returns_ranked_worldwide_suggestions(client, monkeypatch):
    cities._CITY_CACHE.clear()

    def live_get(url, params, headers, timeout, trust_env):
        assert url == "https://geocoding-api.open-meteo.com/v1/search"
        assert params["name"] == "London"
        assert params["language"] == "en"
        assert trust_env is False
        return Response()

    monkeypatch.setattr("backend.geography.cities.httpx.get", live_get)
    response = client.get("/locations/search", params={"q": "London"})
    assert response.status_code == 200
    results = response.json()
    assert [item["display_name"] for item in results] == [
        "London, England, United Kingdom",
        "London, Ontario, Canada",
    ]
    assert results[0]["source"] == "Open-Meteo Geocoding"
