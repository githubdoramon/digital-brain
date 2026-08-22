from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from geo_utils import haversine_meters
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS = 20


def search_nearby(
    *,
    lat: float,
    lon: float,
    radius_m: float,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Search Google's current nearby places, returning normalized candidates."""
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        return {"available": False, "results": [], "error": "missing_api_key"}

    result_limit = max(1, min(int(max_results), MAX_RESULTS))
    field_mask = ",".join(
        (
            "places.id",
            "places.displayName",
            "places.location",
            "places.primaryType",
            "places.types",
            "places.formattedAddress",
            "places.addressComponents",
            "places.businessStatus",
        )
    )
    try:
        response = requests.post(
            GOOGLE_PLACES_SEARCH_URL,
            json={
                "maxResultCount": result_limit,
                "rankPreference": "DISTANCE",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": max(1.0, float(radius_m)),
                    }
                },
            },
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": field_mask,
            },
            timeout=_timeout_seconds(),
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        logger.warning("[google_places] nearby search timed out")
        return {"available": False, "results": [], "error": "timeout"}
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        logger.warning("[google_places] nearby search failed status=%s", status)
        return {"available": False, "results": [], "error": "http_error", "status": status}
    except requests.RequestException as exc:
        logger.warning("[google_places] nearby search request failed: %s", exc)
        return {"available": False, "results": [], "error": "request_error"}
    except ValueError:
        logger.warning("[google_places] nearby search returned invalid JSON")
        return {"available": False, "results": [], "error": "invalid_json"}

    fetched_at = datetime.now(timezone.utc)
    results = [
        normalized
        for raw in payload.get("places", [])
        if isinstance(raw, dict)
        for normalized in [_normalize_candidate(raw, fetched_at=fetched_at)]
        if normalized is not None
    ]
    return {"available": True, "results": results, "fetched_at": fetched_at}


def _normalize_candidate(raw: dict[str, Any], *, fetched_at: datetime) -> dict[str, Any] | None:
    provider_place_id = str(raw.get("id") or "").strip()
    if not provider_place_id:
        resource_name = str(raw.get("name") or "").strip()
        provider_place_id = resource_name.rsplit("/", 1)[-1] if resource_name else ""
    if not provider_place_id:
        return None

    display_name = raw.get("displayName")
    title = display_name.get("text") if isinstance(display_name, dict) else display_name
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    lat = _safe_float(location.get("latitude"))
    lon = _safe_float(location.get("longitude"))
    if lat is None or lon is None:
        return None

    address_components = raw.get("addressComponents")
    city, country = _address_locality(address_components)
    types = [str(value).strip() for value in raw.get("types", []) if str(value).strip()]
    return {
        "provider": "google",
        "provider_place_id": provider_place_id,
        "title": str(title or "").strip() or None,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "primary_type": str(raw.get("primaryType") or "").strip() or None,
        "types": list(dict.fromkeys(types)),
        "formatted_address": str(raw.get("formattedAddress") or "").strip() or None,
        "city": city,
        "country": country,
        "business_status": str(raw.get("businessStatus") or "").strip() or None,
        "fetched_at": fetched_at,
    }


def _address_locality(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, list):
        return None, None
    city: str | None = None
    country: str | None = None
    for component in value:
        if not isinstance(component, dict):
            continue
        text = str(component.get("longText") or component.get("shortText") or "").strip() or None
        types = {str(item).strip() for item in component.get("types", []) if str(item).strip()}
        if city is None and types.intersection({"locality", "postal_town", "administrative_area_level_2"}):
            city = text
        if country is None and "country" in types:
            country = text
    return city, country


def distance_to_candidate(lat: float, lon: float, candidate: dict[str, Any]) -> float | None:
    candidate_lat = _safe_float(candidate.get("lat"))
    candidate_lon = _safe_float(candidate.get("lon"))
    if candidate_lat is None or candidate_lon is None:
        return None
    return haversine_meters(lat, lon, candidate_lat, candidate_lon)


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("GOOGLE_PLACES_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        return float(DEFAULT_TIMEOUT_SECONDS)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
