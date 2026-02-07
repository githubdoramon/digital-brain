"""Tests for conservative contact disambiguation policy."""

from agents.contacts import resolver


def test_llm_disambiguation_requires_context_signal(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
            "clarification_prompt": "Which Gio did you mean?",
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio?",
    )

    assert resolved == []
    assert new == []
    assert len(ambiguous) == 1
    assert ambiguous[0]["original_text"] == "Gio"


def test_llm_disambiguation_accepted_when_context_is_specific(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
            "clarification_prompt": "Which Gio did you mean?",
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio and we talked about birds?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:gio-a"
    assert new == []
    assert ambiguous == []


def test_llm_disambiguation_rejected_for_non_high_confidence(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
            "clarification_prompt": "Which Gio did you mean?",
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "medium",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio and we talked about birds?",
    )

    assert resolved == []
    assert len(ambiguous) == 1


def test_llm_disambiguation_balanced_accepts_name_level_match(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Panerai"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Ghelfi"},
    ]

    monkeypatch.setenv("CONTACT_DISAMBIGUATION_STRICTNESS", "balanced")
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
            "clarification_prompt": "Which Gio did you mean?",
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Panerai",
            "confidence": "high",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Gio"],
        user_email="user@example.com",
        full_text="When did I last meet Gio?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:gio-a"
    assert ambiguous == []


def test_llm_disambiguation_lenient_accepts_without_context(monkeypatch):
    candidates = [
        {"contact_id": "contact:alice-a", "display_name": "Alice Johnson"},
        {"contact_id": "contact:alice-b", "display_name": "Alice Kim"},
    ]

    monkeypatch.setenv("CONTACT_DISAMBIGUATION_STRICTNESS", "lenient")
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
            "clarification_prompt": "Which Alice did you mean?",
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:alice-a",
            "display_name": "Alice Johnson",
            "confidence": "high",
        },
    )

    resolved, _new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Alice"],
        user_email="user@example.com",
        full_text="When did I meet Alice?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:alice-a"
    assert ambiguous == []
