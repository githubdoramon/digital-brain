"""
Temporary storage for command data between extraction and confirmation.

Uses in-memory storage with expiration for pending command confirmations.
"""

import time
from threading import Lock
from typing import Any, Optional

# In-memory storage for command previews
# In production, this should use Redis or a database
_command_storage: dict[str, tuple[dict[str, Any], float]] = {}
_storage_lock = Lock()

_pending_event_storage: dict[str, tuple[str, float]] = {}

# Preview data expires after 30 minutes
PREVIEW_EXPIRATION_SECONDS = 30 * 60


def store_command_data(preview_id: str, data: dict[str, Any]) -> None:
    """
    Store command data temporarily for confirmation.

    Args:
        preview_id: Unique preview ID
        data: Command data to store
    """
    with _storage_lock:
        # Clean expired entries while we're at it
        _clean_expired()
        _command_storage[preview_id] = (data, time.time())


def get_command_data(preview_id: str) -> Optional[dict[str, Any]]:
    """
    Retrieve stored command data.

    Args:
        preview_id: Unique preview ID

    Returns:
        Command data dict if found and not expired, None otherwise
    """
    with _storage_lock:
        if preview_id not in _command_storage:
            return None

        data, stored_at = _command_storage[preview_id]

        # Check expiration
        if time.time() - stored_at > PREVIEW_EXPIRATION_SECONDS:
            del _command_storage[preview_id]
            return None

        return data


def delete_command_data(preview_id: str) -> None:
    """
    Delete command data after confirmation/cancellation.

    Args:
        preview_id: Unique preview ID
    """
    with _storage_lock:
        _command_storage.pop(preview_id, None)


def store_pending_event(key: str, preview_id: str) -> None:
    with _storage_lock:
        _clean_pending_expired()
        _pending_event_storage[key] = (preview_id, time.time())


def get_pending_event(key: str) -> Optional[str]:
    with _storage_lock:
        entry = _pending_event_storage.get(key)
        if not entry:
            return None

        preview_id, stored_at = entry
        if time.time() - stored_at > PREVIEW_EXPIRATION_SECONDS:
            _pending_event_storage.pop(key, None)
            return None

        return preview_id


def clear_pending_event(key: str) -> None:
    with _storage_lock:
        _pending_event_storage.pop(key, None)


def clear_pending_event_by_preview_id(preview_id: str) -> None:
    with _storage_lock:
        keys_to_remove = [
            key
            for key, (stored_preview_id, _) in _pending_event_storage.items()
            if stored_preview_id == preview_id
        ]
        for key in keys_to_remove:
            _pending_event_storage.pop(key, None)


def _clean_expired() -> None:
    """Clean up expired entries (call with lock held)."""
    current_time = time.time()
    expired = [
        preview_id
        for preview_id, (_, stored_at) in _command_storage.items()
        if current_time - stored_at > PREVIEW_EXPIRATION_SECONDS
    ]
    for preview_id in expired:
        del _command_storage[preview_id]


def _clean_pending_expired() -> None:
    current_time = time.time()
    expired = [
        key
        for key, (_, stored_at) in _pending_event_storage.items()
        if current_time - stored_at > PREVIEW_EXPIRATION_SECONDS
    ]
    for key in expired:
        del _pending_event_storage[key]
