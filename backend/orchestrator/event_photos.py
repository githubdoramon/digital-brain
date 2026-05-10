from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

import contacts as contacts_service
import immich_client
from db import get_conn


class EventPhotoError(RuntimeError):
    """Raised when an event photo operation fails."""


IMMICH_EVENT_PHOTO_DEVICE_ID = (
    os.getenv("IMMICH_EVENT_PHOTO_DEVICE_ID") or "digital-brain-events"
).strip()


def attach_event_photo(
    event_id: str,
    *,
    image_bytes: bytes,
    filename: str,
    mime_type: str | None,
    captured_at: datetime | None,
    local_asset_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    normalized_event_id = _normalize_required_string(event_id, "event_id")
    if not image_bytes:
        raise EventPhotoError("Image payload is empty")

    if not _event_exists(normalized_event_id):
        raise EventPhotoError("Event not found")

    checksum = hashlib.sha256(image_bytes).hexdigest()
    upload_result = immich_client.upload_asset(
        image_bytes,
        filename=filename,
        mime_type=mime_type,
        taken_at=captured_at,
        device_asset_id=_build_device_asset_id(normalized_event_id, local_asset_id),
        device_id=IMMICH_EVENT_PHOTO_DEVICE_ID,
    )
    asset_id = immich_client.extract_asset_id(upload_result)
    if not asset_id:
        raise EventPhotoError("Immich upload succeeded but no asset ID was returned")

    asset_payload, detected_people = _fetch_immich_asset_context(asset_id)
    tagged_contacts = _resolve_tagged_contacts(detected_people)
    metadata = {
        "immich_upload": upload_result,
        "immich_asset": asset_payload,
        "detected_people": detected_people,
    }
    photo_row = _upsert_event_photo(
        normalized_event_id,
        asset_id=asset_id,
        checksum=checksum,
        filename=filename,
        mime_type=mime_type,
        captured_at=captured_at,
        local_asset_id=local_asset_id,
        source=source,
        metadata=metadata,
        tagged_contacts=tagged_contacts,
    )
    _merge_event_contacts(normalized_event_id, tagged_contacts)
    return photo_row


def list_event_photos(event_id: str) -> list[dict[str, Any]]:
    return list_event_photos_for_events([event_id]).get(event_id, [])


def list_event_photos_for_events(event_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    normalized_event_ids = [str(event_id or "").strip() for event_id in event_ids if str(event_id or "").strip()]
    if not normalized_event_ids:
        return {}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.event_id,
                   ep.immich_asset_id,
                   ep.checksum,
                   ep.original_file_name,
                   ep.mime_type,
                   ep.captured_at,
                   ep.local_asset_id,
                   ep.source,
                   ep.width,
                   ep.height,
                   ep.created_at,
                   ep.updated_at,
                   ep.metadata,
                   epc.contact_id,
                   c.display_name
            FROM event_photos ep
            LEFT JOIN event_photo_contacts epc
              ON epc.event_id = ep.event_id
             AND epc.immich_asset_id = ep.immich_asset_id
            LEFT JOIN contacts c
              ON c.contact_id = epc.contact_id
            WHERE ep.event_id = ANY(%s)
            ORDER BY ep.captured_at DESC NULLS LAST,
                     ep.created_at DESC,
                     ep.immich_asset_id,
                     c.display_name NULLS LAST,
                     epc.contact_id NULLS LAST
            """,
            (normalized_event_ids,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    photos_by_event: dict[str, list[dict[str, Any]]] = {event_id: [] for event_id in normalized_event_ids}
    photos_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "").strip()
        asset_id = str(row.get("immich_asset_id") or "").strip()
        if not event_id or not asset_id:
            continue
        key = (event_id, asset_id)
        photo = photos_by_key.get(key)
        if photo is None:
            photo = _serialize_photo_row(row)
            photo = _refresh_photo_people_metadata(event_id, photo)
            photos_by_key[key] = photo
            photos_by_event.setdefault(event_id, []).append(photo)
        contact_id = str(row.get("contact_id") or "").strip()
        if contact_id and not any(
            existing.get("contact_id") == contact_id for existing in photo["tagged_contacts"]
        ):
            photo["tagged_contacts"].append(
                {
                    "contact_id": contact_id,
                    "display_name": str(row.get("display_name") or contact_id).strip() or contact_id,
                }
            )
    return photos_by_event


def unlink_event_photo(event_id: str, asset_id: str) -> bool:
    normalized_event_id = _normalize_required_string(event_id, "event_id")
    normalized_asset_id = _normalize_required_string(asset_id, "asset_id")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM event_photos WHERE event_id = %s AND immich_asset_id = %s",
            (normalized_event_id, normalized_asset_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def fetch_event_photo_thumbnail(event_id: str, asset_id: str) -> tuple[bytes, str]:
    normalized_event_id = _normalize_required_string(event_id, "event_id")
    normalized_asset_id = _normalize_required_string(asset_id, "asset_id")
    if not _event_photo_exists(normalized_event_id, normalized_asset_id):
        raise EventPhotoError("Event photo not found")
    return immich_client.fetch_asset_thumbnail(normalized_asset_id)


def _upsert_event_photo(
    event_id: str,
    *,
    asset_id: str,
    checksum: str,
    filename: str,
    mime_type: str | None,
    captured_at: datetime | None,
    local_asset_id: str | None,
    source: str | None,
    metadata: dict[str, Any],
    tagged_contacts: list[dict[str, str]],
) -> dict[str, Any]:
    normalized_local_asset_id = str(local_asset_id or "").strip() or None
    normalized_source = str(source or "").strip() or None
    normalized_filename = filename.strip() or f"{asset_id}.jpg"
    asset_width = _coerce_int(metadata.get("immich_asset", {}).get("exifInfo", {}).get("exifImageWidth"))
    asset_height = _coerce_int(metadata.get("immich_asset", {}).get("exifInfo", {}).get("exifImageHeight"))
    asset_mime_type = (
        str(metadata.get("immich_asset", {}).get("originalMimeType") or "").strip() or mime_type or None
    )
    captured_at_value = captured_at or _parse_iso_datetime(
        metadata.get("immich_asset", {}).get("fileCreatedAt")
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_photos (
              event_id,
              immich_asset_id,
              checksum,
              original_file_name,
              mime_type,
              captured_at,
              local_asset_id,
              source,
              width,
              height,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, immich_asset_id) DO UPDATE
              SET checksum = EXCLUDED.checksum,
                  original_file_name = EXCLUDED.original_file_name,
                  mime_type = EXCLUDED.mime_type,
                  captured_at = EXCLUDED.captured_at,
                  local_asset_id = EXCLUDED.local_asset_id,
                  source = EXCLUDED.source,
                  width = EXCLUDED.width,
                  height = EXCLUDED.height,
                  metadata = EXCLUDED.metadata,
                  updated_at = NOW()
            RETURNING event_id,
                      immich_asset_id,
                      checksum,
                      original_file_name,
                      mime_type,
                      captured_at,
                      local_asset_id,
                      source,
                      width,
                      height,
                      created_at,
                      updated_at,
                      metadata
            """,
            (
                event_id,
                asset_id,
                checksum,
                normalized_filename,
                asset_mime_type,
                captured_at_value,
                normalized_local_asset_id,
                normalized_source,
                asset_width,
                asset_height,
                Jsonb(metadata),
            ),
        )
        photo_row = dict(cur.fetchone() or {})
        cur.execute(
            "DELETE FROM event_photo_contacts WHERE event_id = %s AND immich_asset_id = %s",
            (event_id, asset_id),
        )
        if tagged_contacts:
            cur.executemany(
                """
                INSERT INTO event_photo_contacts (event_id, immich_asset_id, contact_id, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_id, immich_asset_id, contact_id) DO UPDATE
                  SET source = EXCLUDED.source,
                      updated_at = NOW()
                """,
                [
                    (event_id, asset_id, contact["contact_id"], "immich_tag")
                    for contact in tagged_contacts
                ],
            )
        conn.commit()

    photo = _serialize_photo_row(photo_row)
    photo["tagged_contacts"] = tagged_contacts
    return photo


def _resolve_tagged_contacts(asset_payload: dict[str, Any]) -> list[dict[str, str]]:
    return _resolve_tagged_contacts_from_detected_people(_resolve_detected_people(asset_payload))


def _resolve_tagged_contacts_from_detected_people(
    detected_people: Sequence[dict[str, Any]],
) -> list[dict[str, str]]:
    tagged_contacts: list[dict[str, str]] = []
    seen_contact_ids: set[str] = set()
    for person in detected_people:
        contact_id = str(person.get("contact_id") or "").strip()
        if not contact_id or contact_id in seen_contact_ids:
            continue
        seen_contact_ids.add(contact_id)
        tagged_contacts.append(
            {
                "contact_id": contact_id,
                "display_name": str(person.get("display_name") or contact_id).strip() or contact_id,
            }
        )
    return tagged_contacts


def _resolve_detected_people(asset_payload: dict[str, Any]) -> list[dict[str, Any]]:
    detected_people: list[dict[str, Any]] = []
    seen_person_ids: set[str] = set()
    for person in immich_client.extract_tagged_people(asset_payload):
        person_id = str(person.get("person_id") or "").strip()
        if not person_id or person_id in seen_person_ids:
            continue
        seen_person_ids.add(person_id)
        matched_contact = contacts_service.get_contact_by_external_id(person_id)
        contact_id = str(matched_contact.get("contact_id") or "").strip() if matched_contact else None
        display_name = (
            str(matched_contact.get("display_name") or "").strip() if matched_contact else ""
        ) or str(person.get("name") or person_id).strip() or person_id
        detected_people.append(
            {
                "person_id": person_id,
                "name": str(person.get("name") or "").strip() or None,
                "contact_id": contact_id or None,
                "display_name": display_name,
                "has_contact_match": bool(contact_id),
            }
        )
    return detected_people


def _merge_event_contacts(event_id: str, tagged_contacts: Sequence[dict[str, str]]) -> None:
    contact_ids = [
        str(contact.get("contact_id") or "").strip()
        for contact in tagged_contacts
        if str(contact.get("contact_id") or "").strip()
    ]
    if not contact_ids:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO event_contacts (event_id, contact_id)
            VALUES (%s, %s)
            ON CONFLICT (event_id, contact_id) DO NOTHING
            """,
            [(event_id, contact_id) for contact_id in contact_ids],
        )
        conn.commit()


def _build_device_asset_id(event_id: str, local_asset_id: str | None) -> str:
    local_part = _normalize_required_string(local_asset_id, "local_asset_id") if local_asset_id else None
    if local_part:
        return f"event-photo:{local_part}"
    return f"event-photo:{event_id}:{uuid4().hex}"


def _event_exists(event_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM events WHERE id = %s LIMIT 1", (event_id,))
        return cur.fetchone() is not None


def _event_photo_exists(event_id: str, asset_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM event_photos WHERE event_id = %s AND immich_asset_id = %s LIMIT 1",
            (event_id, asset_id),
        )
        return cur.fetchone() is not None


def _serialize_photo_row(row: dict[str, Any]) -> dict[str, Any]:
    event_id = str(row.get("event_id") or "").strip()
    asset_id = str(row.get("immich_asset_id") or "").strip()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    detected_people = metadata.get("detected_people") if isinstance(metadata, dict) else None
    return {
        "asset_id": asset_id,
        "checksum": row.get("checksum"),
        "file_name": row.get("original_file_name"),
        "mime_type": row.get("mime_type"),
        "captured_at": _datetime_to_iso(row.get("captured_at")),
        "local_asset_id": row.get("local_asset_id"),
        "source": row.get("source"),
        "width": row.get("width"),
        "height": row.get("height"),
        "created_at": _datetime_to_iso(row.get("created_at")),
        "updated_at": _datetime_to_iso(row.get("updated_at")),
        "thumbnail_path": f"/mobile/events/{event_id}/photos/{asset_id}/thumbnail" if event_id and asset_id else None,
        "tagged_contacts": [],
        "detected_people": detected_people if isinstance(detected_people, list) else [],
    }


def _fetch_immich_asset_context(asset_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    asset_payload = immich_client.fetch_asset(asset_id)
    combined_payload: dict[str, Any] = dict(asset_payload)
    try:
        faces = immich_client.fetch_asset_faces(asset_id)
    except Exception:
        faces = []
    if faces:
        combined_payload["faces"] = faces
    return asset_payload, _resolve_detected_people(combined_payload)


def _refresh_photo_people_metadata(event_id: str, photo: dict[str, Any]) -> dict[str, Any]:
    asset_id = str(photo.get("asset_id") or "").strip()
    if not event_id or not asset_id:
        return photo

    try:
        asset_payload, detected_people = _fetch_immich_asset_context(asset_id)
    except Exception:
        return photo

    tagged_contacts = _resolve_tagged_contacts_from_detected_people(detected_people)
    if tagged_contacts == (photo.get("tagged_contacts") or []) and detected_people == (photo.get("detected_people") or []):
        return photo

    metadata = {
        "immich_asset": asset_payload,
        "detected_people": detected_people,
    }
    updated_photo = _upsert_event_photo(
        event_id,
        asset_id=asset_id,
        checksum=str(photo.get("checksum") or "").strip(),
        filename=str(photo.get("file_name") or asset_id).strip() or asset_id,
        mime_type=str(photo.get("mime_type") or "").strip() or None,
        captured_at=_parse_iso_datetime(photo.get("captured_at")),
        local_asset_id=str(photo.get("local_asset_id") or "").strip() or None,
        source=str(photo.get("source") or "").strip() or None,
        metadata=metadata,
        tagged_contacts=tagged_contacts,
    )
    _merge_event_contacts(event_id, tagged_contacts)
    return updated_photo


def _normalize_required_string(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise EventPhotoError(f"{field_name} is required")
    return normalized


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _datetime_to_iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
