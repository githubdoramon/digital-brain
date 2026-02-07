"""Tests for contact-resolution executor wiring."""

from agents.contacts import executor


def test_executor_passes_conversation_messages(monkeypatch):
    captured = {}

    def fake_resolve(text, user_email, conversation_messages=None):
        captured["text"] = text
        captured["user_email"] = user_email
        captured["conversation_messages"] = conversation_messages
        return {
            "text": text,
            "people_mentioned": [],
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [],
        }

    monkeypatch.setattr(executor, "resolve_contacts_from_text", fake_resolve)

    result = executor.handle_resolve_contacts_request(
        {
            "text": "When did I meet Gio?",
            "user_email": "user@example.com",
            "conversation_messages": [{"role": "user", "content": "When did I meet Gio?"}],
        }
    )

    assert result["status"] == "no_people"
    assert captured["conversation_messages"] == [
        {"role": "user", "content": "When did I meet Gio?"}
    ]
