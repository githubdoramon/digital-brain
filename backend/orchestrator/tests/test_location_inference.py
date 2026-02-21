from unittest.mock import MagicMock

import location_inference


def test_infer_current_place_prefers_known_place(monkeypatch):
    location_inference._CACHE.clear()
    monkeypatch.setattr(
        "location_inference._load_places_with_coordinates",
        lambda: [
            {
                "place_id": "plc_home",
                "name": "Home",
                "city": "Aurora",
                "country": "Westoria",
                "lat": 38.7222,
                "lon": -9.1393,
            }
        ],
    )

    inferred = location_inference.infer_current_place(
        {"lat": 38.72221, "lon": -9.13931, "accuracy_m": 20.0}
    )

    assert inferred is not None
    assert inferred.get("source") == "known_place_proximity"
    assert inferred.get("place_id") == "plc_home"
    assert inferred.get("place_name") == "Home"


def test_infer_current_place_falls_back_to_geoapify(monkeypatch):
    location_inference._CACHE.clear()
    monkeypatch.setattr("location_inference._load_places_with_coordinates", lambda: [])
    monkeypatch.setenv("GEOAPIFY_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "results": [
            {
                "name": "Coffee Shop",
                "city": "Aurora",
                "country": "Westoria",
                "lat": 38.722,
                "lon": -9.139,
                "distance": 9.2,
            }
        ]
    }
    monkeypatch.setattr("location_inference.requests.get", lambda *args, **kwargs: mock_response)

    inferred = location_inference.infer_current_place(
        {"lat": 38.722, "lon": -9.139, "accuracy_m": 50.0}
    )

    assert inferred is not None
    assert inferred.get("source") == "reverse_geocode"
    assert inferred.get("provider") == "geoapify"
    assert inferred.get("place_name") == "Coffee Shop"


def test_geocode_place_name_uses_geoapify(monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "test-key")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "results": [
            {
                "name": "My Home",
                "city": "Aurora",
                "country": "Westoria",
                "lat": 38.722,
                "lon": -9.139,
            }
        ]
    }
    monkeypatch.setattr("location_inference.requests.get", lambda *args, **kwargs: mock_response)

    result = location_inference.geocode_place_name("my house", near_lat=38.72, near_lon=-9.13)

    assert result is not None
    assert result.get("provider") == "geoapify"
    assert result.get("source") == "geoapify_forward_geocode"
    assert result.get("place_name") == "My Home"
