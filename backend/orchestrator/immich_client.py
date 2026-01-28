from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from contacts import get_contact_by_external_id

IMMICH_HTTP_TIMEOUT = int(os.getenv("IMMICH_HTTP_TIMEOUT", "45"))


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
        print(f"[fetch_person_thumbnail] error={exc}")
        raise ImmichClientError(f"Immich thumbnail request failed: {exc}") from exc

    if response.status_code == 404:
        print("[fetch_person_thumbnail] 404")
        return None
    if response.status_code >= 400:
        print(f"[fetch_person_thumbnail] response={response.text}")
        snippet = response.text[:200]
        raise ImmichClientError(f"Immich thumbnail failed ({response.status_code}): {snippet}")

    content_type = response.headers.get("content-type") or "image/jpeg"
    return response.content, content_type
