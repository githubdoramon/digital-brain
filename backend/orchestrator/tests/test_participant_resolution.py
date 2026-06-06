from __future__ import annotations

import contacts


def test_resolve_meeting_participants_groups_known_contact_emails(monkeypatch):
    contact = {
        "contact_id": "contact:alex-rivera",
        "display_name": "Alex Rivera",
        "aliases": ["AR"],
        "emails": ["alex@work.example", "alex@personal.example"],
    }

    def fake_get_contact_by_email(email: str):
        if email in {"alex@work.example", "alex@personal.example"}:
            return contact
        return None

    monkeypatch.setattr(contacts, "get_contact_by_email", fake_get_contact_by_email)

    result = contacts.resolve_meeting_participants(
        [
            {"name": None, "email": "alex@work.example"},
            {"name": "Alex R", "email": "alex@personal.example"},
            {"name": "Jordan Lee", "email": "jordan@example.test"},
        ],
        authenticated_user_email="owner@example.test",
    )

    assert result == [
        {
            "contact_id": "contact:alex-rivera",
            "name": "Alex Rivera",
            "aliases": ["AR"],
            "emails": ["alex@work.example", "alex@personal.example"],
            "is_current_user": False,
        },
        {
            "contact_id": None,
            "name": "Jordan Lee",
            "aliases": [],
            "emails": ["jordan@example.test"],
            "is_current_user": False,
        },
    ]


def test_resolve_meeting_participants_marks_authenticated_user_by_contact_email(monkeypatch):
    self_contact = {
        "contact_id": "contact:self",
        "display_name": "Taylor Quinn",
        "aliases": ["TQ"],
        "emails": ["taylor@work.example", "taylor@personal.example"],
    }

    def fake_get_contact_by_email(email: str):
        if email in {"taylor@work.example", "taylor@personal.example"}:
            return self_contact
        return None

    monkeypatch.setattr(contacts, "get_contact_by_email", fake_get_contact_by_email)

    result = contacts.resolve_meeting_participants(
        [{"name": None, "email": "taylor@work.example"}],
        authenticated_user_email="taylor@personal.example",
    )

    assert result[0]["is_current_user"] is True
    assert result[0]["name"] == "Taylor Quinn"
    assert result[0]["emails"] == ["taylor@work.example", "taylor@personal.example"]


def test_resolve_meeting_participants_uses_unknown_candidate_name(monkeypatch):
    monkeypatch.setattr(contacts, "get_contact_by_email", lambda _email: None)

    result = contacts.resolve_meeting_participants(
        [
            {"name": "Morgan Chen", "email": "Morgan.Chen@Example.Test"},
            {"name": "Ignored Duplicate", "email": "morgan.chen@example.test"},
        ],
        current_user_email="morgan.chen@example.test",
    )

    assert result == [
        {
            "contact_id": None,
            "name": "Morgan Chen",
            "aliases": [],
            "emails": ["morgan.chen@example.test"],
            "is_current_user": True,
        }
    ]
