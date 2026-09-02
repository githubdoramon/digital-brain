"""Bounded, resumable staging for large glasses media uploads."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import glasses_captures

ROOT = Path(os.getenv("GLASSES_CAPTURE_UPLOAD_DIR", "/tmp/digital-brain-glasses-upload"))
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class UploadSessionError(RuntimeError):
    pass


def create(user_email: str, payload: dict[str, Any]) -> dict[str, Any]:
    size = int(payload.get("size_bytes") or 0)
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise UploadSessionError("size_bytes is outside the supported upload range")
    capture_id = str(payload.get("capture_id") or "").strip()
    filename = str(payload.get("filename") or "").strip()
    if not capture_id or not filename:
        raise UploadSessionError("capture_id and filename are required")
    session_id = str(uuid4())
    directory = ROOT / session_id
    directory.mkdir(parents=True, exist_ok=False)
    _write_manifest(
        directory,
        {
            "user_email": user_email,
            "capture_id": capture_id,
            "filename": filename,
            "mime_type": str(payload.get("mime_type") or "application/octet-stream"),
            "captured_at": payload.get("captured_at"),
            "location": payload.get("location") if isinstance(payload.get("location"), dict) else {},
            "size_bytes": size,
        },
    )
    return {"session_id": session_id, "chunk_size_bytes": MAX_CHUNK_BYTES}


def store_chunk(user_email: str, session_id: str, start: int, total: int, body: bytes) -> None:
    directory, manifest = _session(user_email, session_id)
    expected = int(manifest["size_bytes"])
    if total != expected or start < 0 or start + len(body) > expected or not body or len(body) > MAX_CHUNK_BYTES:
        raise UploadSessionError("invalid chunk range")
    path = directory / f"{start:016d}.part"
    if path.exists() and path.stat().st_size == len(body):
        return
    path.write_bytes(body)


def complete(user_email: str, session_id: str) -> dict[str, Any]:
    directory, manifest = _session(user_email, session_id)
    expected = int(manifest["size_bytes"])
    parts = sorted(directory.glob("*.part"))
    offset = 0
    for part in parts:
        if int(part.stem) != offset:
            raise UploadSessionError("upload is incomplete")
        offset += part.stat().st_size
    if offset != expected:
        raise UploadSessionError("upload is incomplete")
    assembled = directory / "media"
    with assembled.open("wb") as output:
        for part in parts:
            with part.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, length=1024 * 1024)
    with assembled.open("rb") as media:
        capture = glasses_captures.upload_capture(
            user_email=user_email,
            capture_id=manifest["capture_id"],
            media_bytes=media,
            filename=manifest["filename"],
            mime_type=manifest["mime_type"],
            captured_at=_parse_datetime(manifest.get("captured_at")),
            location=manifest["location"],
        )
    shutil.rmtree(directory, ignore_errors=True)
    return capture


def _session(user_email: str, session_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        directory = ROOT / str(UUID(session_id))
    except ValueError as exc:
        raise UploadSessionError("invalid upload session") from exc
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise UploadSessionError("upload session was not found")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("user_email") != user_email:
        raise UploadSessionError("upload session is not available")
    return directory, manifest


def _write_manifest(directory: Path, value: dict[str, Any]) -> None:
    (directory / "manifest.json").write_text(json.dumps(value, separators=(",", ":")))


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UploadSessionError("captured_at is invalid") from exc
