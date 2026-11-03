def test_category_search_returns_exact_venues_with_full_location(client):
    response = client.get("/places/search", params={"city": "New York City", "category": "nightlife"})
    assert response.status_code == 200
    places = response.json()
    assert {place["name"] for place in places} == {"Village Vanguard", "Smalls Jazz Club", "Westlight"}
    assert all(place["address"] and place["neighborhood"] and place["borough_or_district"] for place in places)
    assert all(place["latitude"] and place["longitude"] and place["maps_url"] for place in places)
    detail = client.get(f"/places/{places[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["external_provider"] == "mock"


def test_radius_search_uses_geospatial_fallback(client):
    response = client.get(
        "/places/search", params={"city": "New York City", "latitude": 40.7350, "longitude": -74.0020, "radius_km": 0.5}
    )
    assert response.status_code == 200
    names = {place["name"] for place in response.json()}
    assert "Village Vanguard" in names
    assert "Smalls Jazz Club" in names
    assert "The Metropolitan Museum of Art" not in names
    incomplete = client.get("/places/search", params={"city": "New York City", "latitude": 40.7350})
    assert incomplete.status_code == 422


def test_location_hierarchy_reaches_exact_venues(client):
    response = client.get("/locations/hierarchy", params={"city": "Boston"})
    assert response.status_code == 200
    hierarchy = response.json()
    downtown = hierarchy["United States"]["Massachusetts"]["Boston"]["Boston"]["Downtown"]
    assert {venue["name"] for venue in downtown} >= {"Boston Common", "Old State House", "Boston Public Market"}


def test_neighborhood_ranking_returns_features_and_exact_places(client):
    trip = client.post(
        "/trips",
        json={
            "currency": "USD",
            "destination_city": "New York City",
            "origin": "Chicago",
            "start_date": "2026-09-10",
            "end_date": "2026-09-12",
            "traveler_count": 2,
            "total_budget": 2000,
        },
    ).json()
    for name in ["Ari", "Sam"]:
        traveler = client.post(f"/trips/{trip['id']}/travelers", json={"display_name": name}).json()
        client.put(
            f"/travelers/{traveler['id']}/preferences",
            json={"preferences": [{"category": "nightlife", "subcategory": "general", "score": 10}]},
        )
    response = client.get(f"/trips/{trip['id']}/neighborhoods", params={"category": "nightlife"})
    assert response.status_code == 200
    neighborhoods = response.json()
    assert neighborhoods[0]["neighborhood"] == "Greenwich Village"
    assert neighborhoods[0]["place_count"] == 2
    assert {
        "preference_match",
        "category_density",
        "rating_quality",
        "price_fit",
        "transit_connectivity",
        "geographic_compactness",
        "hotel_proximity",
    } == set(neighborhoods[0]["features"])
    assert {place["name"] for place in neighborhoods[0]["top_places"]} == {"Village Vanguard", "Smalls Jazz Club"}
