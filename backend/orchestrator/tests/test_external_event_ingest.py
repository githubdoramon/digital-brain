import json
from datetime import datetime, timezone

import contacts
import events
from schemas import EventIn, ExternalEventPayload, MeetingIn, MeetingTranscriptPayload, TodoIn


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


def test_ingest_external_event_collects_attendees_from_raw_payload(monkeypatch):
    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: None)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._load_current_user_from_env", lambda: None)
    monkeypatch.setattr(
        "events._resolve_attendee_contacts",
        lambda attendee_emails, **_kwargs: (list(attendee_emails), {}),
    )
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)

    captured: list[EventIn] = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured.append(event))

    payload = ExternalEventPayload(
        event=EventIn(
            id="ghi789",
            startDate=datetime(2026, 2, 26, 11, 0, tzinfo=timezone.utc),
            title="Partner sync",
            raw={
                "attendees": [
                    {"email": "alex@example.com", "displayName": "Alex Carter"},
                    {"address": "dana@example.com"},
                    "alex@example.com",
                ]
            },
        ),
        externalType="google",
    )

    events.ingest_external_event(payload)

    assert len(captured) == 1
    assert captured[0].people == ["alex@example.com", "dana@example.com"]


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
        "alice@acme.example": ("contact:alice", False),
        "bob@acme.example": ("contact:bob", True),
        "carol@other.example": ("contact:carol", False),
        "me@acme.example": ("contact:me", False),
    }

    monkeypatch.setattr(
        "events.contacts_service.ensure_contact_for_email",
        lambda email: contact_map[email.lower()],
    )

    unique_contacts, attendee_contacts_by_domain = events._resolve_attendee_contacts(
        ["alice@acme.example", "bob@acme.example", "carol@other.example"],
        contact_cache={},
        current_user={"email": "me@acme.example"},
    )

    assert unique_contacts == ["contact:alice", "contact:bob", "contact:carol", "contact:me"]
    assert sorted(attendee_contacts_by_domain["acme.example"]) == [
        "contact:alice",
        "contact:bob",
        "contact:me",
    ]
    assert attendee_contacts_by_domain["other.example"] == ["contact:carol"]


def test_ensure_contact_for_email_merges_new_email_into_exact_name_match(monkeypatch):
    matched_contact = {
        "contact_id": "contact:alex-example",
        "display_name": "Alex Example",
        "aliases": ["Alex"],
        "birthday": None,
        "emails": ["alex@example.com"],
        "phones": [],
        "links": [],
        "tags": ["work"],
        "comments": "Known contact",
        "external_id": None,
    }

    monkeypatch.setattr("contacts.get_contact_by_email", lambda _email: None)
    monkeypatch.setattr("contacts._find_unique_contact_by_name", lambda _name: matched_contact)

    captured = []
    monkeypatch.setattr("contacts.ingest_contact", lambda contact: captured.append(contact))

    contact_id, created = contacts.ensure_contact_for_email(
        "alex.alt@example.com",
        display_name="Alex Example",
    )

    assert contact_id == "contact:alex-example"
    assert created is False
    assert captured[0].emails == ["alex@example.com", "alex.alt@example.com"]
    assert captured[0].tags == ["work", "meeting-attendee"]


def test_event_in_normalizes_attendees_alias_and_objects():
    event = EventIn(
        id="google:alias-test",
        startDate=datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc),
        title="Alias test",
        attendees=[
            {"email": "alex@example.com"},
            {"address": "dana@example.com"},
            "alex@example.com",
        ],
    )

    assert event.attendees_emails == ["alex@example.com", "dana@example.com"]


def test_ingest_meeting_transcript_creates_summary_and_named_attendees(monkeypatch):
    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: None)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "events._generate_meeting_transcript_summary",
        lambda _payload, _transcript_text, **_kwargs: {
            "summary": "Generated discussion summary",
            "action_items": [
                {
                    "task": "Send the rollout draft",
                    "assignee_name": "Current User",
                    "assignee_email": "me@example.com",
                    "due_date": None,
                    "evidence": "The meeting agreed to send the rollout draft.",
                }
            ],
        },
    )

    ensure_calls = []

    def fake_ensure_contact_for_email(email, *, display_name=None):
        ensure_calls.append((email, display_name))
        return f"contact:{email.split('@', 1)[0]}", False

    monkeypatch.setattr("events.contacts_service.ensure_contact_for_email", fake_ensure_contact_for_email)

    captured = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured.append(event))
    monkeypatch.setattr("events._get_existing_todo_signatures", lambda _event_id: set())
    created_todos: list[TodoIn] = []

    payload = MeetingTranscriptPayload(
        upload_id="upload-123",
        session_id="session-123",
        transcript_hash="hash-123",
        meeting={
            "original_id": "calendar-123",
            "provider": "google",
            "title": "Partner sync",
            "description": "Calendar description",
            "started_at": "2026-02-26T11:00:00+00:00",
            "ended_at": "2026-02-26T11:30:00+00:00",
        },
        participants=[
            {
                "name": "Alex Example",
                "email": "alex.work@example.com",
                "source": "calendar",
            },
            {
                "name": "Alex Example",
                "email": "alex.personal@example.com",
                "source": "calendar",
            },
        ],
        speaker_identities=[
            {
                "id": "speaker_1",
                "label": "Alex Example",
                "identity": {
                    "kind": "participant",
                    "email": "alex.work@example.com",
                    "name": "Alex Example",
                },
            }
        ],
        transcript={
            "segments": [
                {
                    "speaker_id": "speaker_1",
                    "started_at": "2026-02-26T11:00:01+00:00",
                    "ended_at": "2026-02-26T11:00:02+00:00",
                    "text": "We agreed to send the rollout draft.",
                }
            ]
        },
    )

    result = events.ingest_meeting_transcript(
        payload,
        current_user={"name": "Current User", "email": "me@example.com"},
        todo_writer=lambda todo: created_todos.append(todo),
    )

    assert result["summary"] == "Generated discussion summary"
    assert result["action_items"] == [
        {
            "task": "Send the rollout draft",
            "assignee_name": "Current User",
            "assignee_email": "me@example.com",
            "due_date": None,
            "evidence": "The meeting agreed to send the rollout draft.",
        }
    ]
    assert captured[0].summary == "Generated discussion summary"
    assert captured[0].title == "Partner sync"
    assert captured[0].external_id == "google:calendar-123"
    assert captured[0].raw["source"] == "meeting_transcript_ingest"
    json.dumps(captured[0].raw)
    assert captured[0].raw["transcript_text"] == "Alex Example: We agreed to send the rollout draft."
    assert captured[0].raw["action_items"] == result["action_items"]
    assert len(result["created_todo_ids"]) == 1
    assert created_todos[0].todo_id == result["created_todo_ids"][0]
    assert created_todos[0].description == "Send the rollout draft"
    assert created_todos[0].contact_ids == ["contact:me"]
    assert created_todos[0].event_ids == [captured[0].id]
    assert ("alex.work@example.com", "Alex Example") in ensure_calls
    assert ("alex.personal@example.com", "Alex Example") in ensure_calls
    assert ("me@example.com", "Current User") in ensure_calls


def test_ingest_meeting_transcript_replaces_existing_calendar_summary(monkeypatch):
    existing_event_id = "google:calendar-456:event"
    start = datetime(2026, 2, 26, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: existing_event_id)
    monkeypatch.setattr(
        "events._get_event_by_id",
        lambda _event_id: {
            "id": existing_event_id,
            "start_date": start,
            "end_date": None,
            "place_id": None,
            "people": ["contact:old"],
            "tags": ["calendar"],
            "types": ["meeting"],
            "title": "Old title",
            "summary": "Old calendar description",
            "raw": {"source": "calendar"},
            "external_id": "google:calendar-456",
        },
    )
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "events._generate_meeting_transcript_summary",
        lambda _payload, _transcript_text, **_kwargs: {
            "summary": "Transcript-grounded summary",
            "action_items": [],
        },
    )
    monkeypatch.setattr(
        "events.contacts_service.ensure_contact_for_email",
        lambda email, **_kwargs: (f"contact:{email.split('@', 1)[0]}", False),
    )

    captured = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured.append(event))

    payload = MeetingTranscriptPayload(
        meeting={
            "original_id": "calendar-456",
            "provider": "google",
            "title": "Updated title",
            "started_at": start.isoformat(),
        },
        participants=[{"name": "Dana Example", "email": "dana@example.com"}],
        speaker_identities=[],
        transcript={"segments": [{"speaker_id": "speaker_1", "text": "The transcript has better notes."}]},
    )

    events.ingest_meeting_transcript(
        payload,
        current_user={"name": "Current User", "email": "me@example.com"},
    )

    assert captured[0].id == existing_event_id
    assert captured[0].title == "Updated title"
    assert captured[0].summary == "Transcript-grounded summary"
    assert captured[0].people == ["contact:dana", "contact:me"]


def test_ingest_meeting_transcript_only_creates_current_user_todos(monkeypatch):
    monkeypatch.setattr("events._get_event_id_by_external_id", lambda _external_id: None)
    monkeypatch.setattr("events._find_matching_meeting_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_event_by_id", lambda _event_id: None)
    monkeypatch.setattr("events._create_coworker_relationships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("events._get_existing_todo_signatures", lambda _event_id: {"already exists"})
    monkeypatch.setattr(
        "events._generate_meeting_transcript_summary",
        lambda _payload, _transcript_text, **_kwargs: {
            "summary": "Generated discussion summary",
            "action_items": [
                {
                    "task": "Prepare the rollout draft",
                    "assignee_name": "Current User",
                    "assignee_email": "me@example.com",
                    "due_date": "2026-03-01",
                    "evidence": "Current User took the draft.",
                },
                {
                    "task": "Send the customer note",
                    "assignee_name": "Dana Example",
                    "assignee_email": "dana@example.com",
                    "due_date": None,
                    "evidence": "Dana took the note.",
                },
                {
                    "task": "Already exists",
                    "assignee_name": "Current User",
                    "assignee_email": "me@example.com",
                    "due_date": None,
                    "evidence": "Duplicate.",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "events.contacts_service.ensure_contact_for_email",
        lambda email, **_kwargs: (f"contact:{email.split('@', 1)[0]}", False),
    )

    captured_events = []
    created_todos: list[TodoIn] = []
    monkeypatch.setattr("events.ingest_event", lambda event: captured_events.append(event))

    payload = MeetingTranscriptPayload(
        meeting={
            "title": "Action planning",
            "started_at": "2026-02-26T12:00:00+00:00",
        },
        participants=[{"name": "Dana Example", "email": "dana@example.com"}],
        speaker_identities=[],
        transcript={"segments": [{"speaker_id": "speaker_1", "text": "We split up the action items."}]},
    )

    result = events.ingest_meeting_transcript(
        payload,
        current_user={"name": "Current User", "email": "me@example.com"},
        todo_writer=lambda todo: created_todos.append(todo),
    )

    assert len(created_todos) == 1
    assert created_todos[0].description == "Prepare the rollout draft"
    assert created_todos[0].due_date.isoformat() == "2026-03-01"
    assert created_todos[0].contact_ids == ["contact:me"]
    assert created_todos[0].event_ids == [captured_events[0].id]
    assert result["created_todo_ids"] == [created_todos[0].todo_id]


def test_generate_meeting_transcript_summary_parses_action_items(monkeypatch):
    def fake_call_llm_json(prompt, **kwargs):
        assert '"action_items"' in prompt
        assert "assignee_name" in prompt
        assert "current user" in prompt.lower()
        assert kwargs["response_format"] == {"type": "json_object"}
        return {
            "summary": "The team agreed to prepare the rollout draft.",
            "action_items": [
                {
                    "task": "Prepare the rollout draft",
                    "assignee_name": "Current User",
                    "assignee_email": "ME@EXAMPLE.COM",
                    "due_date": None,
                    "evidence": "Current User committed to preparing the rollout draft.",
                },
                {"task": "", "assignee_name": "Dana Example"},
            ],
        }

    monkeypatch.setattr("llm_helpers.call_llm_json", fake_call_llm_json)

    payload = MeetingTranscriptPayload(
        meeting={
            "title": "Rollout planning",
            "started_at": "2026-02-26T12:00:00+00:00",
        },
        participants=[{"name": "Dana Example", "email": "dana@example.com"}],
        speaker_identities=[],
        transcript={"segments": [{"speaker_id": "speaker_1", "text": "I will prepare the draft."}]},
    )

    result = events._generate_meeting_transcript_summary(
        payload,
        "Current User: I will prepare the draft.",
        current_user={"name": "Current User", "email": "me@example.com"},
    )

    assert result == {
        "summary": "The team agreed to prepare the rollout draft.",
        "action_items": [
            {
                "task": "Prepare the rollout draft",
                "assignee_name": "Current User",
                "assignee_email": "me@example.com",
                "due_date": None,
                "evidence": "Current User committed to preparing the rollout draft.",
            }
        ],
    }


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
