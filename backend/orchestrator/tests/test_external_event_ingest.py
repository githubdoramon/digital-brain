from datetime import datetime, timezone

import events
from schemas import EventIn, ExternalEventPayload, MeetingIn, TodoIn


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


def test_ingest_external_event_authoritatively_replaces_people_and_place_but_keeps_summary(
    monkeypatch,
):
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
    assert captured[0].summary == "Old summary"


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


def test_resolve_attendee_contacts_groups_all_attendees_by_domain(monkeypatch):
    contact_map = {
        "alice@acme.com": ("contact:alice", False),
        "bob@acme.com": ("contact:bob", True),
        "carol@other.com": ("contact:carol", False),
        "me@acme.com": ("contact:me", False),
    }

    monkeypatch.setattr(
        "events.contacts_service.ensure_contact_for_email",
        lambda email: contact_map[email.lower()],
    )

    unique_contacts, attendee_contacts_by_domain = events._resolve_attendee_contacts(
        ["alice@acme.com", "bob@acme.com", "carol@other.com"],
        contact_cache={},
        current_user={"email": "me@acme.com"},
    )

    assert unique_contacts == ["contact:alice", "contact:bob", "contact:carol", "contact:me"]
    assert sorted(attendee_contacts_by_domain["acme.com"]) == [
        "contact:alice",
        "contact:bob",
        "contact:me",
    ]
    assert attendee_contacts_by_domain["other.com"] == ["contact:carol"]


def test_create_coworker_relationships_skips_existing_pairs(monkeypatch):
    created_relationship_ids: list[str] = []

    monkeypatch.setattr(
        "events._get_existing_relationship_ids",
        lambda _relationship_ids: {"rel:coworker:acme.com:contact:a:contact:b"},
    )
    monkeypatch.setattr(
        "events.contacts_service.upsert_contact_relationship",
        lambda rel: created_relationship_ids.append(rel.relationship_id),
    )

    events._create_coworker_relationships({"acme.com": ["contact:a", "contact:b", "contact:c"]})

    assert created_relationship_ids == [
        "rel:coworker:acme.com:contact:a:contact:c",
        "rel:coworker:acme.com:contact:b:contact:c",
    ]


def test_extract_next_steps_supports_grouped_assignee_lists():
    content = """# Next Steps
- Alex
  - Add V1 scope topic to today's standup agenda
  - Continue exploring legal workarounds for active gamification features; loop
    in the team for ideas
- Pat
  - Clearly communicate V1 scope and expectations to the team at today's standup
"""

    steps = events._extract_next_steps(content, user_tokens=["Alex"])

    assert steps == [
        "Add V1 scope topic to today's standup agenda",
        "Continue exploring legal workarounds for active gamification features; loop in the team for ideas",
    ]


def test_extract_next_steps_supports_unicode_bullets_and_plain_name_labels():
    content = """# Next Steps
Alex
  ◦ Add V1 scope topic to today's standup agenda
  ◦ Continue exploring legal workarounds for active gamification features; loop
    in the team for ideas
Pat
  ◦ Clearly communicate V1 scope and expectations to the team at today's standup
"""

    steps = events._extract_next_steps(content, user_tokens=["Alex"])

    assert steps == [
        "Add V1 scope topic to today's standup agenda",
        "Continue exploring legal workarounds for active gamification features; loop in the team for ideas",
    ]


def test_extract_next_steps_supports_same_indent_assignee_and_task_bullets():
    content = """### Next Steps
- Alex
- Add V1 scope topic to today's standup agenda
- Continue exploring legal workarounds for active gamification features; loop
  in the team for ideas
- Pat
- Clearly communicate V1 scope and expectations to the team at today's standup
- Follow up on crypto casino license option via his contact
"""

    steps = events._extract_next_steps(content, user_tokens=["Alex"])

    assert steps == [
        "Add V1 scope topic to today's standup agenda",
        "Continue exploring legal workarounds for active gamification features; loop in the team for ideas",
    ]


def test_ingest_meeting_notes_creates_todos_from_grouped_user_section(monkeypatch):
    monkeypatch.setattr(
        "events._load_current_user_from_env",
        lambda: {"name": "\u00c1lex Sanders", "email": "alex@example.com"},
    )
    monkeypatch.setattr("events._resolve_attendee_contacts", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._event_exists", lambda _event_id: False)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._get_existing_todo_signatures", lambda _event_id: set())
    monkeypatch.setattr("events.ingest_event", lambda _event: None)

    created_todos: list[TodoIn] = []

    meeting = MeetingIn(
        id="meeting-123",
        title="Weekly sync",
        date=datetime(2026, 2, 27, 9, 0, tzinfo=timezone.utc),
        content="""# Next Steps
- Alex
  - Share updated scope draft before Friday
  - Follow up with legal on the open licensing question
- Pat
  - Align the rollout note with the team
""",
        attendeesEmails=[],
    )

    events.ingest_meeting_notes([meeting], todo_writer=lambda todo: created_todos.append(todo))

    assert [todo.description for todo in created_todos] == [
        "Share updated scope draft before Friday",
        "Follow up with legal on the open licensing question",
    ]


def test_ingest_meeting_notes_ignores_assignee_label_with_unicode_bullets(monkeypatch):
    monkeypatch.setattr(
        "events._load_current_user_from_env",
        lambda: {"name": "Alex Sanders", "email": "alex@example.com"},
    )
    monkeypatch.setattr("events._resolve_attendee_contacts", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._event_exists", lambda _event_id: False)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._get_existing_todo_signatures", lambda _event_id: set())
    monkeypatch.setattr("events.ingest_event", lambda _event: None)

    created_todos: list[TodoIn] = []

    meeting = MeetingIn(
        id="meeting-456",
        title="Weekly sync",
        date=datetime(2026, 2, 27, 9, 0, tzinfo=timezone.utc),
        content="""# Next Steps
Alex
  ◦ Share updated scope draft before Friday
  ◦ Follow up with legal on the open licensing question
Pat
  ◦ Align the rollout note with the team
""",
        attendeesEmails=[],
    )

    events.ingest_meeting_notes([meeting], todo_writer=lambda todo: created_todos.append(todo))

    assert [todo.description for todo in created_todos] == [
        "Share updated scope draft before Friday",
        "Follow up with legal on the open licensing question",
    ]


def test_ingest_meeting_notes_creates_todos_from_same_indent_grouped_bullets(monkeypatch):
    monkeypatch.setattr(
        "events._load_current_user_from_env",
        lambda: {"name": "Alex Carter", "email": "alex.carter@example.com"},
    )
    monkeypatch.setattr("events._resolve_attendee_contacts", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._event_exists", lambda _event_id: False)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._get_existing_todo_signatures", lambda _event_id: set())
    monkeypatch.setattr("events.ingest_event", lambda _event: None)

    created_todos: list[TodoIn] = []

    meeting = MeetingIn(
        id="meeting-789",
        title="Pat / Alex 1:1",
        date=datetime(2026, 4, 1, 10, 59, tzinfo=timezone.utc),
        content="""### Next Steps
- Alex
- Add V1 scope topic to today's standup agenda
- Continue exploring legal workarounds for active gamification features; loop
  in the team for ideas
- Pat
- Clearly communicate V1 scope and expectations to the team at today's standup
- Follow up on crypto casino license option via his contact
""",
        attendeesEmails=[],
    )

    events.ingest_meeting_notes([meeting], todo_writer=lambda todo: created_todos.append(todo))

    assert [todo.description for todo in created_todos] == [
        "Add V1 scope topic to today's standup agenda",
        "Continue exploring legal workarounds for active gamification features; loop in the team for ideas",
    ]
