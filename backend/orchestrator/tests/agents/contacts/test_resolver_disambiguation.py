"""Tests for conservative contact disambiguation policy."""

import sys
import types

from agents.contacts import prompt_builders, resolver
from llm_helpers import LLMUnavailableError


def test_llm_disambiguation_requires_context_signal(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Carter"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Lake"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Carter",
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


def test_single_candidate_disambiguation_accepts_high_confidence_llm(monkeypatch):
    candidates = [
        {
            "contact_id": "contact:sophia-vieira-fanti",
            "display_name": "Sophia Vieira Fanti",
            "match_score": 95,
            "match_reason": "name contains: sophia vieira fanti",
        }
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:sophia-vieira-fanti",
            "display_name": "Sophia Vieira Fanti",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Sophia"],
        user_email="user@example.com",
        full_text="Lunch with Sophia.",
        conversation_messages=[
            {
                "role": "assistant",
                "content": "I found multiple matching contacts. Please choose who you meant.",
            },
            {"role": "user", "content": "Sophia Fanti"},
        ],
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:sophia-vieira-fanti"
    assert new == []
    assert ambiguous == []


def test_llm_disambiguation_reraises_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_call_contact_resolution_llm_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LLMUnavailableError("LLM service is unavailable")
        ),
    )

    try:
        resolver._llm_disambiguate_contact(
            person_text="Gio",
            candidates=[
                {"contact_id": "contact:gio-a", "display_name": "Giovanni Carter"},
                {"contact_id": "contact:gio-b", "display_name": "Giovanni Lake"},
            ],
            event_context="When did I meet Gio?",
        )
    except LLMUnavailableError as exc:
        assert str(exc) == "LLM service is unavailable"
    else:
        raise AssertionError("Expected LLMUnavailableError")


def test_llm_disambiguation_prompt_includes_candidate_relationships(monkeypatch):
    captured = {}

    def fake_get_relationship_context(contact_id, **_kwargs):
        if contact_id == "contact:alex-a":
            return {
                "relationships": [
                    {
                        "type": "spouse",
                        "other_type": "spouse",
                        "related_contact": {"display_name": "Robin Lake"},
                    },
                    {
                        "type": "parent",
                        "other_type": "child",
                        "related_contact": {"display_name": "Jamie Vale"},
                    },
                ]
            }
        if contact_id == "contact:alex-b":
            return {
                "relationships": [
                    {
                        "type": "colleague",
                        "other_type": "colleague",
                        "related_contact": {"display_name": "Dana Lewis"},
                    }
                ]
            }
        return {"relationships": []}

    def fake_llm(prompt, **_kwargs):
        captured["prompt"] = prompt
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "new_contact": False,
            "confidence": "low",
        }

    monkeypatch.setattr(resolver, "_get_relationship_context", fake_get_relationship_context)
    monkeypatch.setattr(resolver, "_call_contact_resolution_llm_json", fake_llm)

    result = resolver._llm_disambiguate_contact(
        person_text="Alex",
        candidates=[
            {"contact_id": "contact:alex-a", "display_name": "Alex Carter"},
            {"contact_id": "contact:alex-b", "display_name": "Alex Carter"},
        ],
        event_context="Which Alex joined the school meeting?",
    )

    assert result["resolved"] is False
    prompt = captured["prompt"]
    assert "Relationships: spouse of Robin Lake; parent of Jamie Vale" in prompt
    assert "Relationships: colleague of Dana Lewis" in prompt


def test_llm_disambiguation_accepted_when_context_is_specific(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Carter"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Lake"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Carter",
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


def test_resolve_people_mentions_prefers_relationship_aligned_candidate(monkeypatch):
    candidates = [
        {
            "contact_id": "contact:alex-a",
            "display_name": "Alex Carter",
            "aliases": ["Alex"],
            "match_reason": "exact name match: alex",
        },
        {
            "contact_id": "contact:alex-b",
            "display_name": "Alex Carter",
            "aliases": ["Alex"],
            "match_reason": "exact name match: alex",
        },
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )

    def fake_get_relationship_context(contact_id, **_kwargs):
        if contact_id == "contact:alex-a":
            return {
                "relationships": [
                    {
                        "type": "spouse",
                        "other_type": "spouse",
                        "related_contact": {"display_name": "Robin Lake"},
                    }
                ]
            }
        if contact_id == "contact:alex-b":
            return {
                "relationships": [
                    {
                        "type": "colleague",
                        "other_type": "colleague",
                        "related_contact": {"display_name": "Dana Lewis"},
                    }
                ]
            }
        return {"relationships": []}

    def fake_llm(prompt, **_kwargs):
        assert "Relationships: spouse of Robin Lake" in prompt
        assert "Relationships: colleague of Dana Lewis" in prompt
        return {
            "decision": "resolved",
            "candidate_number": 1,
            "new_contact": False,
            "confidence": "high",
            "reasoning": "Relationship context matches Robin mentioned in the event.",
        }

    monkeypatch.setattr(resolver, "_get_relationship_context", fake_get_relationship_context)
    monkeypatch.setattr(resolver, "_call_contact_resolution_llm_json", fake_llm)

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Alex"],
        user_email="user@example.com",
        full_text="When did I last meet Alex and Robin to talk about school?",
    )

    assert len(resolved) == 1
    assert resolved[0]["contact_id"] == "contact:alex-a"
    assert new == []
    assert ambiguous == []


def test_llm_disambiguation_rejected_for_non_high_confidence(monkeypatch):
    candidates = [
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Carter"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Lake"},
    ]

    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Carter",
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
        {"contact_id": "contact:gio-a", "display_name": "Giovanni Carter"},
        {"contact_id": "contact:gio-b", "display_name": "Giovanni Lake"},
    ]

    monkeypatch.setenv("CONTACT_DISAMBIGUATION_STRICTNESS", "balanced")
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:gio-a",
            "display_name": "Giovanni Carter",
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


def test_resolve_people_marks_new_contact_from_llm_flag(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": [
                {"contact_id": "contact:juliana-a", "display_name": "Juliana A"},
                {"contact_id": "contact:juliana-b", "display_name": "Juliana B"},
            ],
        },
    )

    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": False,
            "new_contact": True,
            "contact_id": None,
            "display_name": None,
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Julia"],
        user_email="user@example.com",
        full_text="Original event description: ...",
        conversation_messages=[
            {"role": "assistant", "content": "I found multiple matching contacts. Please choose."},
            {"role": "user", "content": "It is a new contact, named Julia"},
        ],
    )

    assert resolved == []
    assert ambiguous == []
    assert new == [{"original_text": "Julia", "display_name": "Julia"}]


def test_resolve_people_honors_explicit_new_contact_before_fuzzy_match(monkeypatch):
    def fail_resolve_contact(*_args, **_kwargs):
        raise AssertionError("explicit new contact should bypass fuzzy resolution")

    monkeypatch.setattr(resolver, "resolve_contact", fail_resolve_contact)

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Taylor Reed"],
        user_email="user@example.com",
        full_text="Met Taylor Reed at the neighborhood party. Taylor Reed is a new contact.",
    )

    assert resolved == []
    assert ambiguous == []
    assert new == [{"original_text": "Taylor Reed", "display_name": "Taylor Reed"}]


def test_resolve_contacts_keeps_current_text_even_with_history(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
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


def test_extract_people_ignores_generic_unknown_person_query(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {"people": ["user", "the person"]},
    )

    people = resolver.extract_people_from_text(
        "Who is the person I've met the most in the last 2 weeks?",
    )

    assert people == []


def test_extract_people_restores_named_possessive_family_group(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        if "Extract collective participant selectors" in prompt:
            return {"selectors": []}
        return {"people": ["Morgan Brooks"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "Morgan Brooks's whole family",
        include_collective_selectors=True,
    )

    assert people == ["Morgan Brooks", "Morgan Brooks's whole family"]
    assert selectors == []


def test_extract_people_restores_named_possessive_coworker_group(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        if "Extract collective participant selectors" in prompt:
            return {"selectors": []}
        return {"people": ["Alex"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "Alex's co workers",
        include_collective_selectors=True,
    )

    assert people == ["Alex", "Alex's co workers"]
    assert selectors == []


def test_llm_disambiguation_prompt_includes_aliases_and_match_hints(monkeypatch):
    captured = {}

    def fake_call_llm_json(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
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
                "contact_id": "contact:gio-acme-example",
                "display_name": "Giovanni Carter",
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
    assert "prefer the relationship evidence" in prompt
    assert "candidate number of the correct candidate" in prompt


def test_llm_disambiguation_prompt_includes_chronological_history(monkeypatch):
    captured = {}

    def fake_call_llm_json(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "decision": "cannot_decide",
            "candidate_number": None,
            "new_contact": True,
            "confidence": "low",
            "reasoning": "user says none of these",
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    resolver._llm_disambiguate_contact(
        person_text="Julia",
        candidates=[
            {
                "contact_id": "contact:juliana-charles-c2092f",
                "display_name": "Juliana Charles",
                "aliases": [],
                "match_reason": "name contains: juliana charles",
            }
        ],
        event_context="on February 8th I went with my daughter to her friend's birthday, Julia",
        conversation_messages=[
            {"role": "assistant", "content": "I found multiple matching contacts. Please choose."},
            {"role": "user", "content": "None of these. It is a new contact, named Julia"},
        ],
    )

    prompt = captured["prompt"]
    assert "Disambiguation history (chronological, oldest first):" in prompt
    assert "- assistant: I found multiple matching contacts. Please choose." in prompt
    assert "- user: None of these. It is a new contact, named Julia" in prompt
    assert "Treat the latest user message as the clarification answer" in prompt
    response_format = captured["kwargs"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "contact_disambiguation"
    assert "new_contact" in response_format["json_schema"]["schema"]["properties"]


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


def test_resolve_contacts_short_circuits_simple_relationship_query(monkeypatch):
    llm_calls = []

    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "user-1", "display_name": "Test User"},
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_a, **_k: {
            "relationships": [
                {
                    "contact_id": "contact-daughter",
                    "related_contact": {
                        "contact_id": "contact-daughter",
                        "display_name": "Emma",
                    },
                    "type": "parent",
                    "other_type": "daughter",
                }
            ]
        },
    )
    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *args, **kwargs: llm_calls.append((args, kwargs)) or {"people": []},
    )

    result = resolver.resolve_contacts_from_text(
        "When did I last meet my daughter?",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["status"] == "success"
    assert any(item["display_name"] == "Emma" for item in result["resolved_contacts"])
    assert llm_calls == []


def test_resolve_contacts_does_not_short_circuit_user_only_fast_path(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
        captured["text"] = text
        return ["gio", "pedro"]

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )

    result = resolver.resolve_contacts_from_text(
        "when did i meet gio and pedro?",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert captured["text"] == "when did i meet gio and pedro?"
    assert result["people_mentioned"] == ["gio", "pedro"]


def test_resolve_contacts_does_not_short_circuit_partial_multi_person_fast_path(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
        captured["text"] = text
        return ["John", "pedro"]

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )

    result = resolver.resolve_contacts_from_text(
        "I met John and pedro yesterday",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert captured["text"] == "I met John and pedro yesterday"
    assert result["people_mentioned"] == ["John", "pedro"]


def test_fast_extract_people_captures_direct_object_and_not_literal_i():
    people, selectors, applied = resolver._fast_extract_people_from_text(
        "I met Rita at the physiotherapy session"
    )

    assert applied is True
    assert selectors == []
    assert people == ["user", "Rita"]


def test_fast_extract_people_collapses_relationship_appositive_name():
    people, selectors, applied = resolver._fast_extract_people_from_text(
        "I had lunch with my wife Dana Lewis after the school meeting"
    )

    assert applied is True
    assert selectors == []
    assert people == ["user", "Dana Lewis"]


def test_fast_extract_people_keeps_relationship_and_separate_name():
    people, selectors, applied = resolver._fast_extract_people_from_text(
        "I had lunch with my wife and Dana Lewis after the school meeting"
    )

    assert applied is True
    assert selectors == []
    assert people == ["user", "my wife", "Dana Lewis"]


def test_participant_filter_can_clear_people_list(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_filter_event_participants_via_llm",
        lambda **_kwargs: ([], ["user", "Rita"]),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )

    result = resolver.resolve_contacts_from_text(
        "I met Rita at the physiotherapy session",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
        participant_focus=True,
    )

    assert result["status"] == "no_people"
    assert result["people_mentioned"] == []


def test_resolve_contacts_group_mentions_bypass_fast_path(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return ["My wife", "My daughter", "Dana's whole family"]

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )

    result = resolver.resolve_contacts_from_text(
        "yesterday at 19h I went to the pizza place, at Alder Square, to celebrate my birthday. My wife and daughter, and Dana's whole family went as well. We talked a lot about my work and the possibility of me getting fired soon.",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert captured["text"].startswith("yesterday at 19h I went to the pizza place")
    assert result["people_mentioned"] == ["My wife", "My daughter", "Dana's whole family"]


def test_resolve_contacts_restores_named_family_group_after_llm_extraction(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        if "Extract collective participant selectors" in prompt:
            return {"selectors": []}
        return {"people": ["Morgan Brooks"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda people, *_args, **_kwargs: (
            [
                {
                    "original_text": people[0],
                    "contact_id": "contact:morgan-lyn-brooks",
                    "display_name": "Morgan Lyn Brooks",
                    "matched_via": "multi_token_name_match",
                    "confidence": "high",
                    "resolution_path": None,
                },
                {
                    "original_text": people[1],
                    "contact_id": "contact:alice-brooks",
                    "display_name": "Alice Brooks",
                    "matched_via": "nested_relationship_group",
                    "confidence": "high",
                    "resolution_path": None,
                },
            ],
            [],
            [],
            {},
        ),
    )

    result = resolver.resolve_contacts_from_text(
        "Morgan Brooks's whole family",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["people_mentioned"] == ["Morgan Brooks", "Morgan Brooks's whole family"]


def test_resolve_contacts_restores_named_coworker_group_after_llm_extraction(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        if "Extract collective participant selectors" in prompt:
            return {"selectors": []}
        return {"people": ["Alex"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda people, *_args, **_kwargs: (
            [
                {
                    "original_text": people[0],
                    "contact_id": "contact:alex-carter",
                    "display_name": "Alex Carter",
                    "matched_via": "direct_match",
                    "confidence": "high",
                    "resolution_path": None,
                },
                {
                    "original_text": people[1],
                    "contact_id": "contact:avery",
                    "display_name": "Avery Hill",
                    "matched_via": "nested_relationship_group",
                    "confidence": "high",
                    "resolution_path": None,
                },
            ],
            [],
            [],
            {},
        ),
    )

    result = resolver.resolve_contacts_from_text(
        "Alex's co workers",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["people_mentioned"] == ["Alex", "Alex's co workers"]


def test_resolve_contacts_collective_selector_bypasses_fast_path(monkeypatch):
    captured = {}

    def fake_extract_people(text, conversation_messages=None, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return (["Dana"], [{"kind": "group", "value": "my engineering team", "raw": "my engineering team"}])

    monkeypatch.setattr(resolver, "extract_people_from_text", fake_extract_people)
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_collective_selectors",
        lambda *args, **kwargs: ([], [], []),
    )

    result = resolver.resolve_contacts_from_text(
        "Yesterday I met Dana and my engineering team for coffee.",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert captured["text"] == "Yesterday I met Dana and my engineering team for coffee."
    assert result["people_mentioned"] == ["Dana"]


def test_resolve_contacts_extracts_full_name_list_without_place(monkeypatch):
    captured = {}

    def fake_resolve_people_mentions(people, *_args, **_kwargs):
        captured["people"] = people
        return [], [], [], {}

    monkeypatch.setattr(resolver, "_resolve_people_mentions", fake_resolve_people_mentions)

    result = resolver.resolve_contacts_from_text(
        "Yesterday I had drinks with Dana, Felix Reed, Théo, and Morgan Brooks at The Tide from 8pm to 11pm.",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert captured["people"] == ["user", "Dana", "Felix Reed", "Théo", "Morgan Brooks"]
    assert "The Tide" not in result["people_mentioned"]


def test_minimal_mode_skips_enrichment_steps(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "extract_people_from_text",
        lambda *_args, **_kwargs: ["John Smith"],
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: (
            [
                {
                    "original_text": "John Smith",
                    "contact_id": "contact-1",
                    "display_name": "John Smith",
                    "matched_via": "direct_match",
                    "confidence": "high",
                    "resolution_path": None,
                }
            ],
            [],
            [],
            {},
        ),
    )

    def fail_professions(*_args, **_kwargs):
        raise AssertionError("profession inference should be skipped in minimal mode")

    def fail_relationships(*_args, **_kwargs):
        raise AssertionError("relationship inference should be skipped in minimal mode")

    monkeypatch.setattr(resolver, "_infer_professions_for_new_contacts", fail_professions)
    monkeypatch.setattr(resolver, "_infer_relationship_pairs", fail_relationships)

    result = resolver.resolve_contacts_from_text(
        "John Smith",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["status"] == "success"
    assert result["suggested_relationships"] == []


def test_suggest_missing_relationships_prefetches_relationship_graph(monkeypatch):
    relationship_calls = []

    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "user-1", "display_name": "User"},
    )

    def fake_get_contact_relationships(contact_id, include_contact_details=False):
        relationship_calls.append((contact_id, include_contact_details))
        return {
            "found": True,
            "relationships": [
                {"contact_id": "contact-2"} if contact_id == "contact-1" else {"contact_id": "contact-9"}
            ],
        }

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        fake_get_contact_relationships,
    )
    monkeypatch.setattr(
        resolver,
        "_infer_relationship_types",
        lambda *_args, **_kwargs: {"type": "doctor", "other_type": "patient"},
    )
    monkeypatch.setattr(resolver, "_infer_profession_from_text", lambda *_args, **_kwargs: None)

    suggestions = resolver._suggest_missing_relationships(
        pairs=[
            {"person_text": "Alice", "anchor_text": "Bob", "relationship_hint": "doctor"},
            {"person_text": "Alice", "anchor_text": "Cara", "relationship_hint": "doctor"},
        ],
        full_text="Alice is Bob's doctor and Cara's doctor",
        user_email="user@example.com",
        resolution_cache={
            "Alice": {"status": "resolved", "contact_id": "contact-1", "display_name": "Alice"},
            "Bob": {"status": "resolved", "contact_id": "contact-2", "display_name": "Bob"},
            "Cara": {"status": "resolved", "contact_id": "contact-3", "display_name": "Cara"},
        },
        profession_by_text={},
    )

    assert len(suggestions) == 1
    assert sorted(relationship_calls) == [
        ("contact-1", False),
        ("contact-2", False),
        ("contact-3", False),
    ]


def test_extract_people_splits_selector_prompt(monkeypatch):
    prompts = []

    def fake_call_llm_json(prompt, **_kwargs):
        prompts.append(prompt)
        prompt_lower = prompt.lower()
        if "extract collective participant selectors" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "email_domain",
                        "value": "acme.example",
                        "raw": "@acme.example",
                        "deterministic": True,
                    }
                ]
            }
        return {"people": ["user", "John Smith"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "I met John Smith and everyone with @acme.example",
        include_collective_selectors=True,
    )

    assert people == ["user", "John Smith"]
    assert any(
        isinstance(selector, dict) and selector.get("kind") == "email_domain"
        for selector in selectors
    )
    assert len(prompts) == 2
    assert "extract all person references" in prompts[0].lower()
    assert "collective group selectors" not in prompts[0].lower()
    assert "extract collective participant selectors" in prompts[1].lower()


def test_extract_people_ignores_non_collective_company_mentions(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        prompt_lower = prompt.lower()
        if "extract collective participant selectors" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "company",
                        "value": "Acme",
                        "raw": "from Acme",
                        "deterministic": True,
                    }
                ]
            }
        return {"people": ["user", "Pat"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "this morning, I was fired from Acme by Pat",
        include_collective_selectors=True,
    )

    assert people == ["user", "Pat"]
    assert selectors == []


def test_extract_people_ignores_family_like_group_selectors(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        prompt_lower = prompt.lower()
        if "extract collective participant selectors" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "group",
                        "value": "children",
                        "raw": "the children",
                        "deterministic": False,
                    },
                    {
                        "kind": "group",
                        "value": "kids",
                        "raw": "the kids",
                        "deterministic": False,
                    },
                ]
            }
        return {"people": ["user", "my wife", "my daughter"]}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "afternoon with the children at my house. Me, wife, daughter. The kids played outside.",
        include_collective_selectors=True,
    )

    assert people == ["user", "my wife", "my daughter"]
    assert selectors == []


def test_extract_people_ignores_vague_crowd_group_selectors(monkeypatch):
    def fake_call_llm_json(prompt, **_kwargs):
        prompt_lower = prompt.lower()
        if "extract collective participant selectors" in prompt_lower:
            return {
                "selectors": [
                    {
                        "kind": "group",
                        "value": "people",
                        "raw": "Lots of people",
                        "deterministic": False,
                    }
                ]
            }
        return {
            "people": [
                "user",
                "Morgan Brooks",
                "Morgan Brooks's whole family",
            ]
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people, selectors = resolver.extract_people_from_text(
        "Lots of people were there, Morgan Brooks's whole family included.",
        include_collective_selectors=True,
    )

    assert people == ["user", "Morgan Brooks", "Morgan Brooks's whole family"]
    assert selectors == []


def test_nested_relationship_reuses_prior_resolved_full_name(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, include_contact_details=False: {
            "relationships": [
                {
                    "type": "sibling",
                    "other_type": "family",
                    "related_contact": {
                        "contact_id": "contact-mariela",
                        "display_name": "Iris Lewis",
                    },
                }
            ]
            if contact_id == "contact-dana"
            else []
        },
    )

    nested = resolver._resolve_nested_relationship(
        ["Dana", "family"],
        "user@example.com",
        resolution_cache={
            "Dana Lewis": {
                "status": "resolved",
                "contact_id": "contact-dana",
                "display_name": "Dana Lewis",
                "matched_via": "direct_match",
                "confidence": "high",
            }
        },
    )

    assert nested["found"] is True
    assert nested["contact_id"] == "contact-mariela"
    assert nested["path"] == ["user", "Dana Lewis", "Iris Lewis"]


def test_resolve_contact_short_circuits_unique_multi_token_name_match(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_args, **_kwargs: [
            {
                "contact_id": "contact:alex",
                "display_name": "Alex",
                "match_score": 88,
                "match_reason": "name part match: alex",
                "aliases": [],
            },
            {
                "contact_id": "contact:morgan-lyn-brooks",
                "display_name": "Morgan Lyn Brooks",
                "match_score": 94,
                "match_reason": "all query name parts match: morgan lyn brooks",
                "aliases": [],
            },
            {
                "contact_id": "contact:alice-brooks",
                "display_name": "Alice Brooks",
                "match_score": 88,
                "match_reason": "name part match: brooks",
                "aliases": [],
            },
        ],
    )

    result = resolver.resolve_contact("Morgan Brooks", "user@example.com")

    assert result["status"] == "resolved"
    assert result["matched_via"] == "multi_token_name_match"
    assert result["contact_id"] == "contact:morgan-lyn-brooks"


def test_resolve_people_mentions_updates_cache_after_llm_disambiguation(monkeypatch):
    def fake_resolve_contact(person_text, *_args, resolution_cache=None, **_kwargs):
        if person_text == "Morgan Brooks":
            return {
                "status": "candidates",
                "candidates": [
                    {"contact_id": "contact:alex", "display_name": "Alex"},
                    {
                        "contact_id": "contact:morgan-lyn-brooks",
                        "display_name": "Morgan Lyn Brooks",
                    },
                ],
            }
        if person_text == "Morgan Brooks's whole family":
            assert resolution_cache is not None
            assert resolution_cache["Morgan Brooks"]["status"] == "resolved"
            assert resolution_cache["Morgan Brooks"]["contact_id"] == "contact:morgan-lyn-brooks"
            return {
                "status": "resolved",
                "contact_id": "contact:family-group",
                "display_name": "Morgan Brooks family",
                "matched_via": "nested_relationship_group",
                "confidence": "high",
                "resolution_path": ["user", "Morgan Lyn Brooks", "Morgan Brooks family"],
            }
        raise AssertionError(f"unexpected person_text: {person_text}")

    monkeypatch.setattr(resolver, "resolve_contact", fake_resolve_contact)
    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: {
            "resolved": True,
            "contact_id": "contact:morgan-lyn-brooks",
            "display_name": "Morgan Lyn Brooks",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, cache = resolver._resolve_people_mentions(
        people=["Morgan Brooks", "Morgan Brooks's whole family"],
        user_email="user@example.com",
        full_text="Morgan Brooks's whole family",
    )

    assert [item["contact_id"] for item in resolved] == [
        "contact:morgan-lyn-brooks",
        "contact:family-group",
    ]
    assert cache["Morgan Brooks"]["status"] == "resolved"
    assert new == []
    assert ambiguous == []


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


def test_resolve_contact_nested_collective_uses_related_candidates(monkeypatch):
    monkeypatch.setattr(resolver.contacts_service, "find_self_contact", lambda *_a, **_k: None)

    def fake_search_contacts(query, **kwargs):
        if query == "Dana Lewis":
            return [
                {
                    "contact_id": "contact:dana",
                    "display_name": "Dana Lewis",
                    "match_score": 100,
                }
            ]
        return []

    monkeypatch.setattr(resolver.contacts_service, "search_contacts", fake_search_contacts)

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:robin",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Robin Lake"},
                },
                {
                    "contact_id": "contact:jamie",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Jamie Lake"},
                },
                {
                    "contact_id": "contact:rafael",
                    "type": "spouse",
                    "other_type": "spouse",
                    "related_contact": {"display_name": "Hugo Lake"},
                },
            ]
        }
        if contact_id == "contact:dana"
        else {"relationships": []},
    )

    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {
            "candidate_numbers": [1, 2, 3],
            "collective_reference": True,
            "confidence": "high",
            "reasoning": "whole family refers to multiple close relatives",
        },
    )

    result = resolver.resolve_contact("Dana Lewis's whole family", "user@example.com")

    assert result["status"] == "candidates"
    assert result["auto_resolve_candidates"] is True
    assert "needs_clarification" not in result
    assert len(result["candidates"]) == 3
    assert all(c.get("contact_id", "").startswith("contact:") for c in result["candidates"])


def test_resolve_contact_my_whole_family_anchors_on_user(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "contact:user", "display_name": "Alex"},
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_a, **_k: [],
    )

    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:robin",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Robin Lake"},
                },
                {
                    "contact_id": "contact:jamie",
                    "type": "parent",
                    "other_type": "daughter",
                    "related_contact": {"display_name": "Jamie Lake"},
                },
            ]
        }
        if contact_id == "contact:user"
        else {"relationships": []},
    )

    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {
            "candidate_numbers": [1, 2],
            "collective_reference": True,
            "confidence": "high",
            "reasoning": "whole family implies multiple relatives",
        },
    )

    result = resolver.resolve_contact("my whole family", "user@example.com")

    assert result["status"] == "candidates"
    assert result["auto_resolve_candidates"] is True
    assert "needs_clarification" not in result
    assert {item["contact_id"] for item in result["candidates"]} == {
        "contact:robin",
        "contact:jamie",
    }


def test_resolve_contact_named_coworker_group_uses_related_candidates(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda query, **_kwargs: [
            {
                "contact_id": "contact:alex-carter",
                "display_name": "Alex Carter",
                "match_score": 100,
                "aliases": [],
            }
        ]
        if query == "Alex"
        else [],
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda contact_id, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:avery",
                    "type": "co-worker",
                    "other_type": "co-worker",
                    "related_contact": {"display_name": "Avery Hill"},
                },
                {
                    "contact_id": "contact:philipp",
                    "type": "co-worker",
                    "other_type": "co-worker",
                    "related_contact": {"display_name": "Philipp"},
                },
                {
                    "contact_id": "contact:gio",
                    "type": "friend",
                    "other_type": "friend",
                    "related_contact": {"display_name": "Giovanni Carter"},
                },
            ]
        }
        if contact_id == "contact:alex-carter"
        else {"relationships": []},
    )
    monkeypatch.setattr(
        resolver,
        "call_llm_json",
        lambda *_args, **_kwargs: {
            "candidate_numbers": [1, 2],
            "collective_reference": True,
            "confidence": "high",
            "reasoning": "co workers refers to Alex coworkers",
        },
    )

    result = resolver.resolve_contact("Alex's co workers", "user@example.com")

    assert result["status"] == "candidates"
    assert result["auto_resolve_candidates"] is True
    assert {item["contact_id"] for item in result["candidates"]} == {
        "contact:avery",
        "contact:philipp",
    }


def test_resolve_people_mentions_auto_resolves_collective_candidates(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda *_args, **_kwargs: {
            "status": "candidates",
            "candidates": [
                {"contact_id": "contact:robin", "display_name": "Robin Lake"},
                {"contact_id": "contact:jamie", "display_name": "Jamie Lake"},
            ],
            "confidence": "high",
            "auto_resolve_candidates": True,
            "skip_auto_disambiguation": True,
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        people=["Dana Lewis's whole family"],
        user_email="user@example.com",
        full_text="having lunch with Dana Lewis and his whole family",
    )

    assert len(resolved) == 2
    assert {item["contact_id"] for item in resolved} == {"contact:robin", "contact:jamie"}
    assert all(item["matched_via"] == "nested_relationship_group" for item in resolved)
    assert new == []
    assert ambiguous == []


def test_resolve_contact_applies_hard_rule_before_disambiguation(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(
        resolver,
        "_llm_disambiguate_contact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    facts_module = types.SimpleNamespace(
        get_hard_rules_for_scope=lambda *_args, **_kwargs: [
            {
                "fact_id": "uf_rule",
                "rule_type": "entity_alias",
                "rule_payload": {
                    "alias_text": "Dana",
                    "target_text": "Dana Lewis",
                },
            }
        ]
    )
    monkeypatch.setitem(sys.modules, "user_facts", facts_module)

    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda query, **_kwargs: [
            {
                "contact_id": "contact:dana-lewis",
                "display_name": "Dana Lewis",
                "aliases": ["Dana"],
                "match_score": 100,
            }
        ]
        if query == "Dana Lewis"
        else [],
    )

    result = resolver.resolve_contact("Dana", "user@example.com", event_context="with Dana")

    assert result["status"] == "resolved"
    assert result["matched_via"] == "hard_rule"
    assert result["contact_id"] == "contact:dana-lewis"


def test_contact_resolution_timeout_override_replaces_inner_timeout(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call_llm_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"people": [], "collective_selectors": []}

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    with resolver.use_contact_resolution_timeout(120):
        resolver._call_contact_resolution_llm_json("prompt", timeout=60, use_fast_model=True)

    assert captured["timeout"] == 120
    assert captured["use_fast_model"] is True
    assert captured["reasoning_effort"] == "none"


def test_extract_people_normalizes_object_items(monkeypatch):
    def fake_call_llm_json(_prompt, **_kwargs):
        return {
            "people": [
                {"name": "Jordan Example", "confidence": 0.95},
                {"value": "user"},
            ]
        }

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    people = resolver.extract_people_from_text("I met Jordan Example")

    assert people == ["Jordan Example", "user"]


def test_people_extraction_prompt_forbids_alternate_properties():
    prompt = prompt_builders.build_people_extraction_prompt(
        text="I met Jordan Example",
        conversation_block="",
        user_facts_block="",
    )

    assert 'The object MUST contain exactly one property: "people".' in prompt
    assert 'Do NOT use alternate property names such as "people_references"' in prompt
    assert 'Do NOT wrap people in objects like {"name": "..."}.' in prompt


def test_collective_selector_extraction_normalizes_direct_list(monkeypatch):
    def fake_call_llm_json(_prompt, **_kwargs):
        return [
            {
                "kind": "company",
                "value": "ExampleCo",
                "raw": "ExampleCo",
                "deterministic": False,
            }
        ]

    monkeypatch.setattr(resolver, "call_llm_json", fake_call_llm_json)

    selectors = resolver._extract_collective_selectors_via_llm(
        text="I met everyone at ExampleCo",
        conversation_block="",
    )

    assert selectors == [
        {
            "kind": "company",
            "value": "ExampleCo",
            "raw": "ExampleCo",
            "deterministic": "false",
        }
    ]


def test_nested_relationship_fallback_uses_top_level_contact_id(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_args, **_kwargs: {
            "relationships": [
                {
                    "contact_id": "contact:family-member",
                    "related_contact": {"display_name": "Family Member"},
                }
            ]
        },
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_via_relationship",
        lambda *_args, **_kwargs: {
            "found": False,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_args, **_kwargs: [
            {"contact_id": "contact:family-member", "display_name": "Family Member"}
        ],
    )

    result = resolver._resolve_nested_relationship(
        ["Dana", "family"],
        "user@example.com",
        resolution_cache={
            "Dana": {
                "status": "resolved",
                "contact_id": "contact:dana-lewis",
                "display_name": "Dana Lewis",
            }
        },
    )

    assert result["found"] is True
    assert result["contact_id"] == "contact:family-member"
    assert result["display_name"] == "Family Member"


def test_infer_relationship_pairs_filters_generic_event_roles(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_call_contact_resolution_llm_json",
        lambda *_args, **_kwargs: {
            "relationships": [
                {
                    "person_text": "user",
                    "anchor_text": "Dana Lewis",
                    "relationship_hint": "guest",
                },
                {
                    "person_text": "Alice",
                    "anchor_text": "Bob",
                    "relationship_hint": "doctor",
                },
            ]
        },
    )

    result = resolver._infer_relationship_pairs(
        ["user", "Dana Lewis", "Alice", "Bob"],
        "Went to Dana's place and Alice is Bob's doctor",
    )

    assert result == [
        {
            "person_text": "Alice",
            "anchor_text": "Bob",
            "relationship_hint": "doctor",
        }
    ]


def test_infer_relationship_types_rejects_generic_event_roles(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_call_contact_resolution_llm_json",
        lambda *_args, **_kwargs: {"type": "attendee", "other_type": "host"},
    )

    result = resolver._infer_relationship_types(
        "user",
        "Dana Lewis",
        "guest",
        "Went to Dana's place",
    )

    assert result is None


def test_normalize_bare_family_mentions_for_user_scopes_mentions():
    people = ["user", "wife", "daughter", "Dana Lewis", "Dana's whole family"]

    normalized = resolver._normalize_bare_family_mentions_for_user(
        "had japanese for lunch at 12h with wife, daughter and Dana's whole family at caidan",
        people,
    )

    assert normalized == [
        "user",
        "my wife",
        "my daughter",
        "Dana Lewis",
        "Dana's whole family",
    ]


def test_resolve_contacts_from_text_resolves_bare_family_mentions_against_user_relationships(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_fast_extract_people_from_text",
        lambda *_args, **_kwargs: ([], [], False),
    )
    monkeypatch.setattr(
        resolver,
        "extract_people_from_text",
        lambda *_args, **_kwargs: ["user", "wife", "daughter"],
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: {"contact_id": "user-1", "display_name": "Alex Carter"},
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_a, **_k: {
            "relationships": [
                {
                    "contact_id": "contact-wife",
                    "related_contact": {
                        "contact_id": "contact-wife",
                        "display_name": "Robin Tess Lake",
                    },
                    "type": "husband",
                    "other_type": "wife",
                },
                {
                    "contact_id": "contact-daughter",
                    "related_contact": {
                        "contact_id": "contact-daughter",
                        "display_name": "Jamie Quinn Lake",
                    },
                    "type": "parent",
                    "other_type": "daughter",
                },
            ]
        },
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_a, **_k: [],
    )

    result = resolver.resolve_contacts_from_text(
        "had japanese for lunch at 12h with wife and daughter",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
    )

    assert result["people_mentioned"] == ["user", "my wife", "my daughter"]
    assert result["new_contacts"] == []
    assert {item["display_name"] for item in result["resolved_contacts"]} == {
        "Alex Carter",
        "Robin Tess Lake",
        "Jamie Quinn Lake",
    }


def test_resolve_contacts_restores_collective_family_mentions_after_participant_filter(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_fast_extract_people_from_text",
        lambda *_args, **_kwargs: ([], [], False),
    )
    monkeypatch.setattr(
        resolver,
        "extract_people_from_text",
        lambda *_args, **_kwargs: (
            ["user", "Morgan Brooks", "Morgan Brooks's whole family"],
            [],
        ),
    )
    monkeypatch.setattr(
        resolver,
        "_filter_event_participants_via_llm",
        lambda **_kwargs: (["user", "Morgan Brooks"], ["Morgan Brooks's whole family"]),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_collective_selectors",
        lambda *_args, **_kwargs: ([], [], []),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda *args, **kwargs: ([], [], [], {}),
    )

    result = resolver.resolve_contacts_from_text(
        "Lots of people were there, and Morgan Brooks's whole family was there as well.",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
        participant_focus=True,
    )

    assert result["people_mentioned"] == [
        "user",
        "Morgan Brooks",
        "Morgan Brooks's whole family",
    ]


def test_infer_professions_for_new_contacts_skips_family_terms_without_llm(monkeypatch):
    calls = []

    def fake_infer(person_text, *_args, **_kwargs):
        calls.append(person_text)
        return "doctor" if person_text == "Dr. Brown" else None

    monkeypatch.setattr(resolver, "_infer_profession_from_text", fake_infer)

    new_contacts = [
        {"original_text": "wife"},
        {"original_text": "daughter"},
        {"original_text": "Dr. Brown"},
    ]

    profession_by_text = resolver._infer_professions_for_new_contacts(
        new_contacts,
        "had lunch with wife, daughter, and Dr. Brown",
    )

    assert calls == ["Dr. Brown"]
    assert profession_by_text == {
        "wife": None,
        "daughter": None,
        "Dr. Brown": "doctor",
    }


def test_suggest_missing_relationships_caches_null_profession_results(monkeypatch):
    profession_calls = []

    monkeypatch.setattr(
        resolver.contacts_service,
        "find_self_contact",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        resolver.contacts_service,
        "get_contact_relationships",
        lambda *_a, **_k: {"found": False, "relationships": []},
    )

    def fake_infer_profession(person_text, *_args, **_kwargs):
        profession_calls.append(person_text)
        return None

    monkeypatch.setattr(resolver, "_infer_profession_from_text", fake_infer_profession)
    monkeypatch.setattr(
        resolver,
        "_infer_relationship_types",
        lambda *_args, **_kwargs: {"type": "doctor", "other_type": "patient"},
    )

    suggestions = resolver._suggest_missing_relationships(
        pairs=[
            {"person_text": "Alice", "anchor_text": "Bob", "relationship_hint": "doctor"},
            {"person_text": "Alice", "anchor_text": "Cara", "relationship_hint": "doctor"},
        ],
        full_text="Alice is Bob's doctor and Cara's doctor",
        user_email="user@example.com",
        resolution_cache={
            "Alice": {"status": "resolved", "contact_id": "contact-1", "display_name": "Alice"},
            "Bob": {"status": "resolved", "contact_id": "contact-2", "display_name": "Bob"},
            "Cara": {"status": "resolved", "contact_id": "contact-3", "display_name": "Cara"},
        },
        profession_by_text={},
    )

    assert len(suggestions) == 2
    assert profession_calls == ["Alice", "Bob", "Cara"]


def test_safe_single_low_score_match_stays_ambiguous(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_args, **_kwargs: [
            {
                "contact_id": "contact:guilherme",
                "display_name": "Guilherme Vergueiro Fanti",
                "match_score": 88,
                "match_reason": "name part match: fanti",
                "aliases": ["Gui"],
            }
        ],
    )

    result = resolver.resolve_contact("Rafael Fanti", "user@example.com")

    assert result["status"] == "candidates"
    assert result["candidates"][0]["display_name"] == "Guilherme Vergueiro Fanti"


def test_alias_plus_surname_match_resolves_uniquely(monkeypatch):
    monkeypatch.setattr(
        resolver.contacts_service,
        "search_contacts",
        lambda *_args, **_kwargs: [
            {
                "contact_id": "contact:bia",
                "display_name": "Beatriz Queiroz Fanti",
                "match_score": 97,
                "match_reason": "alias+name parts match: bia + beatriz queiroz fanti",
                "aliases": ["Bia"],
            },
            {
                "contact_id": "contact:rafael",
                "display_name": "Rafael Queiroz Fanti",
                "match_score": 88,
                "match_reason": "name part match: fanti",
                "aliases": [],
            },
        ],
    )

    result = resolver.resolve_contact("Bia Fanti", "user@example.com")

    assert result["status"] == "resolved"
    assert result["matched_via"] == "alias_plus_name_match"
    assert result["display_name"] == "Beatriz Queiroz Fanti"


def test_participant_focus_with_explicit_presence_bypasses_llm_filter(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_fast_extract_people_from_text",
        lambda *_args, **_kwargs: ([], [], False),
    )
    monkeypatch.setattr(
        resolver,
        "extract_people_from_text",
        lambda *_args, **_kwargs: (["Dana Lewis", "Alex Carter"], []),
    )
    monkeypatch.setattr(
        resolver,
        "_filter_event_participants_via_llm",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM filter should not run")),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_collective_selectors",
        lambda *_args, **_kwargs: ([], [], []),
    )
    monkeypatch.setattr(
        resolver,
        "_resolve_people_mentions",
        lambda people, *_args, **_kwargs: (
            [
                {
                    "original_text": person,
                    "contact_id": f"contact:{idx}",
                    "display_name": person,
                    "matched_via": "direct_match",
                    "confidence": "high",
                    "resolution_path": None,
                }
                for idx, person in enumerate(people, start=1)
            ],
            [],
            [],
            {},
        ),
    )

    result = resolver.resolve_contacts_from_text(
        "Dana Lewis and Alex Carter just arrived and are here.",
        "user@example.com",
        mode=resolver.MINIMAL_RESOLUTION_MODE,
        participant_focus=True,
    )

    assert result["people_mentioned"] == ["Dana Lewis", "Alex Carter"]


def test_collective_zero_candidate_resolution_does_not_become_ambiguous(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_contact",
        lambda person_text, *_args, **_kwargs: {
            "status": "candidates",
            "candidates": [],
        }
        if person_text == "Marcela's family"
        else {
            "status": "resolved",
            "contact_id": "contact:denise",
            "display_name": "Denise Queiroz",
            "matched_via": "direct_match",
            "confidence": "high",
        },
    )

    resolved, new, ambiguous, _cache = resolver._resolve_people_mentions(
        ["Marcela's family", "Denise Queiroz"],
        "user@example.com",
        "Marcela's family just arrived. Denise Queiroz is here.",
    )

    assert new == []
    assert ambiguous == []
    assert [item["display_name"] for item in resolved] == ["Denise Queiroz"]
