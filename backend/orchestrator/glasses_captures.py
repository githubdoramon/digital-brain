from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from io import BytesIO
from typing import Any, BinaryIO

import immich_client
from db import get_conn
from observability.logger import get_runtime_logger

IMMICH_ALBUM_NAME = "Ramon eyes capture"
IMMICH_DEVICE_ID = "digital-brain-glasses"
logger = get_runtime_logger(__name__)


class GlassesCaptureError(RuntimeError):
    """Raised when a glasses capture cannot be made durable in Immich."""


def upload_capture(
    *,
    user_email: str,
    capture_id: str,
    media_bytes: bytes | BinaryIO,
    filename: str,
    mime_type: str | None,
    captured_at: datetime | None,
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user = _required(user_email, "user_email")
    stable_capture_id = _required(capture_id, "capture_id")
    stream, size_bytes, checksum, immich_checksum = _prepare_media(media_bytes)
    existing = _find_capture(user, stable_capture_id, checksum)
    if existing:
        return existing

    upload = immich_client.upload_asset_stream(
        stream,
        filename=filename.strip() or f"{stable_capture_id}.bin",
        mime_type=mime_type,
        taken_at=captured_at,
        device_asset_id=stable_capture_id,
        size_bytes=size_bytes,
        checksum_header=immich_checksum,
        device_id=IMMICH_DEVICE_ID,
    )
    asset_id = immich_client.extract_asset_id(upload)
    if not asset_id:
        raise GlassesCaptureError("Immich upload returned no asset ID")

    # Confirm the asset is readable before acknowledging success to the phone.
    asset = immich_client.fetch_asset(asset_id)
    location_update = None
    latitude = _finite_number((location or {}).get("lat"))
    longitude = _finite_number((location or {}).get("lon"))
    if latitude is not None and longitude is not None:
        location_update = immich_client.update_asset_location(
            asset_id,
            latitude=latitude,
            longitude=longitude,
        )
    album = immich_client.ensure_album(IMMICH_ALBUM_NAME, asset_id)
    row = _insert_capture(
        user_email=user,
        capture_id=stable_capture_id,
        checksum=checksum,
        asset_id=asset_id,
        album_id=album.get("id"),
        filename=filename,
        mime_type=mime_type,
        captured_at=captured_at,
        location=location or {},
        metadata={
            "immich_upload": upload,
            "immich_asset": asset,
            "immich_location_update": location_update,
        },
    )
    logger.info(
        "[glasses_captures] capture committed capture_id=%s asset_id=%s size_bytes=%d",
        stable_capture_id,
        asset_id,
        size_bytes,
    )
    return row


def _prepare_media(media: bytes | BinaryIO) -> tuple[BinaryIO, int, str, str]:
    """Hash a seekable upload in bounded chunks and rewind it for Immich."""
    if isinstance(media, bytes):
        stream: BinaryIO = BytesIO(media)
    else:
        stream = media
    try:
        stream.seek(0)
    except (AttributeError, OSError) as exc:
        raise GlassesCaptureError("Media payload must be seekable") from exc

    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    size_bytes = 0
    while True:
        chunk = stream.read(immich_client.UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        sha256.update(chunk)
        sha1.update(chunk)
        size_bytes += len(chunk)
    if size_bytes <= 0:
        raise GlassesCaptureError("Media payload is empty")
    stream.seek(0)
    return stream, size_bytes, sha256.hexdigest(), base64.b64encode(sha1.digest()).decode("ascii")


def _find_capture(user_email: str, capture_id: str, checksum: str) -> dict[str, Any] | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, capture_id, checksum, immich_asset_id, immich_album_id,
                   original_file_name, mime_type, captured_at, location, metadata,
                   created_at, updated_at
            FROM glasses_captures
            WHERE user_email = %s AND (capture_id = %s OR checksum = %s)
            LIMIT 1
            """,
            (user_email, capture_id, checksum),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _insert_capture(**values: Any) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO glasses_captures (
              user_email, capture_id, checksum, immich_asset_id, immich_album_id,
              original_file_name, mime_type, captured_at, location, metadata
            ) VALUES (%(user_email)s, %(capture_id)s, %(checksum)s, %(asset_id)s, %(album_id)s,
                      %(filename)s, %(mime_type)s, %(captured_at)s, %(location)s, %(metadata)s)
            ON CONFLICT (user_email, capture_id) DO UPDATE SET
              updated_at = NOW(), immich_asset_id = EXCLUDED.immich_asset_id,
              immich_album_id = EXCLUDED.immich_album_id
            RETURNING user_email, capture_id, checksum, immich_asset_id, immich_album_id,
                      original_file_name, mime_type, captured_at, location, metadata,
                      created_at, updated_at
            """,
            {
                **values,
                "location": _json(values["location"]),
                "metadata": _json(values["metadata"]),
            },
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise GlassesCaptureError("Capture record was not committed")
    return dict(row)


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _required(value: str, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise GlassesCaptureError(f"{field} is required")
    return cleaned


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
