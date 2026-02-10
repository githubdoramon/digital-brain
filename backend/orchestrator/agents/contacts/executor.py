"""Compatibility wrapper for the shared contact-resolution service."""

from typing import Any

from contact_resolution_service import resolve_contacts_request


def handle_resolve_contacts_request(data: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible adapter for legacy imports."""
    return resolve_contacts_request(data)
