"""
Temporary storage for command data between extraction and confirmation.

Uses in-memory storage with expiration for pending command confirmations.
"""

import time
from typing import Any, Optional
from threading import Lock

# In-memory storage for command previews
# In production, this should use Redis or a database
_command_storage: dict[str, tuple[dict[str, Any], float]] = {}
_storage_lock = Lock()

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
