from datetime import datetime, timezone

import events
from schemas import EventIn, ExternalEventPayload


def test_ingest_external_event_updates_existing_event_time(monkeypatch):
    existing_event_id = "google:abc123:event"
    existing_start = datetime(2026, 2, 24, 15, 0, tzinfo=timezone.utc)
    existing_end = datetime(2026, 2, 24, 16, 0, tzinfo=timezone.utc)
    incoming_start = datetime(2026, 2, 24, 18, 30, tzinfo=timezone.utc)
    incoming_end = datetime(2026, 2, 24, 19, 30, tzinfo=timezone.utc)

    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: existing_event_id)
    monkeypatch.setattr(
        "events._get_event_by_id",
        lambda _event_id: {
            "id": existing_event_id,
            "start_date": existing_start,
            "end_date": existing_end,
            "place_id": None,
            "people": [],
            "tags": ["calendar"],
            "types": ["meeting"],
            "title": "Team Sync",
            "summary": "Old summary",
            "raw": {"version": "old"},
            "external_id": "google:abc123",
        },
    )
    monkeypatch.setattr("events._load_current_user_from_env", lambda: None)
    monkeypatch.setattr("events._resolve_attendee_contacts", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)

    captured: list[EventIn] = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured.append(event))

    payload = ExternalEventPayload(
        event=EventIn(
            id="abc123",
            startDate=incoming_start,
            endDate=incoming_end,
            title="Team Sync",
            summary="Updated summary",
            attendeesEmails=[],
        ),
        externalType="google",
    )

    event_id = events.ingest_external_event(payload)

    assert event_id == existing_event_id
    assert len(captured) == 1
    assert captured[0].start_date == incoming_start
    assert captured[0].end_date == incoming_end


def test_ingest_external_event_authoritatively_replaces_people_summary_and_place(monkeypatch):
    existing_event_id = "google:def456:event"
    incoming_start = datetime(2026, 2, 25, 15, 30, tzinfo=timezone.utc)

    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: existing_event_id)
    monkeypatch.setattr(
        "events._get_event_by_id",
        lambda _event_id: {
            "id": existing_event_id,
            "start_date": datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
            "end_date": datetime(2026, 2, 25, 16, 0, tzinfo=timezone.utc),
            "place_id": "place:office",
            "people": ["contact:ana", "contact:bruno"],
            "tags": [],
            "types": ["meeting"],
            "title": "1:1",
            "summary": "Old summary",
            "raw": {},
            "external_id": "google:def456",
        },
    )
    monkeypatch.setattr("events._load_current_user_from_env", lambda: None)
    monkeypatch.setattr("events._resolve_attendee_contacts", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)

    captured: list[EventIn] = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured.append(event))

    payload = ExternalEventPayload(
        event=EventIn(
            id="def456",
            startDate=incoming_start,
            endDate=None,
            placeId=None,
            people=[],
            title="1:1",
            summary="New concise summary",
            attendeesEmails=[],
        ),
        externalType="google",
    )

    events.ingest_external_event(payload)

    assert len(captured) == 1
    assert captured[0].start_date == incoming_start
    assert captured[0].end_date is None
    assert captured[0].place_id is None
    assert captured[0].people == []
    assert captured[0].summary == "New concise summary"


def test_merge_event_stays_additive_by_default():
    existing = {
        "id": "event:additive",
        "start_date": datetime(2026, 2, 26, 14, 0, tzinfo=timezone.utc),
        "end_date": datetime(2026, 2, 26, 15, 0, tzinfo=timezone.utc),
        "place_id": "place:office",
        "people": ["contact:ana"],
        "tags": ["work"],
        "types": ["meeting"],
        "title": "Weekly sync",
        "summary": "Old notes",
        "raw": {"old": True},
        "external_id": None,
    }
    incoming = EventIn(
        id="event:additive",
        startDate=datetime(2026, 2, 26, 16, 30, tzinfo=timezone.utc),
        endDate=datetime(2026, 2, 26, 16, 45, tzinfo=timezone.utc),
        placeId=None,
        people=["contact:bruno"],
        title="Weekly sync",
        summary="New notes",
        attendeesEmails=[],
    )

    merged = events._merge_event(existing, incoming)

    assert merged.start_date == existing["start_date"]
    assert merged.end_date == incoming.end_date
    assert merged.place_id == existing["place_id"]
    assert merged.people == ["contact:ana", "contact:bruno"]
    assert merged.summary == "Old notes\n\nNew notes"
