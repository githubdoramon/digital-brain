from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

MAX_CHAT_MEDIA_ATTACHMENTS = int(os.getenv("CHAT_MEDIA_MAX_ATTACHMENTS", "5"))
MAX_CHAT_MEDIA_BYTES = int(os.getenv("CHAT_MEDIA_MAX_BYTES", str(6 * 1024 * 1024)))


class ChatMediaError(RuntimeError):
    """Raised when chat media attachments cannot be staged or loaded."""


def stage_chat_media_attachments(
    raw_attachments: Sequence[dict[str, Any]] | None,
    *,
    user_email: str,
) -> list[dict[str, Any]]:
    attachments = [item for item in (raw_attachments or []) if isinstance(item, dict)]
    if not attachments:
        return []
    if len(attachments) > MAX_CHAT_MEDIA_ATTACHMENTS:
        raise ChatMediaError(
            f"A maximum of {MAX_CHAT_MEDIA_ATTACHMENTS} chat media attachments is supported."
        )

    base_dir = _ensure_chat_media_dir()
    staged: list[dict[str, Any]] = []
    for raw in attachments:
        staged.append(_stage_single_attachment(raw, user_email=user_email, base_dir=base_dir))
    return staged


def summarize_staged_chat_media_attachments(
    attachments: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        summaries.append(
            {
                "attachment_id": _normalize_string(attachment.get("attachment_id")),
                "file_name": _normalize_string(attachment.get("file_name")),
                "mime_type": _normalize_string(attachment.get("mime_type")),
                "source": _normalize_string(attachment.get("source")),
                "captured_at": _normalize_datetime_for_json(attachment.get("captured_at")),
                "local_asset_id": _normalize_string(attachment.get("local_asset_id")),
                "width": _coerce_int(attachment.get("width")),
                "height": _coerce_int(attachment.get("height")),
            }
        )
    return summaries


def merge_staged_chat_media_attachments(
    existing: Sequence[dict[str, Any]] | None,
    incoming: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for attachment in list(existing or []) + list(incoming or []):
        if not isinstance(attachment, dict):
            continue
        attachment_id = _normalize_string(attachment.get("attachment_id"))
        if not attachment_id or attachment_id in seen_ids:
            continue
        seen_ids.add(attachment_id)
        merged.append(attachment)
    return merged


def load_staged_chat_media_attachment(
    attachment: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(attachment, dict):
        raise ChatMediaError("Attachment metadata is invalid")
    storage_path = _normalize_string(attachment.get("storage_path"))
    if not storage_path:
        raise ChatMediaError("Attachment storage path is missing")
    path = Path(storage_path)
    if not path.is_file():
        raise ChatMediaError("Attachment file is no longer available")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ChatMediaError(f"Failed to read attachment file: {exc}") from exc
    return content, attachment


def delete_staged_chat_media_attachments(attachments: Sequence[dict[str, Any]] | None) -> None:
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        storage_path = _normalize_string(attachment.get("storage_path"))
        if not storage_path:
            continue
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[chat_media] Failed to delete staged attachment %s: %s", storage_path, exc)


def _stage_single_attachment(
    raw: dict[str, Any],
    *,
    user_email: str,
    base_dir: Path,
) -> dict[str, Any]:
    attachment_id = _normalize_string(raw.get("attachment_id")) or f"chat-media:{uuid4().hex}"
    file_name = _safe_file_name(raw.get("file_name"))
    mime_type = _normalize_string(raw.get("mime_type")) or "application/octet-stream"
    if not mime_type.startswith("image/"):
        raise ChatMediaError("Only image chat attachments are currently supported.")
    content_base64 = _normalize_string(raw.get("content_base64"))
    if not content_base64:
        raise ChatMediaError("Chat attachment content is missing.")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ChatMediaError("Chat attachment content is not valid base64.") from exc
    if not content:
        raise ChatMediaError("Chat attachment content is empty.")
    if len(content) > MAX_CHAT_MEDIA_BYTES:
        max_mb = round(MAX_CHAT_MEDIA_BYTES / (1024 * 1024), 1)
        raise ChatMediaError(f"Each chat attachment must be {max_mb} MB or smaller.")

    file_ext = Path(file_name).suffix or _suffix_from_mime_type(mime_type)
    storage_name = f"{uuid4().hex}{file_ext}"
    storage_path = base_dir / storage_name
    try:
        storage_path.write_bytes(content)
    except OSError as exc:
        raise ChatMediaError(f"Failed to store chat attachment: {exc}") from exc

    return {
        "attachment_id": attachment_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "source": _normalize_string(raw.get("source")) or "mobile_chat",
        "captured_at": _parse_optional_datetime(raw.get("captured_at")),
        "local_asset_id": _normalize_string(raw.get("local_asset_id")) or None,
        "width": _coerce_int(raw.get("width")),
        "height": _coerce_int(raw.get("height")),
        "storage_path": str(storage_path),
        "user_email": user_email,
        "size_bytes": len(content),
        "created_at": datetime.now(timezone.utc),
    }


def _ensure_chat_media_dir() -> Path:
    base_dir = Path(__file__).resolve().parent / "storage" / "chat_media"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _safe_file_name(value: Any) -> str:
    candidate = Path(str(value or "attachment").strip() or "attachment").name
    candidate = candidate.replace("\x00", "").strip()
    return candidate or "attachment"


def _suffix_from_mime_type(mime_type: str) -> str:
    subtype = mime_type.split("/", 1)[1].strip().lower() if "/" in mime_type else "bin"
    subtype = "jpg" if subtype == "jpeg" else subtype
    subtype = "".join(ch for ch in subtype if ch.isalnum()) or "bin"
    return f".{subtype}"


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _normalize_string(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ChatMediaError("Chat attachment captured_at must be ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_datetime_for_json(value: Any) -> str | None:
    parsed = _parse_optional_datetime(value) if value is not None else None
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_string(value: Any) -> str:
    return str(value or "").strip()


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
