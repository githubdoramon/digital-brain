"""Tests for shared contact resolution service behavior."""

import contact_resolution_service as service


def test_service_builds_need_user_input_for_ambiguity(monkeypatch):
    def fake_resolve(_text, _user_email, conversation_messages=None):
        return {
            "status": "success",
            "text": "When did I meet Gio?",
            "people_mentioned": ["Gio"],
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [
                {
                    "original_text": "Gio",
                    "candidates": [
                        {"contact_id": "c1", "display_name": "Giovanni Panerai"},
                        {"contact_id": "c2", "display_name": "Giovanni Ghelfi"},
                    ],
                }
            ],
        }

    monkeypatch.setattr(service, "resolve_contacts_from_text", fake_resolve)

    result = service.resolve_contacts_request(
        {
            "text": "When did I meet Gio?",
            "user_email": "user@example.com",
            "conversation_messages": [{"role": "user", "content": "When did I meet Gio?"}],
        }
    )

    assert result["status"] == "need_user_input"
    assert result["need_user_input"]["kind"] == "disambiguation"
    assert result["need_user_input"]["submission_mode"] == "ui_submission"
    assert result["need_user_input"]["fields"][0]["kind"] == "select"


def test_service_normalizes_existing_need_user_input(monkeypatch):
    def fake_resolve(_text, _user_email, conversation_messages=None):
        return {
            "status": "need_user_input",
            "text": "When did I meet Gio?",
            "people_mentioned": ["Gio"],
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [],
            "need_user_input": {
                "kind": "disambiguation",
                "prompt": "Which Gio did you mean?",
                "questions": ["Which Gio did you mean?"],
                "fields": [
                    {
                        "id": "who_gio",
                        "kind": "select",
                        "label": "Who did you mean by 'Gio'?",
                        "required": True,
                        "options": [{"id": "c1", "label": "Giovanni Panerai"}],
                    }
                ],
                "submission_mode": "ui_submission",
            },
        }

    monkeypatch.setattr(service, "resolve_contacts_from_text", fake_resolve)

    result = service.resolve_contacts_request(
        {
            "text": "When did I meet Gio?",
            "user_email": "user@example.com",
        }
    )

    assert result["status"] == "need_user_input"
    assert result["need_user_input"]["prompt"] == "Which Gio did you mean?"
    assert result["need_user_input"]["submission_mode"] == "ui_submission"
