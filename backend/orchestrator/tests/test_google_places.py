from __future__ import annotations

import google_places


def test_search_nearby_normalizes_candidates(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "places": [
                    {
                        "id": "ChIJexample",
                        "displayName": {"text": "Example Cafe"},
                        "location": {"latitude": 38.7, "longitude": -9.1},
                        "primaryType": "cafe",
                        "types": ["cafe", "food"],
                        "formattedAddress": "1 Example Street",
                        "businessStatus": "OPERATIONAL",
                        "addressComponents": [
                            {"longText": "Example City", "types": ["locality"]},
                            {"longText": "Example Country", "types": ["country"]},
                        ],
                    }
                ]
            }

    captured: dict[str, object] = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(google_places.requests, "post", fake_post)

    result = google_places.search_nearby(lat=38.7, lon=-9.1, radius_m=100)

    assert result["available"] is True
    assert result["results"][0]["provider_place_id"] == "ChIJexample"
    assert result["results"][0]["title"] == "Example Cafe"
    assert result["results"][0]["primary_type"] == "cafe"
    assert result["results"][0]["city"] == "Example City"
    assert result["results"][0]["country"] == "Example Country"
    assert captured["kwargs"]["headers"]["X-Goog-Api-Key"] == "test-key"


def test_search_nearby_is_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    result = google_places.search_nearby(lat=1, lon=2, radius_m=100)

    assert result == {"available": False, "results": [], "error": "missing_api_key"}
