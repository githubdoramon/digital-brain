from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from db import get_conn
from geo_utils import haversine_meters
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CACHE_TOLERANCE_METERS = 150.0


def lookup_search(
    *,
    lat: float,
    lon: float,
    tolerance_m: float,
) -> list[dict[str, Any]] | None:
    """Return a recent cached search result when its prior center is nearby."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT search_id, center_lat, center_lon, radius_m, provider_place_ids,
                   fetched_at
            FROM google_place_search_cache
            WHERE fetched_at >= NOW() - (%s * INTERVAL '1 second')
            ORDER BY fetched_at DESC
            LIMIT 100
            """,
            (_cache_ttl_seconds(),),
        )
        searches = [dict(row) for row in cur.fetchall()]

        matching = [
            row
            for row in searches
            if haversine_meters(lat, lon, float(row["center_lat"]), float(row["center_lon"]))
            <= max(0.0, tolerance_m)
        ]
        if not matching:
            return None

        ids: list[str] = []
        for row in matching:
            for provider_place_id in row.get("provider_place_ids") or []:
                clean_id = str(provider_place_id or "").strip()
                if clean_id and clean_id not in ids:
                    ids.append(clean_id)
        if not ids:
            return []

        cur.execute(
            """
            SELECT provider_place_id, title, lat, lon, primary_type, types,
                   formatted_address, city, country, business_status, fetched_at
            FROM google_place_lookup_cache
            WHERE provider_place_id = ANY(%s)
            """,
            (ids,),
        )
        candidates = [dict(row) for row in cur.fetchall()]
        if ids and not candidates:
            # A prune job may remove candidate rows before its coverage rows;
            # treat that as a cache miss so the caller can refresh Google data.
            conn.rollback()
            return None
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("business_status") or "").upper() != "CLOSED_PERMANENTLY"
        ]
        cur.execute(
            """
            UPDATE google_place_search_cache
            SET last_used_at = NOW()
            WHERE search_id = ANY(%s)
            """,
            ([row["search_id"] for row in matching],),
        )
        conn.commit()

    candidates.sort(
        key=lambda item: haversine_meters(lat, lon, float(item["lat"]), float(item["lon"]))
    )
    return candidates


def store_search(*, center_lat: float, center_lon: float, radius_m: float, candidates: list[dict[str, Any]]) -> None:
    fetched_at = datetime.now(timezone.utc)
    provider_ids: list[str] = []
    with get_conn() as conn, conn.cursor() as cur:
        for candidate in candidates:
            provider_place_id = str(candidate.get("provider_place_id") or "").strip()
            title = str(candidate.get("title") or "").strip()
            lat = _safe_float(candidate.get("lat"))
            lon = _safe_float(candidate.get("lon"))
            if not provider_place_id or not title or lat is None or lon is None:
                continue
            provider_ids.append(provider_place_id)
            cur.execute(
                """
                INSERT INTO google_place_lookup_cache (
                    provider_place_id, title, lat, lon, primary_type, types,
                    formatted_address, city, country, business_status,
                    fetched_at, last_seen_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (provider_place_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    primary_type = EXCLUDED.primary_type,
                    types = EXCLUDED.types,
                    formatted_address = EXCLUDED.formatted_address,
                    city = EXCLUDED.city,
                    country = EXCLUDED.country,
                    business_status = EXCLUDED.business_status,
                    fetched_at = EXCLUDED.fetched_at,
                    last_seen_at = NOW()
                """,
                (
                    provider_place_id,
                    title,
                    lat,
                    lon,
                    candidate.get("primary_type"),
                    candidate.get("types") or [],
                    candidate.get("formatted_address"),
                    candidate.get("city"),
                    candidate.get("country"),
                    candidate.get("business_status"),
                    candidate.get("fetched_at") or fetched_at,
                ),
            )
        cur.execute(
            """
            INSERT INTO google_place_search_cache (
                center_lat, center_lon, radius_m, provider_place_ids,
                fetched_at, last_used_at
            )
            VALUES (%s,%s,%s,%s,%s,NOW())
            """,
            (center_lat, center_lon, radius_m, provider_ids, fetched_at),
        )
        conn.commit()


def get_candidate(provider_place_id: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_place_id, title, lat, lon, primary_type, types,
                   formatted_address, city, country, business_status, fetched_at
            FROM google_place_lookup_cache
            WHERE provider_place_id = %s
            """,
            (provider_place_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_canonical_place_id(provider_place_id: str) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id
            FROM google_place_canonical_links
            WHERE provider = 'google' AND provider_place_id = %s
            """,
            (provider_place_id,),
        )
        row = cur.fetchone()
    return str(row["place_id"]) if row else None


def link_canonical_place(provider_place_id: str, place_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO google_place_canonical_links (provider, provider_place_id, place_id)
            VALUES ('google', %s, %s)
            ON CONFLICT (provider, provider_place_id) DO NOTHING
            """,
            (provider_place_id, place_id),
        )
        conn.commit()


def cache_tolerance_meters() -> float:
    return _env_float("GOOGLE_PLACES_CACHE_TOLERANCE_M", DEFAULT_CACHE_TOLERANCE_METERS)


def _cache_ttl_seconds() -> float:
    return _env_float("GOOGLE_PLACES_CACHE_SECONDS", DEFAULT_CACHE_TTL_SECONDS)


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except ValueError:
        return default


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
