"""Tests for contact-resolution executor wiring."""

from agents.contacts import executor


def test_executor_passes_conversation_messages(monkeypatch):
    captured = {}

    def fake_resolve_request(payload):
        captured.update(payload)
        return {
            "status": "no_people",
            "text": payload.get("text"),
            "people_mentioned": [],
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [],
        }

    monkeypatch.setattr(executor, "resolve_contacts_request", fake_resolve_request)

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
