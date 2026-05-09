from __future__ import annotations

import conversations


def test_normalize_command_resolved_metadata_preserves_existing_shape():
    metadata = {
        "command_result": {"preview_id": "event:preview:123"},
        "command_resolved": {"status": "updated", "label": "Event updated"},
        "event_resolved": "created",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "updated",
        "label": "Event updated",
    }


def test_normalize_command_resolved_metadata_maps_legacy_event_status():
    metadata = {
        "command_result": {"preview_id": "event:preview:123"},
        "event_resolved": "created",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "created",
        "label": "Event created",
    }


def test_normalize_command_resolved_metadata_maps_legacy_contact_status():
    metadata = {
        "command_result": {"preview_id": "contact:preview:123"},
        "contact_resolved": "cancelled",
    }

    normalized = conversations._normalize_command_resolved_metadata(metadata)

    assert normalized["command_resolved"] == {
        "status": "cancelled",
        "label": "Contact update cancelled",
    }
