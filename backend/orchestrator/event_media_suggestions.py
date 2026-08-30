from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Any

import immich_client
from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

TIME_TOLERANCE = timedelta(minutes=30)
DEFAULT_EVENT_DURATION = timedelta(hours=1)
MAX_DISTANCE_METERS = 250.0


def suggest_event_media(
    *,
    start_at: datetime | str | None,
    end_at: datetime | str | None,
    event_lat: float | None = None,
    event_lon: float | None = None,
) -> list[dict[str, Any]]:
    """Find all time-matching Immich media and rank them by temporal fit.

    Time is the primary signal. A 250m distance constraint is applied only
    when both the event and asset have coordinates; missing asset GPS never
    disqualifies an otherwise time-matching asset.
    """
    start = _parse_datetime(start_at)
    if start is None:
        logger.warning("[event_media] search skipped because event start is invalid start_at=%r", start_at)
        return []
    parsed_end = _parse_datetime(end_at)
    end = parsed_end or (start + DEFAULT_EVENT_DURATION)
    if end < start:
        logger.warning(
            "[event_media] search skipped because event end precedes start start=%s end=%s",
            start.isoformat(),
            end.isoformat(),
        )
        return []

    taken_after = start - TIME_TOLERANCE
    taken_before = end + TIME_TOLERANCE
    logger.info(
        "[event_media] search started start=%s end=%s end_defaulted=%s "
        "taken_after=%s taken_before=%s event_coordinates_present=%s",
        start.isoformat(),
        end.isoformat(),
        parsed_end is None,
        taken_after.isoformat(),
        taken_before.isoformat(),
        event_lat is not None and event_lon is not None,
    )

    try:
        config = immich_client.get_immich_config()
        logger.debug(
            "[event_media] Immich configuration available server_configured=%s "
            "api_key_configured=%s timeout=%s",
            bool(config.base_url),
            bool(config.api_key),
            config.http_timeout,
        )
        assets = immich_client.search_assets_by_time(
            taken_after=taken_after,
            taken_before=taken_before,
            config=config,
        )
    except Exception as exc:
        logger.warning("[event_media] Immich suggestion search failed: %s", exc)
        return []

    suggestions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    skipped = {
        "non_object": 0,
        "missing_id": 0,
        "duplicate_id": 0,
        "missing_file_created_at": 0,
        "outside_distance": 0,
    }
    logger.info("[event_media] Immich search returned raw_assets=%d", len(assets))
    for asset in assets:
        if not isinstance(asset, dict):
            skipped["non_object"] += 1
            continue
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            skipped["missing_id"] += 1
            logger.debug("[event_media] asset rejected reason=missing_id")
            continue
        if asset_id in seen_ids:
            skipped["duplicate_id"] += 1
            logger.debug("[event_media] asset rejected asset_id=%s reason=duplicate_id", asset_id)
            continue
        captured_at = _parse_datetime(asset.get("fileCreatedAt"))
        if captured_at is None:
            skipped["missing_file_created_at"] += 1
            exif = asset.get("exifInfo") if isinstance(asset.get("exifInfo"), dict) else {}
            logger.debug(
                "[event_media] asset rejected asset_id=%s filename=%s reason=missing_file_created_at "
                "file_created_at=%r local_date_time=%r exif_date_time_original=%r",
                asset_id,
                asset.get("originalFileName"),
                asset.get("fileCreatedAt"),
                asset.get("localDateTime"),
                exif.get("dateTimeOriginal"),
            )
            continue
        asset_lat, asset_lon = _asset_coordinates(asset)
        distance_m = None
        if event_lat is not None and event_lon is not None and asset_lat is not None and asset_lon is not None:
            distance_m = _distance_meters(event_lat, event_lon, asset_lat, asset_lon)
            if distance_m > MAX_DISTANCE_METERS:
                skipped["outside_distance"] += 1
                logger.debug(
                    "[event_media] asset rejected asset_id=%s filename=%s reason=outside_distance "
                    "distance_m=%.2f max_distance_m=%.2f captured_at=%s",
                    asset_id,
                    asset.get("originalFileName"),
                    distance_m,
                    MAX_DISTANCE_METERS,
                    captured_at.isoformat(),
                )
                continue

        seen_ids.add(asset_id)
        temporal_distance_seconds = _distance_to_interval_seconds(captured_at, start, end)
        exif = asset.get("exifInfo") if isinstance(asset.get("exifInfo"), dict) else {}
        logger.debug(
            "[event_media] asset accepted asset_id=%s filename=%s type=%s "
            "file_created_at=%r local_date_time=%r exif_date_time_original=%r "
            "captured_at=%s temporal_distance_seconds=%.3f distance_m=%s has_gps=%s",
            asset_id,
            asset.get("originalFileName"),
            asset.get("type"),
            asset.get("fileCreatedAt"),
            asset.get("localDateTime"),
            exif.get("dateTimeOriginal"),
            captured_at.isoformat(),
            temporal_distance_seconds,
            round(distance_m, 2) if distance_m is not None else None,
            asset_lat is not None and asset_lon is not None,
        )
        suggestions.append(
            _serialize_suggestion(
                asset,
                captured_at=captured_at,
                distance_m=distance_m,
                temporal_distance_seconds=temporal_distance_seconds,
            )
        )

    suggestions.sort(
        key=lambda item: (
            float(item.get("temporal_distance_seconds") or 0),
            float(item.get("distance_m")) if item.get("distance_m") is not None else float("inf"),
            str(item.get("captured_at") or ""),
            str(item.get("asset_id") or ""),
        )
    )
    logger.info(
        "[event_media] search completed raw_assets=%d suggestions=%d "
        "skipped_non_object=%d skipped_missing_id=%d skipped_duplicate_id=%d "
        "skipped_missing_file_created_at=%d skipped_outside_distance=%d",
        len(assets),
        len(suggestions),
        skipped["non_object"],
        skipped["missing_id"],
        skipped["duplicate_id"],
        skipped["missing_file_created_at"],
        skipped["outside_distance"],
    )
    return suggestions


def list_proposal_media(proposal_id: str) -> list[dict[str, Any]]:
    normalized = str(proposal_id or "").strip()
    if not normalized:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT proposal_id, immich_asset_id, media_type, original_file_name,
                   mime_type, captured_at, width, height, duration_seconds,
                   distance_m, temporal_distance_seconds, has_gps, status,
                   metadata, created_at, updated_at
            FROM proposed_event_media
            WHERE proposal_id = %s
            ORDER BY temporal_distance_seconds ASC,
                     distance_m ASC NULLS LAST,
                     captured_at ASC NULLS LAST,
                     immich_asset_id ASC
            """,
            (normalized,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [_serialize_stored_suggestion(row) for row in rows]


def persist_proposal_media(proposal_id: str, suggestions: list[dict[str, Any]]) -> None:
    normalized = str(proposal_id or "").strip()
    if not normalized:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM proposed_event_media WHERE proposal_id = %s", (normalized,))
        for suggestion in suggestions:
            asset_id = str(suggestion.get("asset_id") or "").strip()
            if not asset_id:
                continue
            cur.execute(
                """
                INSERT INTO proposed_event_media (
                    proposal_id, immich_asset_id, media_type, original_file_name,
                    mime_type, captured_at, width, height, duration_seconds,
                    distance_m, temporal_distance_seconds, has_gps, status, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'included', %s::jsonb)
                ON CONFLICT (proposal_id, immich_asset_id) DO UPDATE SET
                    media_type = EXCLUDED.media_type,
                    original_file_name = EXCLUDED.original_file_name,
                    mime_type = EXCLUDED.mime_type,
                    captured_at = EXCLUDED.captured_at,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    duration_seconds = EXCLUDED.duration_seconds,
                    distance_m = EXCLUDED.distance_m,
                    temporal_distance_seconds = EXCLUDED.temporal_distance_seconds,
                    has_gps = EXCLUDED.has_gps,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                (
                    normalized,
                    asset_id,
                    suggestion.get("media_type"),
                    suggestion.get("file_name"),
                    suggestion.get("mime_type"),
                    _parse_datetime(suggestion.get("captured_at")),
                    suggestion.get("width"),
                    suggestion.get("height"),
                    suggestion.get("duration_seconds"),
                    suggestion.get("distance_m"),
                    suggestion.get("temporal_distance_seconds"),
                    suggestion.get("has_gps", False),
                    json.dumps({"match_reasons": suggestion.get("match_reasons") or []}),
                ),
            )
        conn.commit()


def set_proposal_media_selection(proposal_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
    normalized = str(proposal_id or "").strip()
    selected = {str(asset_id or "").strip() for asset_id in asset_ids if str(asset_id or "").strip()}
    if not normalized:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT immich_asset_id FROM proposed_event_media WHERE proposal_id = %s",
            (normalized,),
        )
        available = {str(row["immich_asset_id"]) for row in cur.fetchall()}
        if not selected.issubset(available):
            raise ValueError("One or more selected media items are not proposal candidates")
        cur.execute(
            """
            UPDATE proposed_event_media
            SET status = CASE WHEN immich_asset_id = ANY(%s) THEN 'included' ELSE 'removed' END,
                updated_at = NOW()
            WHERE proposal_id = %s
            """,
            (list(selected), normalized),
        )
        conn.commit()
    return list_proposal_media(normalized)


def selected_proposal_asset_ids(proposal_id: str) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT immich_asset_id
            FROM proposed_event_media
            WHERE proposal_id = %s AND status = 'included'
            ORDER BY temporal_distance_seconds ASC, immich_asset_id ASC
            """,
            (str(proposal_id or "").strip(),),
        )
        return [str(row["immich_asset_id"]) for row in cur.fetchall()]


def _serialize_suggestion(
    asset: dict[str, Any],
    *,
    captured_at: datetime,
    distance_m: float | None,
    temporal_distance_seconds: float,
) -> dict[str, Any]:
    asset_id = str(asset.get("id") or "").strip()
    exif = asset.get("exifInfo") if isinstance(asset.get("exifInfo"), dict) else {}
    asset_lat, asset_lon = _asset_coordinates(asset)
    media_type = str(asset.get("type") or "").strip().lower() or None
    reasons = ["time"]
    if distance_m is not None:
        reasons.append("proximity")
    return {
        "asset_id": asset_id,
        "media_type": media_type,
        "file_name": str(asset.get("originalFileName") or "").strip() or None,
        "mime_type": str(asset.get("originalMimeType") or "").strip() or None,
        "captured_at": captured_at.isoformat(),
        "width": _int_or_none(asset.get("width") or exif.get("exifImageWidth")),
        "height": _int_or_none(asset.get("height") or exif.get("exifImageHeight")),
        "duration_seconds": _duration_seconds(asset.get("duration")),
        "distance_m": round(distance_m, 2) if distance_m is not None else None,
        "temporal_distance_seconds": round(temporal_distance_seconds, 3),
        "has_gps": asset_lat is not None and asset_lon is not None,
        "status": "included",
        "match_reasons": reasons,
        "thumbnail_path": f"/mobile/event-media/{asset_id}/thumbnail",
    }


def _serialize_stored_suggestion(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    captured_at = row.get("captured_at")
    if hasattr(captured_at, "isoformat"):
        captured_at = captured_at.isoformat()
    return {
        "asset_id": str(row.get("immich_asset_id") or ""),
        "media_type": row.get("media_type"),
        "file_name": row.get("original_file_name"),
        "mime_type": row.get("mime_type"),
        "captured_at": captured_at,
        "width": row.get("width"),
        "height": row.get("height"),
        "duration_seconds": row.get("duration_seconds"),
        "distance_m": row.get("distance_m"),
        "temporal_distance_seconds": row.get("temporal_distance_seconds"),
        "has_gps": bool(row.get("has_gps")),
        "status": row.get("status") or "included",
        "match_reasons": metadata.get("match_reasons") or [],
        "thumbnail_path": f"/mobile/event-media/{row.get('immich_asset_id')}/thumbnail",
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _asset_coordinates(asset: dict[str, Any]) -> tuple[float | None, float | None]:
    exif = asset.get("exifInfo") if isinstance(asset.get("exifInfo"), dict) else {}
    return _float_or_none(exif.get("latitude")), _float_or_none(exif.get("longitude"))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def _distance_to_interval_seconds(value: datetime, start: datetime, end: datetime) -> float:
    if value < start:
        return (start - value).total_seconds()
    if value > end:
        return (value - end).total_seconds()
    return 0.0


def _distance_meters(first_lat: float, first_lon: float, second_lat: float, second_lon: float) -> float:
    earth_radius_meters = 6_371_000
    lat_delta = radians(second_lat - first_lat)
    lon_delta = radians(second_lon - first_lon)
    first_lat_radians = radians(first_lat)
    second_lat_radians = radians(second_lat)
    haversine = sin(lat_delta / 2) ** 2 + cos(first_lat_radians) * cos(second_lat_radians) * sin(lon_delta / 2) ** 2
    return earth_radius_meters * 2 * atan2(sqrt(haversine), sqrt(1 - haversine))
