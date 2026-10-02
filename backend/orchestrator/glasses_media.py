"""Durable, resumable original-media uploads and confirmed Immich receipts."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests

import immich_client
import user_locations
from db import get_conn

CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 2 * 1024**3


def root() -> Path:
    path = Path(os.getenv("GLASSES_MEDIA_UPLOAD_DIR", "storage/glasses-media"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_storage(user: str, session_id: str, metadata_in: dict) -> None:
    """Bound staging reservations and expire abandoned sessions after seven idle days."""
    storage = root()
    with (storage / ".quota-lock").open("a+b") as quota_lock:
        fcntl.flock(quota_lock, fcntl.LOCK_EX)
        reserved = 0
        for directory in storage.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                uuid.UUID(directory.name)
            except ValueError:
                continue
            with (directory / "lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # An active commit keeps its reservation, even while busy.
                    metadata = json.loads((directory / "manifest.json").read_text())
                    if metadata["user"] == user and directory.name != session_id:
                        reserved += metadata["size"]
                    continue
                if time.time() - directory.stat().st_mtime > 7 * 86400:
                    shutil.rmtree(directory)
                    continue
                manifest = directory / "manifest.json"
                if manifest.exists() and not (directory / "confirmed").exists():
                    metadata = json.loads(manifest.read_text())
                    if metadata["user"] == user and directory.name != session_id:
                        reserved += metadata["size"]
        if reserved + metadata_in["size"] > 4 * 1024**3:
            raise ValueError("Upload staging quota reached; originals must be retained")
        # Reserve before releasing the quota lock, including before any bytes arrive.
        directory = storage / session_id
        directory.mkdir(exist_ok=True)
        with locked(directory):
            manifest = directory / "manifest.json"
            if not manifest.exists():
                temp = directory / "manifest.tmp"
                temp.write_text(json.dumps({"user": user, **metadata_in}))
                temp.replace(manifest)
            directory.touch()


def receipt(user: str, key: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT asset_id, album_id FROM glasses_media_receipts WHERE user_email=%s AND capture_key=%s",
            (user, key),
        )
        row = cur.fetchone()
    return {"confirmed": True, **row} if row else None


def create_session(user: str, metadata: dict) -> dict:
    size = metadata.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_BYTES:
        raise ValueError("Invalid media size")
    for field in ("capture_key", "sha256"):
        value = metadata.get(field, "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"Invalid {field}")
    name = metadata.get("filename", "")
    if not name or Path(name).name != name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid filename")
    if metadata.get("mime_type") not in {
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/avif",
        "video/mp4",
        "video/quicktime",
    }:
        raise ValueError("Unsupported original media type")
    timestamp = metadata.get("captured_at")
    if timestamp:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Capture timestamp needs a timezone")
    prior = receipt(user, metadata["capture_key"])
    if prior:
        return prior
    # One deterministic session per account and capture. Retry recovers the same offset.
    session_id = str(
        uuid.UUID(hashlib.sha256(f"{user}\n{metadata['capture_key']}".encode()).hexdigest()[:32])
    )
    prepare_storage(user, session_id, metadata)
    directory = root() / session_id
    directory.mkdir(exist_ok=True)
    with locked(directory):
        manifest = directory / "manifest.json"
        if manifest.exists():
            existing = json.loads(manifest.read_text())
            if existing != {"user": user, **metadata}:
                raise ValueError("Capture metadata changed; retain original media")
        else:
            temp = directory / "manifest.tmp"
            temp.write_text(json.dumps({"user": user, **metadata}))
            temp.replace(manifest)
        offset = (directory / "media").stat().st_size if (directory / "media").exists() else 0
    directory.touch()
    return {"session_id": session_id, "offset": offset, "confirmed": False}


@contextmanager
def locked(directory: Path):
    with (directory / "lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def session(user: str, session_id: str) -> tuple[Path, dict]:
    normalized = str(uuid.UUID(session_id))
    directory = root() / normalized
    try:
        metadata = json.loads((directory / "manifest.json").read_text())
    except FileNotFoundError as exc:
        raise ValueError("Unknown upload session") from exc
    if metadata["user"] != user:
        raise ValueError("Unknown upload session")
    return directory, metadata


def store_chunk(user: str, session_id: str, offset: int, data: bytes) -> dict:
    directory, metadata = session(user, session_id)
    if not data or len(data) > CHUNK_BYTES or offset < 0 or offset + len(data) > metadata["size"]:
        raise ValueError("Invalid chunk range")
    with locked(directory):
        path = directory / "media"
        with path.open("r+b" if path.exists() else "w+b") as stream:
            stream.seek(0, 2)
            current = stream.tell()
            if offset < current:
                stream.seek(offset)
                if stream.read(len(data)) != data:
                    raise ValueError("Conflicting retry chunk")
            elif offset != current:
                raise ValueError("Upload offset mismatch")
            else:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0, 2)
            current = stream.tell()
    directory.touch()
    return {"offset": current}


def resolve_album(config) -> str:
    """Reuse a configured existing album; never create a guessed destination."""
    album_id = os.getenv("GLASSES_IMMICH_ALBUM_ID", "").strip()
    name = os.getenv("GLASSES_IMMICH_ALBUM_NAME", "").strip()
    headers = {"x-api-key": config.api_key}
    if album_id:
        result = requests.get(
            f"{config.base_url}/api/albums/{album_id}", headers=headers, timeout=config.http_timeout
        )
        result.raise_for_status()
        if result.json().get("id") != album_id:
            raise ValueError("Configured glasses album is unavailable")
        return album_id
    if not name:
        raise ValueError("Configure GLASSES_IMMICH_ALBUM_ID for the existing glasses album")
    result = requests.get(
        f"{config.base_url}/api/albums", headers=headers, timeout=config.http_timeout
    )
    result.raise_for_status()
    matches = [a for a in result.json() if a.get("albumName") == name]
    if len(matches) != 1:
        raise ValueError("Existing glasses album must resolve uniquely; configure its ID")
    return matches[0]["id"]


def complete(user: str, session_id: str) -> dict:
    directory, metadata = session(user, session_id)
    with locked(directory):
        previous = receipt(user, metadata["capture_key"])
        if previous:
            (directory / "media").unlink(missing_ok=True)
            (directory / "confirmed").touch()
            return previous
        path = directory / "media"
        if not path.exists() or path.stat().st_size != metadata["size"]:
            raise ValueError("Incomplete original media")
        sha256, sha1 = hashlib.sha256(), hashlib.sha1()
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK_BYTES):
                sha256.update(chunk)
                sha1.update(chunk)
        if sha256.hexdigest() != metadata["sha256"]:
            raise ValueError("Original media checksum mismatch")
        cfg = immich_client.get_immich_config()
        album_id = resolve_album(cfg)
        # Cross-process serialization also covers two capture keys with identical bytes.
        lock_key = int.from_bytes(
            hashlib.sha256(f"{user}\n{metadata['sha256']}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            cur.execute(
                "SELECT asset_id FROM glasses_media_receipts WHERE user_email=%s AND sha256=%s LIMIT 1",
                (user, metadata["sha256"]),
            )
            duplicate = cur.fetchone()
            captured = (
                datetime.fromisoformat(metadata["captured_at"].replace("Z", "+00:00"))
                if metadata.get("captured_at")
                else None
            )
            if duplicate:
                asset_id = duplicate["asset_id"]
            else:
                with path.open("rb") as stream:
                    uploaded = immich_client.upload_asset_stream(
                        stream,
                        filename=metadata["filename"],
                        mime_type=metadata["mime_type"],
                        taken_at=captured or datetime.now(timezone.utc),
                        device_asset_id=hashlib.sha256(
                            f"{user}\n{metadata['sha256']}".encode()
                        ).hexdigest(),
                        device_id="digital-brain-glasses",
                        size_bytes=metadata["size"],
                        checksum_header=base64.b64encode(sha1.digest()).decode(),
                        config=cfg,
                    )
                asset_id = uploaded["id"]
            asset = immich_client.fetch_asset(asset_id, config=cfg)
            if not asset or asset.get("id") != asset_id or asset.get("isTrashed") is True:
                raise ValueError("Immich asset is not readable")
            if captured and not duplicate:
                location = user_locations.get_nearest_location(
                    user_email=user, captured_at=captured, tolerance_seconds=600
                )
                if location:
                    immich_client.update_asset_location(
                        asset_id, latitude=location["lat"], longitude=location["lon"], config=cfg
                    )
            response = requests.put(
                f"{cfg.base_url}/api/albums/{album_id}/assets",
                headers={"x-api-key": cfg.api_key},
                json={"ids": [asset_id]},
                timeout=cfg.http_timeout,
            )
            response.raise_for_status()
            # A 2xx bulk response can still report per-asset failures. Verify membership.
            response = requests.get(
                f"{cfg.base_url}/api/albums/{album_id}",
                headers={"x-api-key": cfg.api_key},
                timeout=cfg.http_timeout,
            )
            response.raise_for_status()
            if asset_id not in {a["id"] for a in response.json().get("assets", [])}:
                raise ValueError("Immich album membership is not confirmed")
            cur.execute(
                "INSERT INTO glasses_media_receipts(user_email,capture_key,sha256,asset_id,album_id,captured_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (user_email,capture_key) DO NOTHING",
                (user, metadata["capture_key"], metadata["sha256"], asset_id, album_id, captured),
            )
            conn.commit()
        (directory / "confirmed").touch()
        path.unlink(missing_ok=True)
        return {"confirmed": True, "asset_id": asset_id, "album_id": album_id}
