from urllib.parse import parse_qs, urlparse

from backend.geography.maps import google_maps_directions_url, google_maps_place_url


def test_google_maps_place_url_uses_official_keyless_format():
    url = google_maps_place_url("The British Museum", "Great Russell Street, London", 51.5194, -0.1269)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parsed.path == "/maps/search/"
    assert params["api"] == ["1"]
    assert params["query"] == ["The British Museum, Great Russell Street, London"]


def test_google_maps_directions_url_preserves_coordinates_and_mode():
    url = google_maps_directions_url(51.5194, -0.1269, 51.5081, -0.0759, "public transit")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.path == "/maps/dir/"
    assert params == {
        "api": ["1"],
        "origin": ["51.519400,-0.126900"],
        "destination": ["51.508100,-0.075900"],
        "travelmode": ["transit"],
    }
