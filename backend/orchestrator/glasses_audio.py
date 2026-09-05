"""Ephemeral audio objects used by the smart-glasses command endpoint.

Audio is intentionally process-local.  It is never put in the memory graph,
conversation rows, or the document store.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def _ttl_seconds() -> int:
    try:
        return max(1, int(os.getenv("GLASSES_AUDIO_TTL_SECONDS", "300")))
    except ValueError:
        return 300


@dataclass
class _AudioObject:
    data: bytes
    expires_at: datetime
    user_email: str | None = None


_audio_objects: dict[str, _AudioObject] = {}
_lock = threading.Lock()


def _cleanup_locked(now: datetime) -> None:
    for audio_id, item in list(_audio_objects.items()):
        if item.expires_at <= now:
            _audio_objects.pop(audio_id, None)


def put_audio(
    data: bytes,
    *,
    user_email: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, str]:
    """Store WAV bytes and return the opaque download reference."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds or _ttl_seconds())
    audio_id = uuid4().hex
    with _lock:
        _cleanup_locked(now)
        _audio_objects[audio_id] = _AudioObject(
            data=bytes(data), expires_at=expires_at, user_email=user_email
        )
    return {"audio_id": audio_id, "expires_at": expires_at.isoformat()}


def get_audio(audio_id: str, *, user_email: str | None = None) -> bytes | None:
    """Return an unexpired object without consuming it."""
    now = datetime.now(timezone.utc)
    with _lock:
        _cleanup_locked(now)
        item = _audio_objects.get(audio_id)
        if item and (item.user_email is None or item.user_email == user_email):
            return item.data
        return None


def delete_audio(audio_id: str) -> None:
    with _lock:
        _audio_objects.pop(audio_id, None)


def clear_audio() -> None:
    """Test helper; production callers should rely on TTL/consumption."""
    with _lock:
        _audio_objects.clear()
