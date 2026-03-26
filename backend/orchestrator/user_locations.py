from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import get_conn
from location_inference import infer_current_place
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def upsert_user_location(
    *,
    user_email: str,
    lat: float,
    lon: float,
    accuracy_m: float | None = None,
    captured_at: datetime | None = None,
    source: str | None = None,
    timezone_name: str | None = None,
    place_name: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    safe_source = (source or "unknown").strip() or "unknown"
    resolved_captured_at = captured_at or datetime.now(timezone.utc)

    enriched_place_name = (place_name or "").strip() or None
    enriched_city = (city or "").strip() or None
    enriched_country = (country or "").strip() or None

    if not (enriched_place_name and enriched_city and enriched_country):
        inferred = infer_current_place(
            {
                "lat": lat,
                "lon": lon,
                "accuracy_m": accuracy_m,
                "captured_at": resolved_captured_at.isoformat(),
            },
            user_email=user_email,
        )
        if isinstance(inferred, dict):
            enriched_place_name = enriched_place_name or str(inferred.get("place_name") or "").strip() or None
            enriched_city = enriched_city or str(inferred.get("city") or "").strip() or None
            enriched_country = (
                enriched_country or str(inferred.get("country") or "").strip() or None
            )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_last_known_locations (
              user_email,
              lat,
              lon,
              accuracy_m,
              captured_at,
              source,
              timezone,
              place_name,
              city,
              country
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_email)
            DO UPDATE SET
              lat = EXCLUDED.lat,
              lon = EXCLUDED.lon,
              accuracy_m = EXCLUDED.accuracy_m,
              captured_at = EXCLUDED.captured_at,
              source = EXCLUDED.source,
              timezone = EXCLUDED.timezone,
              place_name = EXCLUDED.place_name,
              city = EXCLUDED.city,
              country = EXCLUDED.country,
              updated_at = NOW()
            RETURNING user_email, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country, updated_at
            """,
            (
                user_email,
                lat,
                lon,
                accuracy_m,
                resolved_captured_at,
                safe_source,
                (timezone_name or "").strip() or None,
                enriched_place_name,
                enriched_city,
                enriched_country,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    logger.info("[user_locations] Updated user location for user=%s", user_email)
    return dict(row or {})


def get_last_known_location(user_email: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country, updated_at
            FROM user_last_known_locations
            WHERE user_email = %s
            """,
            (user_email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)
