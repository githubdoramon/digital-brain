from __future__ import annotations

from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Any

from db import get_conn
from location_inference import infer_current_place
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

LOCATION_DEDUPE_MIN_DISTANCE_METERS = 50.0


def _distance_meters(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_lat = float(first.get("lat") or 0.0)
    first_lon = float(first.get("lon") or 0.0)
    second_lat = float(second.get("lat") or 0.0)
    second_lon = float(second.get("lon") or 0.0)

    earth_radius_meters = 6_371_000
    lat_delta = radians(second_lat - first_lat)
    lon_delta = radians(second_lon - first_lon)
    first_lat_radians = radians(first_lat)
    second_lat_radians = radians(second_lat)

    haversine = sin(lat_delta / 2) ** 2 + cos(first_lat_radians) * cos(second_lat_radians) * sin(lon_delta / 2) ** 2
    arc = 2 * atan2(sqrt(haversine), sqrt(1 - haversine))
    return earth_radius_meters * arc


def _normalize_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def _should_skip_location_update(
    *,
    existing: dict[str, Any] | None,
    lat: float,
    lon: float,
    captured_at: datetime,
) -> bool:
    if not existing:
        return False

    existing_captured_at = _normalize_timestamp(existing.get("captured_at"))
    if existing_captured_at and captured_at <= existing_captured_at:
        return True

    moved_meters = _distance_meters(existing, {"lat": lat, "lon": lon})
    return moved_meters < LOCATION_DEDUPE_MIN_DISTANCE_METERS


def _build_skip_reason(
    *,
    existing: dict[str, Any] | None,
    lat: float,
    lon: float,
    captured_at: datetime,
) -> str:
    if not existing:
        return "no_existing_location"

    existing_captured_at = _normalize_timestamp(existing.get("captured_at"))
    if existing_captured_at and captured_at <= existing_captured_at:
        return "stale_or_duplicate_timestamp"

    moved_meters = _distance_meters(existing, {"lat": lat, "lon": lon})
    if moved_meters >= LOCATION_DEDUPE_MIN_DISTANCE_METERS:
        return "significant_movement"
    return "movement_below_threshold"


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
    debug_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_source = (source or "unknown").strip() or "unknown"
    resolved_captured_at = captured_at or datetime.now(timezone.utc)
    received_at = datetime.now(timezone.utc)
    debug_context = debug_context or {}
    debug_request_id = str(debug_context.get("debug_request_id") or "").strip() or None
    logger.info(
        "[user_locations] Record request user=%s lat=%.6f lon=%.6f source=%s captured_at=%s received_at=%s debug_request_id=%s batch_id=%s sample_index=%s/%s app_state=%s",
        user_email,
        lat,
        lon,
        safe_source,
        resolved_captured_at.isoformat(),
        received_at.isoformat(),
        debug_request_id or "none",
        debug_context.get("batch_id") or "none",
        debug_context.get("sample_index") or "none",
        debug_context.get("sample_count") or "none",
        debug_context.get("app_state") or "unknown",
    )

    enriched_place_name = (place_name or "").strip() or None
    enriched_city = (city or "").strip() or None
    enriched_country = (country or "").strip() or None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country, updated_at
            FROM user_location_history
            WHERE user_email = %s
            ORDER BY captured_at DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (user_email,),
        )
        existing = dict(cur.fetchone() or {})

    if debug_request_id:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mobile_location_request_dedupe (debug_request_id, user_email, captured_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (debug_request_id) DO NOTHING
                RETURNING debug_request_id
                """,
                (debug_request_id, user_email, resolved_captured_at),
            )
            dedupe_row = cur.fetchone()
            conn.commit()
        if not dedupe_row:
            logger.info(
                "[user_locations] Ignored duplicate mobile location request user=%s debug_request_id=%s batch_id=%s sample_index=%s/%s",
                user_email,
                debug_request_id,
                debug_context.get("batch_id") or "none",
                debug_context.get("sample_index") or "none",
                debug_context.get("sample_count") or "none",
            )
            return existing if existing else {}

    existing_captured_at = _normalize_timestamp(existing.get("captured_at")) if existing else None
    moved_meters_from_latest = (
        _distance_meters(existing, {"lat": lat, "lon": lon}) if existing else None
    )
    ingestion_delay_seconds = max(0.0, (received_at - resolved_captured_at).total_seconds())

    if _should_skip_location_update(
        existing=existing,
        lat=lat,
        lon=lon,
        captured_at=resolved_captured_at,
    ):
        skip_reason = _build_skip_reason(
            existing=existing,
            lat=lat,
            lon=lon,
            captured_at=resolved_captured_at,
        )
        logger.info(
            "[user_locations] Skipped update for user=%s reason=%s existing_captured_at=%s moved_meters_from_latest=%s ingestion_delay_seconds=%.1f debug_request_id=%s batch_id=%s sample_index=%s/%s",
            user_email,
            skip_reason,
            existing_captured_at,
            f"{moved_meters_from_latest:.1f}" if moved_meters_from_latest is not None else "none",
            ingestion_delay_seconds,
            debug_request_id or "none",
            debug_context.get("batch_id") or "none",
            debug_context.get("sample_index") or "none",
            debug_context.get("sample_count") or "none",
        )
        return existing

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
            INSERT INTO user_location_history (
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

    logger.info(
        "[user_locations] Recorded user location for user=%s lat=%.6f lon=%.6f source=%s stored_captured_at=%s received_at=%s ingestion_delay_seconds=%.1f moved_meters_from_latest=%s debug_request_id=%s batch_id=%s sample_index=%s/%s",
        user_email,
        lat,
        lon,
        safe_source,
        resolved_captured_at.isoformat(),
        received_at.isoformat(),
        ingestion_delay_seconds,
        f"{moved_meters_from_latest:.1f}" if moved_meters_from_latest is not None else "none",
        debug_request_id or "none",
        debug_context.get("batch_id") or "none",
        debug_context.get("sample_index") or "none",
        debug_context.get("sample_count") or "none",
    )
    return dict(row or {})


def get_last_known_location(user_email: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, lat, lon, accuracy_m, captured_at, source, timezone, place_name, city, country, updated_at
            FROM user_location_history
            WHERE user_email = %s
            ORDER BY captured_at DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (user_email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)
