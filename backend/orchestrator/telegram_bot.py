from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import immich_client
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

TELEGRAM_HTTP_TIMEOUT = int(os.getenv("TELEGRAM_HTTP_TIMEOUT", "20"))
IMMICH_HTTP_TIMEOUT = immich_client.IMMICH_HTTP_TIMEOUT


class TelegramConfigError(RuntimeError):
    """Raised when the integration is not configured correctly."""


class TelegramAuthError(RuntimeError):
    """Raised when a request fails authentication or authorization."""


class TelegramProcessingError(RuntimeError):
    """Raised when the payload from Telegram cannot be processed."""


class TelegramUploadError(RuntimeError):
    """Raised when files cannot be delivered to Immich."""


@dataclass
class TelegramConfig:
    bot_token: str
    immich_url: str
    immich_api_key: str
    immich_device_id: str
    allowed_chat_ids: set[int]
    secret_token: str | None

    @property
    def telegram_api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    @property
    def telegram_file_url(self) -> str:
        return f"https://api.telegram.org/file/bot{self.bot_token}"

    @property
    def immich_upload_url(self) -> str:
        return f"{self.immich_url}/api/assets"


def process_update(update: dict[str, Any], *, secret_token: str | None) -> dict[str, Any]:
    config = _load_config()
    _verify_secret(secret_token, config)

    message = _extract_message(update)
    logger.debug("[telegram_bot] message=%s", message)
    if not message:
        logger.info("Telegram webhook ignored update without a message: %s", list(update.keys()))
        return {"ok": True, "skipped": "no_message"}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    logger.debug("[telegram_bot] chat_id=%s", chat_id)
    if chat_id is None:
        raise TelegramProcessingError("Missing chat identifier in Telegram payload")

    chat_id_int = int(chat_id)
    if config.allowed_chat_ids and chat_id_int not in config.allowed_chat_ids:
        raise TelegramAuthError("Chat is not allowed to use this webhook")

    candidate = _extract_image_candidate(message)
    logger.debug("[telegram_bot] candidate=%s", candidate)
    if not candidate:
        logger.info("Telegram webhook skipped non-image message chat=%s", chat_id_int)
        return {"ok": True, "skipped": "non_image_message"}

    logger.debug("[telegram_bot] candidate=%s", candidate)
    file_bytes, remote_filename = _download_file(candidate["file_id"], config)
    taken_at = _message_datetime(message)
    filename = _build_filename(candidate, remote_filename, chat_id_int)
    device_asset_id = candidate.get("file_unique_id") or candidate["file_id"]

    upload_result = _upload_to_immich(
        image_bytes=file_bytes,
        filename=filename,
        mime_type=candidate.get("mime_type"),
        taken_at=taken_at,
        device_asset_id=device_asset_id,
        config=config,
    )

    logger.info(
        "Uploaded Telegram image chat=%s asset_id=%s duplicate=%s",
        chat_id_int,
        upload_result.get("id"),
        upload_result.get("duplicate"),
    )

    return {
        "ok": True,
        "immich_asset_id": upload_result.get("id"),
        "duplicate": upload_result.get("duplicate", False),
        "file_name": filename,
    }


def _load_config() -> TelegramConfig:
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not bot_token:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN is not configured")

    immich_cfg = immich_client.get_immich_config(require_device=True)
    immich_url = immich_cfg.base_url
    immich_api_key = immich_cfg.api_key
    device_id = immich_cfg.device_id or "telegram-bot"
    secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip() or None
    allowed_ids = _parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))

    return TelegramConfig(
        bot_token=bot_token,
        immich_url=immich_url,
        immich_api_key=immich_api_key,
        immich_device_id=device_id or "telegram-bot",
        allowed_chat_ids=allowed_ids,
        secret_token=secret,
    )


def _parse_allowed_chat_ids(raw: str | None) -> set[int]:
    ids: set[int] = set()
    if not raw:
        return ids
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ALLOWED_CHAT_IDS entry: %s", chunk)
    return ids


def _verify_secret(provided: str | None, config: TelegramConfig) -> None:
    if not config.secret_token:
        return
    if provided != config.secret_token:
        raise TelegramAuthError("Invalid Telegram secret token")


def _extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        message = update.get(key)
        if message:
            return message
    return None


def _extract_image_candidate(message: dict[str, Any]) -> dict[str, Any] | None:
    photos = message.get("photo") or []
    if photos:
        # Telegram sends an array of sizes; pick the largest by file_size or width*height
        candidate = max(
            photos,
            key=lambda item: item.get("file_size")
            or (item.get("width", 0) * item.get("height", 0)),
        )
        candidate_copy = dict(candidate)
        candidate_copy["mime_type"] = "image/jpeg"
        return candidate_copy

    document = message.get("document")
    if document:
        mime_type = (document.get("mime_type") or "").lower()
        if mime_type.startswith("image/"):
            return dict(document)

    return None


def _download_file(file_id: str, config: TelegramConfig) -> tuple[bytes, str]:
    file_info = _call_telegram("getFile", {"file_id": file_id}, config)
    file_path = file_info.get("file_path")
    if not file_path:
        raise TelegramProcessingError("Telegram did not return file_path for photo")

    file_url = f"{config.telegram_file_url}/{file_path}"
    try:
        response = requests.get(file_url, timeout=TELEGRAM_HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TelegramProcessingError(f"Failed to download Telegram image: {exc}") from exc

    filename = Path(file_path).name
    return response.content, filename


def _call_telegram(method: str, params: dict[str, Any], config: TelegramConfig) -> dict[str, Any]:
    url = f"{config.telegram_api_url}/{method}"
    try:
        response = requests.get(url, params=params, timeout=TELEGRAM_HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TelegramProcessingError(f"Telegram API call '{method}' failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramProcessingError("Telegram API returned invalid JSON") from exc

    if not payload.get("ok"):
        raise TelegramProcessingError(f"Telegram API error for '{method}': {payload}")

    return payload.get("result") or {}


def _message_datetime(message: dict[str, Any]) -> datetime:
    timestamp = message.get("date")
    if isinstance(timestamp, int):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _build_filename(candidate: dict[str, Any], remote_filename: str, chat_id: int) -> str:
    if remote_filename:
        return remote_filename

    ext = ".jpg"
    mime_type = (candidate.get("mime_type") or "").lower()
    if mime_type.startswith("image/"):
        ext = f".{mime_type.split('/', 1)[1]}"

    file_unique_id = candidate.get("file_unique_id") or candidate.get("file_id")
    return f"telegram_{chat_id}_{file_unique_id}{ext}"


def _upload_to_immich(
    image_bytes: bytes,
    filename: str,
    mime_type: str | None,
    taken_at: datetime,
    device_asset_id: str,
    config: TelegramConfig,
) -> dict[str, Any]:
    iso_timestamp = _format_timestamp(taken_at)
    headers = {
        "x-api-key": config.immich_api_key,
        "accept": "application/json",
    }
    data = {
        "deviceAssetId": device_asset_id,
        "deviceId": config.immich_device_id,
        "fileCreatedAt": iso_timestamp,
        "fileModifiedAt": iso_timestamp,
        "isFavorite": "false",
    }
    files = {
        "assetData": (
            filename,
            image_bytes,
            mime_type or "application/octet-stream",
        )
    }

    try:
        response = requests.post(
            config.immich_upload_url,
            headers=headers,
            data=data,
            files=files,
            timeout=IMMICH_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TelegramUploadError(f"Failed to reach Immich: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        raise TelegramUploadError(f"Immich upload failed ({response.status_code}): {snippet}")

    try:
        return response.json()
    except ValueError as exc:
        raise TelegramUploadError("Immich upload returned invalid JSON response") from exc


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
