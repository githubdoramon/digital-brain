from datetime import datetime, timezone

import events
import immich_client


def test_extract_asset_id_reads_nested_duplicate_payload():
    payload = {
        "status": "duplicate",
        "data": {
            "duplicateAssetId": "asset:123",
        },
    }

    assert immich_client.extract_asset_id(payload) == "asset:123"


def test_extract_tagged_person_ids_walks_people_and_faces():
    payload = {
        "people": [
            {"id": "person:1", "name": "Alex"},
            {"id": "person:2", "name": "Sam"},
        ],
        "exifInfo": {"city": "Aurora"},
        "faces": [
            {"personId": "person:2"},
            {"personId": "person:3"},
        ],
    }

    assert immich_client.extract_tagged_person_ids(payload) == [
        "person:1",
        "person:2",
        "person:3",
    ]


def test_get_events_includes_linked_photos(monkeypatch):
    monkeypatch.setattr(
        events,
        "fetch_events",
        lambda ids: [
            {
                "id": ids[0],
                "start_date": datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                "end_date": None,
                "people": ["contact:alex"],
                "_contact_names": {"contact:alex": "Alex"},
                "tags": ["personal"],
                "types": ["generic"],
                "title": "Lunch",
                "summary": "Had lunch together",
                "external_id": None,
                "place_id": None,
            }
        ],
    )
    monkeypatch.setattr(
        events,
        "enrich_people",
        lambda people, names: [{"contact_id": people[0], "display_name": names[people[0]]}],
    )
    monkeypatch.setattr(
        events.event_photos_service,
        "list_event_photos_for_events",
        lambda ids: {
            ids[0]: [
                {
                    "asset_id": "asset:1",
                    "thumbnail_path": f"/mobile/events/{ids[0]}/photos/asset:1/thumbnail",
                    "tagged_contacts": [{"contact_id": "contact:alex", "display_name": "Alex"}],
                }
            ]
        },
    )

    result = events.get_events(["event:1"])

    assert result[0]["photos"][0]["asset_id"] == "asset:1"
    assert result[0]["photos"][0]["tagged_contacts"][0]["contact_id"] == "contact:alex"
