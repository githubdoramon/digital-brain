from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, BinaryIO

import requests

from contacts import get_contact_by_external_id
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

IMMICH_HTTP_TIMEOUT = int(os.getenv("IMMICH_HTTP_TIMEOUT", "45"))
UPLOAD_CHUNK_SIZE = 1024 * 1024


class ImmichClientError(RuntimeError):
    """Raised when the Immich client is not configured correctly."""


class ImmichIdentifyError(RuntimeError):
    """Raised when face identification fails."""


@dataclass
class ImmichConfig:
    base_url: str
    api_key: str
    face_api_key: str | None
    device_id: str | None = None
    http_timeout: int = IMMICH_HTTP_TIMEOUT


def _load_base_auth() -> tuple[str, str, str | None]:
    base_url = (os.getenv("IMMICH_SERVER_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("IMMICH_API_KEY") or "").strip()
    face_api_key = (os.getenv("IMMICH_FACE_API_KEY") or "").strip()
    if not base_url and not api_key:
        raise ImmichClientError("IMMICH_SERVER_URL and IMMICH_API_KEY must be configured")
    return base_url, api_key, face_api_key


def get_immich_config(require_device: bool = False) -> ImmichConfig:
    base_url, api_key, face_api_key = _load_base_auth()
    device_id = (os.getenv("IMMICH_DEVICE_ID") or "").strip() or None
    if require_device and not device_id:
        device_id = "telegram-bot"
    return ImmichConfig(
        base_url=base_url, api_key=api_key, face_api_key=face_api_key, device_id=device_id
    )


def identify_contacts_from_image(
    image_bytes: bytes,
    filename: str | None = None,
    mime_type: str | None = None,
    config: ImmichConfig | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """
    Call Immich's face identify endpoint and map detected faces to contacts.

    Returns (contacts, raw_response). Contacts includes only matches that were
    linked to an existing contact via personId.
    """
    if not image_bytes:
        raise ImmichIdentifyError("Image payload is empty")

    cfg = config or get_immich_config()
    if not cfg.face_api_key:
        raise ImmichIdentifyError("IMMICH_FACE_API_KEY must be configured")
    url = f"{cfg.base_url}/api/faces/identify"
    headers = {
        "x-api-key": cfg.face_api_key,
        "accept": "application/json",
    }
    files = {
        "file": (
            filename or "capture.jpg",
            image_bytes,
            mime_type or "application/octet-stream",
        )
    }

    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.post(url, headers=headers, files=files, timeout=timeout)
    except requests.RequestException as exc:
        raise ImmichIdentifyError(f"Failed to reach Immich identify endpoint: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise ImmichIdentifyError(f"Immich identify failed ({response.status_code}): {snippet}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImmichIdentifyError("Immich identify returned invalid JSON") from exc

    matches: list[Any] = payload if isinstance(payload, list) else [payload] if payload else []
    contacts: list[dict[str, Any]] = []
    seen_ids = set()

    for match in matches:
        person_id = match.get("personId") if isinstance(match, dict) else None
        if not person_id or person_id in seen_ids:
            continue
        seen_ids.add(person_id)
        contact = get_contact_by_external_id(str(person_id))
        if contact:
            contacts.append(contact)

    return contacts, payload


def fetch_person_thumbnail(
    person_id: str,
    config: ImmichConfig | None = None,
) -> tuple[bytes, str] | None:
    if not person_id:
        return None

    cfg = config or get_immich_config()
    url = f"{cfg.base_url}/api/people/{person_id}/thumbnail"
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "image/*",
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("[fetch_person_thumbnail] error=%s", exc, exc_info=exc)
        raise ImmichClientError(f"Immich thumbnail request failed: {exc}") from exc

    if response.status_code == 404:
        logger.info("[fetch_person_thumbnail] 404")
        return None
    if response.status_code >= 400:
        logger.warning("[fetch_person_thumbnail] response=%s", response.text)
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich thumbnail failed ({response.status_code}): {snippet}")

    content_type = response.headers.get("content-type") or "image/jpeg"
    return response.content, content_type


class _StreamingMultipartBody:
    def __init__(
        self,
        *,
        prefix: bytes,
        file_obj: BinaryIO,
        file_size: int,
        suffix: bytes,
    ) -> None:
        self._prefix = prefix
        self._file_obj = file_obj
        self._file_size = file_size
        self._suffix = suffix

    def __iter__(self) -> Iterator[bytes]:
        yield self._prefix
        while True:
            chunk = self._file_obj.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
        yield self._suffix

    def __len__(self) -> int:
        return len(self._prefix) + self._file_size + len(self._suffix)


def _safe_multipart_value(value: str) -> str:
    return str(value).replace("\r", "").replace("\n", "").replace('"', "'")


def upload_asset_stream(
    file_obj: BinaryIO,
    *,
    filename: str,
    mime_type: str | None,
    taken_at: datetime | None,
    device_asset_id: str,
    size_bytes: int,
    checksum_header: str,
    device_id: str | None = None,
    config: ImmichConfig | None = None,
) -> dict[str, Any]:
    if size_bytes <= 0:
        raise ImmichClientError("Image payload is empty")
    if not device_asset_id:
        raise ImmichClientError("device_asset_id is required")

    try:
        file_obj.seek(0)
    except (AttributeError, OSError) as exc:
        raise ImmichClientError("Image payload must be seekable for streaming upload") from exc

    cfg = config or get_immich_config()
    resolved_device_id = (device_id or cfg.device_id or "digital-brain").strip()
    timestamp = _format_timestamp(taken_at or datetime.now(timezone.utc))
    url = f"{cfg.base_url}/api/assets"
    boundary = f"----digitalbrain-{hashlib.sha256(device_asset_id.encode()).hexdigest()[:24]}"
    fields = {
        "deviceAssetId": device_asset_id,
        "deviceId": resolved_device_id,
        "fileCreatedAt": timestamp,
        "fileModifiedAt": timestamp,
        "filename": filename,
        "isFavorite": "false",
    }
    prefix_parts: list[bytes] = []
    for name, value in fields.items():
        prefix_parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; '
                f'name="{_safe_multipart_value(name)}"'
                f"\r\n\r\n{_safe_multipart_value(value)}\r\n"
            ).encode()
        )
    prefix_parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="assetData"; '
            f'filename="{_safe_multipart_value(filename)}"\r\n'
            f"Content-Type: "
            f"{_safe_multipart_value(mime_type or 'application/octet-stream')}\r\n\r\n"
        ).encode()
    )
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    body = _StreamingMultipartBody(
        prefix=b"".join(prefix_parts),
        file_obj=file_obj,
        file_size=size_bytes,
        suffix=suffix,
    )
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "application/json",
        "content-type": f"multipart/form-data; boundary={boundary}",
        "content-length": str(len(body)),
        "x-immich-checksum": checksum_header,
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.post(url, headers=headers, data=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ImmichClientError(f"Failed to reach Immich upload endpoint: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich upload failed ({response.status_code}): {snippet}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImmichClientError("Immich upload returned invalid JSON response") from exc

    return payload if isinstance(payload, dict) else {"raw": payload}


def upload_asset(
    image_bytes: bytes,
    *,
    filename: str,
    mime_type: str | None,
    taken_at: datetime | None,
    device_asset_id: str,
    device_id: str | None = None,
    config: ImmichConfig | None = None,
) -> dict[str, Any]:
    """Upload an in-memory asset, retaining the legacy API for small callers."""
    if not image_bytes:
        raise ImmichClientError("Image payload is empty")
    checksum_header = base64.b64encode(hashlib.sha1(image_bytes).digest()).decode("ascii")
    return upload_asset_stream(
        BytesIO(image_bytes),
        filename=filename,
        mime_type=mime_type,
        taken_at=taken_at,
        device_asset_id=device_asset_id,
        size_bytes=len(image_bytes),
        checksum_header=checksum_header,
        device_id=device_id,
        config=config,
    )


def ensure_album(
    album_name: str,
    asset_id: str,
    config: ImmichConfig | None = None,
) -> dict[str, Any]:
    """Find or create an Immich album and ensure the asset is a member."""
    normalized_name = str(album_name or "").strip()
    normalized_asset = str(asset_id or "").strip()
    if not normalized_name or not normalized_asset:
        raise ImmichClientError("album_name and asset_id are required")
    cfg = config or get_immich_config()
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT
    try:
        response = requests.get(f"{cfg.base_url}/api/albums", headers=headers, timeout=timeout)
        response.raise_for_status()
        albums = response.json()
        album = next(
            (
                item
                for item in albums
                if isinstance(item, dict) and item.get("albumName") == normalized_name
            ),
            None,
        )
        if album is None:
            response = requests.post(
                f"{cfg.base_url}/api/albums",
                headers=headers,
                json={"albumName": normalized_name, "assetIds": [normalized_asset]},
                timeout=timeout,
            )
            response.raise_for_status()
            album = response.json()
        elif normalized_asset not in set(album.get("assetIds") or []):
            response = requests.put(
                f"{cfg.base_url}/api/albums/{album['id']}/assets",
                headers=headers,
                json={"assetIds": [normalized_asset]},
                timeout=timeout,
            )
            response.raise_for_status()
            album = {**album, "assetIds": [*(album.get("assetIds") or []), normalized_asset]}
    except requests.RequestException as exc:
        raise ImmichClientError(f"Immich album request failed: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise ImmichClientError("Immich album returned invalid JSON") from exc
    if not isinstance(album, dict) or not album.get("id"):
        raise ImmichClientError("Immich album response did not include an ID")
    return album


def update_asset_location(
    asset_id: str,
    *,
    latitude: float,
    longitude: float,
    config: ImmichConfig | None = None,
) -> dict[str, Any]:
    """Attach phone-derived coordinates to an Immich asset without rewriting bytes."""
    normalized_asset = str(asset_id or "").strip()
    if not normalized_asset:
        raise ImmichClientError("asset_id is required")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ImmichClientError("asset location is outside valid bounds")
    cfg = config or get_immich_config()
    try:
        response = requests.put(
            f"{cfg.base_url}/api/assets/{normalized_asset}",
            headers={
                "x-api-key": cfg.api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"latitude": latitude, "longitude": longitude},
            timeout=cfg.http_timeout or IMMICH_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ImmichClientError(f"Immich asset location update failed: {exc}") from exc
    except ValueError as exc:
        raise ImmichClientError("Immich asset location returned invalid JSON") from exc
    return payload if isinstance(payload, dict) else {"raw": payload}


def fetch_asset(asset_id: str, config: ImmichConfig | None = None) -> dict[str, Any]:
    normalized_asset_id = str(asset_id or "").strip()
    if not normalized_asset_id:
        raise ImmichClientError("asset_id is required")

    cfg = config or get_immich_config()
    url = f"{cfg.base_url}/api/assets/{normalized_asset_id}"
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "application/json",
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ImmichClientError(f"Immich asset request failed: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich asset fetch failed ({response.status_code}): {snippet}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImmichClientError("Immich asset fetch returned invalid JSON response") from exc

    if not isinstance(payload, dict):
        raise ImmichClientError("Immich asset fetch returned unexpected payload")
    return payload


def fetch_asset_faces(asset_id: str, config: ImmichConfig | None = None) -> list[dict[str, Any]]:
    normalized_asset_id = str(asset_id or "").strip()
    if not normalized_asset_id:
        raise ImmichClientError("asset_id is required")

    cfg = config or get_immich_config()
    url = f"{cfg.base_url}/api/faces"
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "application/json",
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.get(url, headers=headers, params={"id": normalized_asset_id}, timeout=timeout)
    except requests.RequestException as exc:
        raise ImmichClientError(f"Immich face request failed: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich face fetch failed ({response.status_code}): {snippet}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImmichClientError("Immich face fetch returned invalid JSON response") from exc

    if not isinstance(payload, list):
        raise ImmichClientError("Immich face fetch returned unexpected payload")
    return [face for face in payload if isinstance(face, dict)]


def fetch_asset_thumbnail(
    asset_id: str,
    *,
    size: str = "preview",
    config: ImmichConfig | None = None,
) -> tuple[bytes, str]:
    normalized_asset_id = str(asset_id or "").strip()
    if not normalized_asset_id:
        raise ImmichClientError("asset_id is required")

    cfg = config or get_immich_config()
    url = f"{cfg.base_url}/api/assets/{normalized_asset_id}/thumbnail"
    headers = {
        "x-api-key": cfg.api_key,
        "accept": "image/*",
    }
    timeout = cfg.http_timeout or IMMICH_HTTP_TIMEOUT

    try:
        response = requests.get(url, headers=headers, params={"size": size}, timeout=timeout)
    except requests.RequestException as exc:
        raise ImmichClientError(f"Immich thumbnail request failed: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich thumbnail fetch failed ({response.status_code}): {snippet}")

    return response.content, response.headers.get("content-type") or "image/jpeg"


def extract_asset_id(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    direct_candidates = [
        payload.get("id"),
        payload.get("assetId"),
        payload.get("asset_id"),
        payload.get("existingAssetId"),
        payload.get("existing_asset_id"),
        payload.get("duplicateAssetId"),
        payload.get("duplicate_asset_id"),
    ]
    for candidate in direct_candidates:
        normalized = _normalize_string(candidate)
        if normalized:
            return normalized

    nested_keys = ("asset", "data", "result")
    for key in nested_keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_asset_id = extract_asset_id(nested)
            if nested_asset_id:
                return nested_asset_id

    return None


def extract_tagged_person_ids(asset_payload: dict[str, Any] | None) -> list[str]:
    person_ids: list[str] = []
    for person in extract_tagged_people(asset_payload):
        person_id = person.get("person_id")
        if isinstance(person_id, str) and person_id:
            person_ids.append(person_id)
    return person_ids


def extract_tagged_people(asset_payload: dict[str, Any] | None) -> list[dict[str, str | None]]:
    seen: set[str] = set()
    ordered: list[dict[str, str | None]] = []

    def _add(candidate: Any, name: Any = None) -> None:
        normalized = _normalize_string(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append({"person_id": normalized, "name": _normalize_string(name)})

    def _walk(value: Any, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            current_key = (parent_key or "").lower()
            if current_key in {"people", "personwithfaces", "personwithface", "person"}:
                _add(
                    value.get("id") or value.get("personId") or value.get("person_id"),
                    value.get("name"),
                )
            if current_key in {"faces", "face"}:
                person = value.get("person") if isinstance(value.get("person"), dict) else None
                _add(
                    value.get("personId")
                    or value.get("person_id")
                    or (person or {}).get("id")
                    or value.get("id"),
                    (person or {}).get("name"),
                )
            for key, nested in value.items():
                key_lower = str(key).lower()
                if key_lower in {"personid", "person_id"}:
                    _add(nested)
                _walk(nested, key_lower)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item, parent_key)

    _walk(asset_payload)
    return ordered


def _normalize_string(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
