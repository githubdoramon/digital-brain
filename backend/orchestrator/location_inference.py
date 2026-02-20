from __future__ import annotations

import os
import threading
import time
from math import asin, cos, radians, sin, sqrt
from typing import Any

import requests

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_LOCK = threading.Lock()


def infer_current_place(
    client_location: dict[str, Any] | None,
    *,
    user_email: str | None = None,
) -> dict[str, Any] | None:
    """Infer likely place from client coordinates.

    Preference order:
    1) Match against known places in DB by proximity.
    2) Fallback to Geoapify reverse geocoding.
    """
    normalized = _normalize_location(client_location)
    if not normalized:
        return None

    cache_key = _build_cache_key(normalized)
    cached = _read_cache(cache_key)
    if cached is not _CACHE_MISS:
        return dict(cached) if isinstance(cached, dict) else None

    lat = normalized["lat"]
    lon = normalized["lon"]
    accuracy_m = normalized.get("accuracy_m")

    inferred = _match_known_place(lat=lat, lon=lon, accuracy_m=accuracy_m)
    if inferred is None:
        inferred = _reverse_geocode_with_geoapify(
            lat=lat,
            lon=lon,
            captured_at=normalized.get("captured_at"),
        )

    _write_cache(cache_key, inferred)
    if inferred:
        logger.info(
            "[location_inference] source=%s confidence=%s user=%s",
            inferred.get("source"),
            inferred.get("confidence"),
            user_email or "unknown",
        )
    return inferred


def _normalize_location(location: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(location, dict):
        return None

    lat = _safe_float(location.get("lat"))
    lon = _safe_float(location.get("lon"))
    if lat is None or lon is None:
        return None

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    normalized: dict[str, Any] = {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    }

    accuracy = location.get("accuracy_m")
    if accuracy is not None:
        try:
            normalized["accuracy_m"] = round(max(0.0, float(accuracy)), 1)
        except (TypeError, ValueError):
            pass

    captured_at = str(location.get("captured_at") or "").strip()
    if captured_at:
        normalized["captured_at"] = captured_at

    return normalized


def _match_known_place(
    *, lat: float, lon: float, accuracy_m: float | None
) -> dict[str, Any] | None:
    threshold = _known_place_threshold_meters(accuracy_m)
    candidates = _load_places_with_coordinates()
    if not candidates:
        return None

    nearest: dict[str, Any] | None = None
    nearest_distance: float | None = None
    for place in candidates:
        distance = _haversine_meters(lat, lon, place["lat"], place["lon"])
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest = place

    if nearest is None or nearest_distance is None or nearest_distance > threshold:
        return None

    confidence = _confidence_for_known_place(nearest_distance, threshold, accuracy_m)
    return {
        "place_id": nearest.get("place_id"),
        "place_name": nearest.get("name") or "Known place",
        "city": nearest.get("city"),
        "country": nearest.get("country"),
        "lat": round(float(nearest["lat"]), 6),
        "lon": round(float(nearest["lon"]), 6),
        "distance_m": round(nearest_distance, 1),
        "confidence": confidence,
        "source": "known_place_proximity",
        "provider": None,
    }


def _reverse_geocode_with_geoapify(
    *,
    lat: float,
    lon: float,
    captured_at: str | None,
) -> dict[str, Any] | None:
    api_key = os.getenv("GEOAPIFY_API_KEY", "").strip()
    if not api_key:
        return None

    base_url = os.getenv(
        "GEOAPIFY_REVERSE_GEOCODE_URL", "https://api.geoapify.com/v1/geocode/reverse"
    )
    timeout_s = _env_float("GEOAPIFY_TIMEOUT_SECONDS", 5.0)

    try:
        response = requests.get(
            base_url,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "apiKey": api_key,
            },
            timeout=timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("[location_inference] Geoapify request failed: %s", exc)
        return None
    except ValueError:
        logger.warning("[location_inference] Geoapify returned invalid JSON")
        return None

    candidate = _first_geoapify_result(payload)
    if not candidate:
        return None

    name = (
        str(candidate.get("name") or "").strip()
        or str(candidate.get("address_line1") or "").strip()
        or str(candidate.get("formatted") or "").strip()
    )
    if not name:
        return None

    city = (
        str(candidate.get("city") or "").strip()
        or str(candidate.get("town") or "").strip()
        or str(candidate.get("village") or "").strip()
        or str(candidate.get("municipality") or "").strip()
        or None
    )
    country = str(candidate.get("country") or "").strip() or None

    result: dict[str, Any] = {
        "place_id": None,
        "place_name": name,
        "city": city,
        "country": country,
        "lat": round(float(candidate.get("lat", lat)), 6),
        "lon": round(float(candidate.get("lon", lon)), 6),
        "distance_m": _safe_round(candidate.get("distance"), 1),
        "confidence": "medium",
        "source": "reverse_geocode",
        "provider": "geoapify",
    }
    if captured_at:
        result["captured_at"] = captured_at
    return result


def _first_geoapify_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    if isinstance(payload.get("results"), list) and payload["results"]:
        first = payload["results"][0]
        return first if isinstance(first, dict) else None

    features = payload.get("features")
    if isinstance(features, list) and features:
        first_feature = features[0]
        if isinstance(first_feature, dict):
            props = first_feature.get("properties")
            return props if isinstance(props, dict) else None

    return None


def _load_places_with_coordinates() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id, name, city, country, lat, lon
            FROM places
            WHERE lat IS NOT NULL
              AND lon IS NOT NULL
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def _known_place_threshold_meters(accuracy_m: float | None) -> float:
    base = _env_float("LOCATION_INFERENCE_BASE_RADIUS_M", 120.0)
    max_radius = _env_float("LOCATION_INFERENCE_MAX_RADIUS_M", 500.0)
    multiplier = _env_float("LOCATION_INFERENCE_ACCURACY_MULTIPLIER", 1.5)
    dynamic = (accuracy_m or 0.0) * multiplier
    threshold = max(base, dynamic)
    return min(max_radius, threshold)


def _confidence_for_known_place(
    distance_m: float,
    threshold_m: float,
    accuracy_m: float | None,
) -> str:
    conservative_accuracy = max(accuracy_m or 0.0, 20.0)
    if distance_m <= min(conservative_accuracy, threshold_m * 0.33):
        return "high"
    if distance_m <= threshold_m * 0.66:
        return "medium"
    return "low"


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return radius_m * c


def _build_cache_key(location: dict[str, Any]) -> str:
    lat = round(float(location["lat"]), 4)
    lon = round(float(location["lon"]), 4)
    accuracy = _safe_round(location.get("accuracy_m"), 1) or 0.0
    return f"{lat}:{lon}:{accuracy}"


def _cache_ttl_seconds() -> float:
    return _env_float("LOCATION_INFERENCE_CACHE_SECONDS", 300.0)


def _read_cache(key: str) -> dict[str, Any] | None | object:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return _CACHE_MISS
        expires_at, payload = entry
        if expires_at < now:
            _CACHE.pop(key, None)
            return _CACHE_MISS
        return payload


def _write_cache(key: str, payload: dict[str, Any] | None) -> None:
    expires_at = time.time() + _cache_ttl_seconds()
    with _CACHE_LOCK:
        _CACHE[key] = (expires_at, payload)


def _safe_round(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_CACHE_MISS = object()
