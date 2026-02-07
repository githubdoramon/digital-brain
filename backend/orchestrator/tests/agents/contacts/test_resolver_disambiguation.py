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


def test_resolve_contacts_keeps_current_text_even_with_history(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None):
        captured["text"] = text
        captured["conversation_messages"] = conversation_messages
        return []

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)

    result = resolver.resolve_contacts_from_text(
        text="Perenai",
        user_email="user@example.com",
        conversation_messages=[
            {"role": "user", "content": "when did I last meet Gio?"},
            {"role": "assistant", "content": "Which Gio did you mean?"},
        ],
    )

    assert captured["text"] == "Perenai"
    assert result["text"] == "Perenai"
    assert result["people_mentioned"] == []


def test_llm_disambiguation_prompt_includes_aliases_and_match_hints(monkeypatch):
    captured = {}

    def fake_call_llm_json(prompt, **_kwargs):
        captured["prompt"] = prompt
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "confidence": "low",
            "reasoning": "insufficient context",
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    output = resolver._llm_disambiguate_contact(
        person_text="Gio",
        candidates=[
            {
                "contact_id": "contact:gio-acme-xyz",
                "display_name": "Giovanni Panerai",
                "aliases": ["Gio", "Panerai"],
                "match_reason": "exact name match: gio",
            }
        ],
        event_context="Perenai",
    )

    assert output["resolved"] is False
    prompt = captured["prompt"]
    assert "Aliases: Gio, Panerai" in prompt
    assert "Match hint: exact name match: gio" in prompt


def test_resolve_contact_uses_any_search_for_role_queries(monkeypatch):
    captured = {}

    monkeypatch.setattr(resolver.contacts_service, "find_self_contact", lambda *_a, **_k: None)
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda query, **kwargs: captured.update({"query": query, "kwargs": kwargs}) or [],
    )

    result = resolver.resolve_contact("the CTO of Acme", "user@example.com")

    assert result["status"] == "new"
    assert captured["query"] == "CTO of Acme"
    assert captured["kwargs"]["search_by"] == "any"


def test_relationship_candidates_include_match_reason():
    relationship_context = {
        "relationships": [
            {
                "type": "doctor",
                "related_contact": {"contact_id": "doc-1", "display_name": "Dr. One"},
            },
            {
                "type": "doctor",
                "related_contact": {"contact_id": "doc-2", "display_name": "Dr. Two"},
            },
        ]
    }

    result = resolver._resolve_via_relationship("doctor", relationship_context)

    assert result["found"] is False
    assert len(result["candidates"]) == 2
    assert all(c.get("match_reason") == "relationship match: doctor" for c in result["candidates"])
